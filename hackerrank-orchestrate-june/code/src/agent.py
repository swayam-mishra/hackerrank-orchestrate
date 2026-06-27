"""The Claude tool-use perception loop. Owns the Anthropic SDK; the ONLY place raw
responses live. Emits a typed `PerceptionFacts` (the seam) — nothing downstream sees
an SDK object. `anthropic` is imported lazily so importing this module (and the test
suite) does not require the package.

Loop: adaptive thinking + tool_choice=auto, bounded by cfg.max_tool_rounds; handle
inspect_image; finalize via submit_decision. If the model doesn't finalize, force it
with tool_choice + thinking off. Validate the tool input against SubmitDecision with
up to cfg.max_repair_retries; on persistent failure return abstaining facts.
"""
from __future__ import annotations

from collections import Counter
from typing import Any

from pydantic import ValidationError

from src.config import Config
from src.io.reader import ClaimInput
from src.perception.ingest import LoadedImage
from src.prompts import build_user_content
from src.schema import FAMILY_OF_ISSUE, ImageFact, PerceptionFacts, SubmitDecision, submit_decision_tool_schema
from src.tools.inspect_image import INSPECT_IMAGE_TOOL, handle_inspect_image


def build_tools(cfg: Config) -> list[dict]:
    submit = {
        "name": "submit_decision",
        "description": (
            "Record your structured visual findings (observations only — deterministic code, "
            "not you, decides claim_status). Call exactly once when done inspecting. Two rules "
            "that most affect correctness: (1) set `part_assessable=false` if the CLAIMED part is "
            "not clearly in frame; (2) every id in `supporting_image_ids` must be an image whose "
            "`visual_cue` you filled with a specific, locatable description — an image with no "
            "cue is NOT a supporting image."
        ),
        "input_schema": submit_decision_tool_schema(),
    }
    if cfg.use_strict_tool:
        submit["strict"] = True
    return [INSPECT_IMAGE_TOOL, submit]


def _call_model(client: Any, trace: dict, fallback_model: str | None = None, **kwargs: Any) -> Any:
    """client.messages.create with a 429 counter + optional availability failover. The SDK
    retries 429/5xx internally below this; we count rate-limit escapes, and if a fallback model
    is configured we retry once on it when the primary call fails (real provider failover)."""
    try:
        return client.messages.create(**kwargs)
    except Exception as e:  # noqa: BLE001 — tally, optionally fail over, else re-raise
        if getattr(e, "status_code", None) == 429 or type(e).__name__ == "RateLimitError":
            trace["rate_limit_429s"] = trace.get("rate_limit_429s", 0) + 1
        if fallback_model and kwargs.get("model") != fallback_model:
            trace["fallback_model_used"] = fallback_model
            return client.messages.create(**{**kwargs, "model": fallback_model})
        raise


def _capture_provenance(resp: Any, trace: dict) -> None:
    """Persist the per-call Anthropic request id + the model's free-text rationale, so a
    decision can be reconstructed/audited (and a bad generation traced with the provider)."""
    rid = getattr(resp, "_request_id", None) or getattr(resp, "id", None)
    if rid:
        trace.setdefault("request_ids", []).append(rid)
    texts: list[str] = []
    for b in getattr(resp, "content", None) or []:
        t = getattr(b, "type", None)
        if t == "text":
            texts.append(getattr(b, "text", "") or "")
        elif t == "thinking":
            texts.append(getattr(b, "thinking", "") or "")
    joined = " ".join(x for x in texts if x).strip()
    if joined:
        trace.setdefault("rationale", []).append(joined)


def _accumulate(trace: dict, usage: Any) -> None:
    t = trace["usage"]
    t["input_tokens"] += getattr(usage, "input_tokens", 0) or 0
    t["output_tokens"] += getattr(usage, "output_tokens", 0) or 0
    t["cache_read_input_tokens"] += getattr(usage, "cache_read_input_tokens", 0) or 0
    t["cache_creation_input_tokens"] += getattr(usage, "cache_creation_input_tokens", 0) or 0


def _used_inspect(trace: dict) -> bool:
    """Did the verdict rely on an inspect_image zoom? (provenance for audit + confidence routing)"""
    return any(tc.get("name") == "inspect_image" for tc in trace.get("tool_calls", []))


def _should_reinspect(sd: SubmitDecision, cfg: Config) -> bool:
    """True when the model's first decision is low-confidence AND ungrounded (no supporting
    image carried a specific visual_cue) — force ONE more inspect+resubmit before finalizing,
    instead of accepting a shaky read. Cheap: reuses the already-cached image turn."""
    support = set(sd.supporting_image_ids)
    has_cue = any(o.visual_cue.strip() for o in sd.images if o.image_id in support)
    return sd.vlm_confidence <= cfg.reinspect_conf_max and not has_cue


def _validate(tool_input: dict) -> tuple[SubmitDecision | None, str]:
    try:
        return SubmitDecision.model_validate(tool_input), ""
    except ValidationError as e:
        return None, str(e)


def run_perception(
    claim: ClaimInput,
    loaded: list[LoadedImage],
    cfg: Config,
    client: Any,
    system_prompt: str,
) -> tuple[PerceptionFacts, dict]:
    """Run the perception loop for one claim. Returns (facts, trace)."""
    trace: dict = {
        "model": cfg.model, "rounds": 0, "api_calls": 0, "tool_calls": [], "forced": False,
        "repaired": 0, "error": None, "error_class": None, "stop_reason": None, "rate_limit_429s": 0,
        "request_ids": [], "rationale": [],
        "usage": {"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0},
    }
    usable = [i for i in loaded if i.ok]
    if not usable:
        trace["error"] = "no usable images"
        trace["error_class"] = "no_usable_images"
        return _abstain_facts(claim, loaded, "no usable images"), trace

    abs_by_id = {i.image_id: i.abs_path for i in usable}
    tools = build_tools(cfg)
    system = [{"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}]
    messages: list[dict] = [{"role": "user", "content": build_user_content(claim, loaded)}]

    sd: SubmitDecision | None = None
    reinspected = False
    for _ in range(cfg.max_tool_rounds):
        trace["rounds"] += 1
        resp = _call_model(
            client, trace, fallback_model=cfg.fallback_model,
            model=cfg.model, max_tokens=cfg.max_output_tokens, system=system,
            messages=messages, tools=tools, tool_choice={"type": "auto"},
            thinking={"type": "adaptive"},
        )
        trace["api_calls"] += 1
        _accumulate(trace, resp.usage)
        _capture_provenance(resp, trace)
        trace["stop_reason"] = resp.stop_reason
        messages.append({"role": "assistant", "content": resp.content})
        tool_uses = [b for b in resp.content if getattr(b, "type", None) == "tool_use"]

        submit_block = next((b for b in tool_uses if b.name == "submit_decision"), None)
        if submit_block is not None:
            trace["tool_calls"].append({"name": "submit_decision"})
            sd, err = _validate(dict(submit_block.input))
            if sd is not None:
                # one-shot re-examination: a low-confidence, un-cued first read gets sent back
                # to zoom + reconsider before we accept it (FAILURE: false negatives on small damage).
                if cfg.reinspect_low_confidence and not reinspected and _should_reinspect(sd, cfg):
                    reinspected = True
                    trace["reinspected"] = True
                    messages.append({"role": "user", "content": [{
                        "type": "tool_result", "tool_use_id": submit_block.id,
                        "content": ("Before finalizing: your confidence is low and no supporting image has a "
                                    "specific visual_cue. Use inspect_image to zoom the claimed region, then "
                                    "call submit_decision again with what you can actually localize."),
                    }]})
                    continue
                facts = _facts_from_submit(claim, sd, loaded)
                facts.perception_used_inspect = _used_inspect(trace)
                return facts, trace
            # invalid -> feed the error back and let it retry within the loop
            trace["repaired"] += 1
            messages.append({"role": "user", "content": [{
                "type": "tool_result", "tool_use_id": submit_block.id, "is_error": True,
                "content": f"submit_decision failed validation; fix and resend. Error: {err[:600]}",
            }]})
            continue

        results = []
        for b in tool_uses:
            if b.name == "inspect_image":
                trace["tool_calls"].append({"name": "inspect_image", "input": dict(b.input)})
                results.append({"type": "tool_result", "tool_use_id": b.id,
                                "content": handle_inspect_image(dict(b.input), abs_by_id, cfg)})
        if results:
            messages.append({"role": "user", "content": results})
        elif resp.stop_reason != "tool_use":
            break  # model stopped without finalizing -> force below

    # Force a final submit_decision (thinking off avoids any forced-tool/thinking conflict).
    facts = _force_submit(claim, loaded, cfg, client, system, tools, messages, trace)
    facts.perception_used_inspect = _used_inspect(trace)
    return facts, trace


def _force_submit(claim, loaded, cfg, client, system, tools, messages, trace) -> PerceptionFacts:
    trace["forced"] = True
    if messages and messages[-1]["role"] == "assistant":
        messages.append({"role": "user", "content": "Provide your final decision now by calling submit_decision."})
    last_err = ""
    for _ in range(cfg.max_repair_retries + 1):
        resp = _call_model(
            client, trace, fallback_model=cfg.fallback_model,
            model=cfg.model, max_tokens=cfg.max_output_tokens, system=system,
            messages=messages, tools=tools,
            tool_choice={"type": "tool", "name": "submit_decision"},
        )
        trace["api_calls"] += 1
        _accumulate(trace, resp.usage)
        _capture_provenance(resp, trace)
        messages.append({"role": "assistant", "content": resp.content})
        block = next((b for b in resp.content if getattr(b, "type", None) == "tool_use" and b.name == "submit_decision"), None)
        if block is None:
            last_err = "model did not call submit_decision under forced tool_choice"
            break
        sd, err = _validate(dict(block.input))
        if sd is not None:
            return _facts_from_submit(claim, sd, loaded)
        last_err = err
        trace["repaired"] += 1
        messages.append({"role": "user", "content": [{
            "type": "tool_result", "tool_use_id": block.id, "is_error": True,
            "content": f"Invalid submit_decision; fix and resend. Error: {err[:600]}",
        }]})
    trace["error"] = f"perception finalize failed: {last_err[:300]}"
    trace["error_class"] = "perception_finalize"
    return _abstain_facts(claim, loaded, trace["error"])


def _facts_from_submit(claim: ClaimInput, sd: SubmitDecision, loaded: list[LoadedImage]) -> PerceptionFacts:
    obs = {o.image_id: o for o in sd.images}
    images: list[ImageFact] = []
    for li in loaded:
        o = obs.get(li.image_id)
        if o is not None:
            # Code-gate (blur/light metric) flags count only for relevant images, so noisy
            # metrics on context/overview images don't leak into risk_flags; the VLM's own
            # semantic flags (wrong_angle, cropped_or_obstructed, ...) are always kept.
            code_q = set(li.quality_flags) if o.relevant_to_claim else set()
            images.append(ImageFact(
                image_id=li.image_id, usable=li.ok,
                quality_flags=sorted(code_q | set(o.vlm_quality_flags)),
                authenticity=o.authenticity, relevant_to_claim=o.relevant_to_claim,
                visible_object=o.visible_object, visible_part=o.visible_part,
                visible_issue_type=o.visible_issue_type, visible_severity=o.visible_severity,
                visual_cue=o.visual_cue, image_text=o.image_text,
                reused_in_cases=li.reused_in_cases, duplicate_of=li.duplicate_of,
                manipulation_prior=li.manipulation_prior,
            ))
        else:
            images.append(ImageFact(
                image_id=li.image_id, usable=li.ok, quality_flags=list(li.quality_flags),
                reused_in_cases=li.reused_in_cases, duplicate_of=li.duplicate_of,
                manipulation_prior=li.manipulation_prior,
            ))
    return PerceptionFacts(
        user_id=claim.user_id, claim_object=claim.claim_object,
        claimed_part=sd.claimed_part, claimed_issue_family=sd.claimed_issue_family,
        claimed_severity=sd.claimed_severity,
        claim_text_instruction_present=sd.claim_text_instruction_present, images=images,
        object_matches_claim=sd.object_matches_claim, part_assessable=sd.part_assessable,
        visible_issue_type=sd.aggregate_issue_type, visible_object_part=sd.aggregate_object_part,
        severity_estimate=sd.severity_estimate, vlm_confidence=sd.vlm_confidence,
        contradiction_signals=list(sd.contradiction_signals),
        vlm_supporting_image_ids=list(sd.supporting_image_ids),
    )


def _abstain_facts(claim: ClaimInput, loaded: list[LoadedImage], error: str) -> PerceptionFacts:
    """Safe perception result when the loop cannot produce a valid decision."""
    return PerceptionFacts(
        user_id=claim.user_id, claim_object=claim.claim_object,
        images=[ImageFact(image_id=li.image_id, usable=li.ok, quality_flags=list(li.quality_flags), reused_in_cases=li.reused_in_cases) for li in loaded],
        perception_error=error,
    )


# ───────────────────────────── self-consistency (borderline re-sampling) ─────────────────────────────
# Opus 4.8 perception is non-deterministic (no temperature). The costliest error class is the
# supported<->contradicted boundary; one shaky read decides it. On borderline rows we re-sample
# perception N times, majority-vote the decision-driving fields, and flag cross-read disagreement
# for human review. PURE merge logic (testable without the API) is split from the API orchestration.


def _is_borderline(facts: PerceptionFacts, cfg: Config) -> bool:
    """A read worth re-sampling: any contradiction signal present (the supported/contradicted
    boundary) OR the model's own confidence is at/below the borderline threshold."""
    return bool(facts.contradiction_signals) or facts.vlm_confidence <= cfg.self_consistency_conf_max


def _majority(values: list):
    """Most common value; ties broken by first-seen order (stable, deterministic)."""
    counts = Counter(values)
    top = max(counts.values())
    for v in values:
        if counts[v] == top:
            return v


def merge_perception_reads(reads: list[PerceptionFacts]) -> tuple[PerceptionFacts, bool]:
    """Majority-vote the decision-driving aggregate fields across N perception reads. Returns
    (consensus_facts, disagreement). The consensus borrows the images/cues of the read whose
    issue matches the majority (so the chosen cue stays coherent with the chosen issue).
    `disagreement` is True when the reads did not unanimously agree on a verdict-shaping field."""
    if len(reads) == 1:
        return reads[0], False
    n = len(reads)
    need = n // 2 + 1  # strict majority for a contradiction signal to survive
    issue = _majority([r.visible_issue_type for r in reads])
    sig_counts = Counter(s for r in reads for s in set(r.contradiction_signals))
    signals = [s for s in ("wrong_object", "wrong_object_part", "claim_mismatch") if sig_counts[s] >= need]
    rep = next((r for r in reads if r.visible_issue_type == issue), reads[0])  # coherent images/cues
    # disagreement = reads differ on any verdict-shaping signal (issue collapsed to family so
    # trivial subtype variation, e.g. dent vs scratch, doesn't count as disagreement).
    keys = {
        (r.object_matches_claim, r.part_assessable,
         FAMILY_OF_ISSUE.get(r.visible_issue_type, "unknown"),
         frozenset(r.contradiction_signals))
        for r in reads
    }
    disagreement = len(keys) > 1
    merged = PerceptionFacts(
        user_id=rep.user_id, claim_object=rep.claim_object,
        claimed_part=_majority([r.claimed_part for r in reads]),
        claimed_issue_family=_majority([r.claimed_issue_family for r in reads]),
        claimed_severity=_majority([r.claimed_severity for r in reads]),
        claim_text_instruction_present=_majority([r.claim_text_instruction_present for r in reads]),
        images=rep.images,
        object_matches_claim=_majority([r.object_matches_claim for r in reads]),
        part_assessable=_majority([r.part_assessable for r in reads]),
        visible_issue_type=issue,
        visible_object_part=_majority([r.visible_object_part for r in reads]),
        severity_estimate=_majority([r.severity_estimate for r in reads]),
        vlm_confidence=round(sum(r.vlm_confidence for r in reads) / n, 3),
        contradiction_signals=signals,
        vlm_supporting_image_ids=list(rep.vlm_supporting_image_ids),
        perception_disagreement=disagreement,
        perception_used_inspect=any(r.perception_used_inspect for r in reads),
    )
    return merged, disagreement


def _combine_traces(traces: list[dict]) -> dict:
    """Roll N per-read perception traces into one so cost/latency reporting stays accurate."""
    base = dict(traces[0])
    u = {k: 0 for k in ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")}
    calls = rounds = repaired = rl429 = 0
    tool_calls: list = []
    request_ids: list = []
    rationale: list = []
    err = None
    for t in traces:
        calls += int(t.get("api_calls", 0) or 0)
        rounds += int(t.get("rounds", 0) or 0)
        repaired += int(t.get("repaired", 0) or 0)
        rl429 += int(t.get("rate_limit_429s", 0) or 0)
        tool_calls += t.get("tool_calls", []) or []
        request_ids += t.get("request_ids", []) or []
        rationale += t.get("rationale", []) or []
        err = err or t.get("error")
        for k in u:
            u[k] += int(t.get("usage", {}).get(k, 0) or 0)
    base.update(api_calls=calls, rounds=rounds, repaired=repaired, rate_limit_429s=rl429,
                tool_calls=tool_calls, request_ids=request_ids, rationale=rationale, error=err, usage=u)
    return base


def run_perception_consistent(
    claim: ClaimInput, loaded: list[LoadedImage], cfg: Config, client: Any, system_prompt: str,
) -> tuple[PerceptionFacts, dict]:
    """run_perception, with majority-vote self-consistency on BORDERLINE rows only. A clear,
    confident first read returns immediately (single call); a borderline read is re-sampled to
    cfg.self_consistency_samples and merged. Disagreement -> perception_disagreement (the decision
    layer maps that to manual_review_required)."""
    facts, trace = run_perception(claim, loaded, cfg, client, system_prompt)
    n = cfg.self_consistency_samples
    if n <= 1 or facts.perception_error or not _is_borderline(facts, cfg):
        return facts, trace
    reads, traces = [facts], [trace]
    for _ in range(n - 1):
        f, t = run_perception(claim, loaded, cfg, client, system_prompt)
        reads.append(f)
        traces.append(t)
    merged, disagreement = merge_perception_reads(reads)
    out_trace = _combine_traces(traces)
    out_trace["self_consistency"] = {
        "samples": len(reads), "borderline": True, "disagreement": disagreement,
        "issue_votes": [r.visible_issue_type for r in reads],
        "status_signal_votes": [list(r.contradiction_signals) for r in reads],
    }
    return merged, out_trace
