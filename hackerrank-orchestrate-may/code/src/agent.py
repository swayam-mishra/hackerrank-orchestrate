import json
import re
import time
from pathlib import Path

import anthropic

from src.config import CLAUDE_MODEL, LLM_TEMPERATURE, MAX_TOKENS_RESPONSE
from src.decision.confidence import score as confidence_score
from src.observability.coverage import maybe_log as coverage_maybe_log
from src.observability.decision_trace import trace as decision_trace_log
from src.decision.degrade import degrade as degrade_response
from src.observability.failures import log_failure
from src.validation.faithfulness import score as faithfulness_score
from src.retrieval.multi_request import split_requests
from src.retrieval.normalize import normalize_query
from src.validation.output_filter import find_unsupported, scrub
from src.pii import redact
from src.retrieval.prefilter import prefilter
from src.decision import risk_gate
from src.prompts import build_system_prompt, build_user_message
from src.decision.sentiment import classify as classify_sentiment
from src.validation.validator import validate as validate_result


def _build_handoff(reason: str, issue: str = "", chunks: list = None) -> str:
    parts = ["ESCALATED TO HUMAN AGENT", "", f"Reason: {reason}"]
    if issue:
        parts.append("")
        parts.append(f"Original issue (preview): {issue[:200]}")
    if chunks:
        sources = []
        seen = set()
        for c in chunks:
            base = Path(c.get("source_file", "")).name
            if base and base not in seen:
                sources.append(base)
                seen.add(base)
        if sources:
            parts.append("")
            parts.append("Retrieved documentation (top sources):")
            for s in sources[:5]:
                parts.append(f"  - {s}")
    return "\n".join(parts)


def _escalated(reason: str, t0: float = None, issue: str = "", chunks: list = None) -> dict:
    return {
        "status": "escalated",
        "product_area": "",
        "response": _build_handoff(reason, issue, chunks),
        "justification": reason,
        "request_type": "invalid",
        "inferred_company": "",
        "latency_ms": int((time.perf_counter() - t0) * 1000) if t0 else 0,
    }


def _invalid_reply(reason: str, t0: float = None) -> dict:
    return {
        "status": "replied",
        "product_area": "",
        "response": "I am sorry, this is out of scope from my capabilities.",
        "justification": reason,
        "request_type": "invalid",
        "inferred_company": "",
        "latency_ms": int((time.perf_counter() - t0) * 1000) if t0 else 0,
    }


def process_ticket(
    issue: str,
    subject: str,
    company: str,
    retriever,
    client: anthropic.Anthropic,
    ticket_idx: int = 0,
) -> dict:
    t0 = time.perf_counter()
    combined_text = f"{subject} {issue}".strip() if subject and subject.strip() else issue

    trace_entry = {
        "ticket_idx": ticket_idx,
        "issue_preview": combined_text[:200],
        "company_input": company,
    }

    def _finalize(result: dict) -> dict:
        trace_entry.setdefault("output_filter", {
            "urls_stripped": 0, "phones_stripped": 0
        })
        trace_entry["final"] = {
            "status": result.get("status", ""),
            "product_area": result.get("product_area", ""),
            "request_type": result.get("request_type", ""),
            "latency_ms": result.get("latency_ms", 0),
            "degraded": bool(result.get("_degraded", False)),
        }
        try:
            decision_trace_log(trace_entry)
        except Exception as e:  # never let tracing kill a ticket
            print(f"[agent] trace failed: {type(e).__name__}: {redact(str(e))}")
        return result

    # Step 1: prefilter
    pf = prefilter(combined_text)
    trace_entry["prefilter"] = {
        "reason": pf.get("reason"),
        "shortcircuit": pf.get("should_shortcircuit", False),
    }
    if pf["should_shortcircuit"]:
        if pf["reason"] == "injection_attempt":
            return _finalize(_escalated(
                "Prompt injection attempt detected.", t0, issue=issue
            ))
        return _finalize(_invalid_reply(
            f"Ticket filtered: {pf['reason']}.", t0
        ))

    # Step 2: retrieve + rerank (normalised query for retrieval; original text for LLM)
    norm_company = company if company and company.strip().lower() != "none" else None
    sub_queries = split_requests(combined_text)
    trace_entry["multi_request"] = {"split_count": len(sub_queries)}

    if len(sub_queries) == 1:
        norm_text = normalize_query(combined_text)
        bm25_chunks, _ = retriever.retrieve(norm_text, company=norm_company)
        chunks, top_score, all_rerank_scores = retriever.rerank(norm_text, bm25_chunks)
    else:
        # Retrieve per sub-query, merge by source_file (de-duplicate), then rerank against
        # the original combined text so the cross-encoder weighs each chunk against the
        # whole ticket — not just one sub-query.
        merged = {}
        for sub in sub_queries:
            sub_norm = normalize_query(sub)
            sub_chunks, _ = retriever.retrieve(sub_norm, company=norm_company)
            for c in sub_chunks:
                merged.setdefault(c["source_file"], c)
        merged_chunks = list(merged.values())
        chunks, top_score, all_rerank_scores = retriever.rerank(
            normalize_query(combined_text), merged_chunks
        )

    # Step 2b: quantify retrieval confidence; very-short tickets force low bucket
    conf = confidence_score(all_rerank_scores, chunks, norm_company)
    if pf.get("low_signal") and conf["bucket"] == "high":
        conf = {**conf, "bucket": "medium"}  # demote, very-short tickets shouldn't be high-confidence

    # Coverage-gap logging — flag low-confidence retrievals to a side-car log
    coverage_maybe_log(
        ticket_idx, conf["value"], combined_text,
        [Path(c.get("source_file", "")).name for c in chunks[:3]],
        norm_company,
    )
    rerank_gap = (
        round(all_rerank_scores[0] - all_rerank_scores[1], 3)
        if len(all_rerank_scores) >= 2 else 0.0
    )
    trace_entry["retrieval"] = {
        "rerank_top": round(float(top_score), 3),
        "rerank_gap": rerank_gap,
        "company_match": conf["components"]["match"],
        "confidence": conf["value"],
        "bucket": conf["bucket"],
        "top_sources": [Path(c.get("source_file", "")).name for c in chunks[:3]],
    }

    # Step 3: risk gate
    gate = risk_gate.check(issue, pf, top_score, norm_company)
    trace_entry["risk_gate"] = {
        "escalated": gate.get("should_escalate", False),
        "reason": gate.get("reason"),
    }
    if gate["should_escalate"]:
        return _finalize(_escalated(
            f"Escalated by risk gate: {gate['reason']}.", t0,
            issue=issue, chunks=chunks
        ))

    # Step 4: call Claude (3-attempt retry + 1 repair attempt)
    sentiment = classify_sentiment(combined_text)
    trace_entry["sentiment"] = sentiment
    system = build_system_prompt(
        norm_company or "unknown", sentiment, confidence_bucket=conf["bucket"]
    )
    user_msg = build_user_message(chunks, issue, subject)

    _TRANSIENT_ERRORS = (
        anthropic.RateLimitError,
        anthropic.APIConnectionError,
        anthropic.APIStatusError,
    )

    for attempt in range(3):
        try:
            message = client.messages.create(
                model=CLAUDE_MODEL,
                max_tokens=MAX_TOKENS_RESPONSE,
                temperature=LLM_TEMPERATURE,
                system=system,
                messages=[{"role": "user", "content": user_msg}],
            )
            raw = message.content[0].text.strip()
            if raw.startswith("```"):
                raw = re.sub(r"^```(?:json)?\s*", "", raw)
                raw = re.sub(r"\s*```$", "", raw)
                raw = raw.strip()
            result = json.loads(raw)

            # Validate; one repair attempt if blocking errors
            v = validate_result(result, chunks, norm_company, issue=issue)
            tokens_in_total = message.usage.input_tokens
            tokens_out_total = message.usage.output_tokens
            repair_attempted = False
            if not v["valid"]:
                repair_attempted = True
                try:
                    repair_msg = client.messages.create(
                        model=CLAUDE_MODEL,
                        max_tokens=MAX_TOKENS_RESPONSE,
                        temperature=LLM_TEMPERATURE,
                        system=system + "\n\n" + v["hint"],
                        messages=[
                            {"role": "user", "content": user_msg},
                            {"role": "assistant", "content": json.dumps(result)},
                            {"role": "user", "content": v["hint"]},
                        ],
                    )
                    repair_raw = repair_msg.content[0].text.strip()
                    if repair_raw.startswith("```"):
                        repair_raw = re.sub(r"^```(?:json)?\s*", "", repair_raw)
                        repair_raw = re.sub(r"\s*```$", "", repair_raw)
                        repair_raw = repair_raw.strip()
                    try:
                        repaired_result = json.loads(repair_raw)
                        v2 = validate_result(repaired_result, chunks, norm_company, issue=issue)
                        tokens_in_total += repair_msg.usage.input_tokens
                        tokens_out_total += repair_msg.usage.output_tokens
                        if v2["valid"]:
                            result = repaired_result
                            v = v2
                    except json.JSONDecodeError:
                        pass
                except Exception as e:
                    print(f"[agent] repair call failed: {type(e).__name__}: {redact(str(e))}")

            # Output filter: strip URLs/phones the LLM may have hallucinated
            unsupported = find_unsupported(result.get("response", ""), chunks)
            n_urls = len(unsupported["urls"])
            n_phones = len(unsupported["phones"])
            if n_urls or n_phones:
                result["response"] = scrub(result["response"], unsupported)
                note = f" [Output filter: removed {n_urls} unsupported URL(s), {n_phones} phone(s).]"
                result["justification"] = result.get("justification", "") + note
                result["_filtered"] = True
            trace_entry["output_filter"] = {
                "urls_stripped": n_urls, "phones_stripped": n_phones
            }

            # Faithfulness scoring: check claims against retrieved chunks
            faith = faithfulness_score(result.get("response", ""), chunks)
            result["_faithfulness_ratio"] = faith["ratio"]
            result["_faithfulness_total_claims"] = faith["total_claims"]
            trace_entry["faithfulness"] = {
                "ratio": faith["ratio"],
                "total_claims": faith["total_claims"],
                "unsupported": faith["unsupported"],
            }

            # Mandatory-citation check (informational): replied + corpus had coverage
            # but no in-line "According to <filename>" citation.
            response_lc = result.get("response", "").lower()
            has_citation = "according to" in response_lc
            result["_has_citation"] = has_citation
            trace_entry["citation"] = {"present": has_citation}

            result["latency_ms"] = int((time.perf_counter() - t0) * 1000)
            result["_tokens_in"] = tokens_in_total
            result["_tokens_out"] = tokens_out_total
            result["_validation_errors"] = v["errors"]
            result["_repair_attempted"] = repair_attempted
            result["_repair_succeeded"] = repair_attempted and v["valid"]
            result["_confidence"] = conf["value"]
            result["_confidence_bucket"] = conf["bucket"]
            trace_entry["llm"] = {
                "attempts": attempt + 1,
                "tokens_in": tokens_in_total,
                "tokens_out": tokens_out_total,
                "repaired": result["_repair_succeeded"],
            }
            trace_entry["validation"] = {"errors": v["errors"]}
            return _finalize(result)
        except json.JSONDecodeError as e:
            if attempt == 2:
                log_failure(ticket_idx, "JSONDecodeError", str(e), issue[:120])
                trace_entry["llm"] = {"attempts": 3, "error": "JSONDecodeError"}
                # Fall back to degraded template instead of pure escalation
                latency = int((time.perf_counter() - t0) * 1000)
                return _finalize(degrade_response(
                    "JSON parse failed after retries", chunks, issue,
                    norm_company, latency
                ))
        except _TRANSIENT_ERRORS as e:
            if attempt == 2:
                log_failure(ticket_idx, type(e).__name__, str(e), issue[:120])
                trace_entry["llm"] = {"attempts": 3, "error": type(e).__name__}
                latency = int((time.perf_counter() - t0) * 1000)
                return _finalize(degrade_response(
                    f"API error after retries: {type(e).__name__}",
                    chunks, issue, norm_company, latency
                ))
            time.sleep(2 ** attempt)
        except Exception as e:
            print(f"[agent] API error on ticket: {type(e).__name__}: {redact(str(e))}")
            log_failure(ticket_idx, type(e).__name__, str(e), issue[:120])
            trace_entry["llm"] = {"attempts": attempt + 1, "error": type(e).__name__}
            latency = int((time.perf_counter() - t0) * 1000)
            return _finalize(degrade_response(
                f"API error: {type(e).__name__}",
                chunks, issue, norm_company, latency
            ))

    trace_entry["llm"] = {"attempts": 3, "error": "unexpected_loop_exit"}
    latency = int((time.perf_counter() - t0) * 1000)
    return _finalize(degrade_response(
        "Unexpected loop exit", chunks, issue, norm_company, latency
    ))
