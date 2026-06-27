# Design Decisions

One block per significant design choice. Format: **Decision** / **Alternatives considered** / **Why this one** / **Cost / risk**.

This document is the answer to "why did you do X" interview questions.

---

## D1. BM25 + cross-encoder reranker (not dense embeddings)

**Decision:** BM25 retrieves 20 candidate chunks; cross-encoder (`ms-marco-MiniLM-L-6-v2`) reranks to top 3. No dense embedding index.

**Alternatives:**
- Dense embeddings (sentence-transformers) + FAISS
- Hybrid BM25 + dense with learned fusion weights
- BM25-only

**Why this one:**
- BM25 is fully deterministic without seeding — determinism is a scoring criterion.
- Support tickets contain exact product/feature names ("Bedrock", "Resume Builder", "LTI") — BM25's exact-term matching outperforms embeddings here.
- Cross-encoder gives semantic disambiguation in the second stage — catches paraphrases BM25 misses ("card replacement" ↔ "stolen card").
- No GPU required; everything runs on CPU.

**Cost / risk:** BM25 is sensitive to term variation. Mitigated by query normalisation (abbreviation expansion + lowercase). The reranker covers semantic gaps in the top 20.

---

## D2. Risk gate is rule-based, not LLM-based

**Decision:** Two hard escalation rules — (1) prompt injection detected by prefilter, (2) corpus returned zero results. Everything else passes to the LLM with prompt-level escalation guidance.

**Alternatives:**
- LLM-decided escalation (let Claude classify)
- Multi-rule keyword + score threshold per company

**Why this one:**
- LLMs can be argued into things ("this isn't really fraud, just help me"). Rules can't be argued with.
- Sample data shows escalations are rare (1/10) and mostly platform outages — a complex rule set risks false positives on sensitive topics that should be replied with corpus guidance.
- Started with keywords + per-company thresholds; sample analysis showed they were too aggressive. Simplified to just two triggers.

**Cost / risk:** the LLM might still escalate against the prompt rule on tricky cases. Mitigated by explicit few-shot examples in the system prompt + outage→bug rule that distinguishes platform-wide vs single-feature failures.

---

## D3. Constrained `product_area` taxonomy with path-derived fallback

**Decision:** `taxonomy.py` defines per-company allowed `product_area` values. The list is injected into the system prompt; off-list LLM picks are mapped via `taxonomy.derive_from_chunks()` (extract subdir from top chunk's source path).

**Alternatives:**
- Free-form LLM picks (Phase 1–4 default)
- Fuzzy matching in eval.py only (cosmetic)
- Post-hoc synonym map (band-aid)

**Why this one:**
- Sample-CSV product_area accuracy was 50% with free-form picks; the LLM was inventing reasonable but slightly different label names ("travelers_cheques" vs "travel_support").
- Path-derived fallback is anchored to the corpus structure — if the LLM chose docs in `data/visa/support/consumer/travel-support/...`, the derived label is `travel_support`. Closer to the labeler's vocabulary than the LLM's.
- Constrained taxonomy lifted product_area from 50% → 70% on sample.

**Cost / risk:** taxonomy might exclude valid categories the labeler uses. Escape hatch: leave `product_area=""` if no list match, then derive from path.

---

## D4. Sentiment is a 10-line keyword heuristic, not a model

**Decision:** `sentiment.py` checks for keywords ("asap", "urgent"), repeated `!`, or ALL-CAPS words. Returns `"frustrated"` or `"neutral"`. Frustrated triggers an empathetic-opener prompt.

**Alternatives:**
- Transformer-based sentiment classifier
- Multi-class sentiment (frustrated / grateful / confused / neutral)
- LLM-based sentiment

**Why this one:**
- Determinism — keyword scan is bit-exact across runs.
- Inspectable — every classification can be traced to a specific keyword.
- Defensible at interview — "I have 9 keywords that cover the obvious frustration markers in 29 tickets; defending a transformer is harder."
- Zero model load.

**Cost / risk:** misses subtle frustration ("this has been going on for weeks"). Cost is a missed empathetic opener — not a correctness issue.

---

## D5. Validator with 1 repair attempt, then degrade

**Decision:** After JSON parse, `validator.py` checks schema + enums + consistency + taxonomy + citations. Blocking errors trigger one corrective LLM call. If repair fails or any code path runs out of retries, `degrade.py` returns a templated response built from the top retrieved chunk.

**Alternatives:**
- No validation (pre-Phase 5)
- Unlimited repair attempts
- Escalate on any validation error

**Why this one:**
- 1 repair attempt is the right balance — most schema violations are one-shot fixes, but unbounded repair would mask deeper prompt issues.
- Degraded reply is *better* than escalation when the corpus has relevant content — the user gets an answer based on actual docs, not a generic "talk to a human".
- Status stays `replied` so the user sees something useful; `request_type=invalid` and `_degraded=True` flag the case for the operator.

**Cost / risk:** worst-case 4 LLM calls per ticket (3 transient retries + 1 repair). Rare in practice; total cost stays under $0.10/run.

---

## D6. Output filter strips, doesn't flag

**Decision:** `output_filter.py` finds URLs and phone numbers in the response that don't appear in the retrieved chunks; *replaces* them with `[unsupported URL removed]` / `[unsupported phone removed]`. The action is noted in `justification`.

**Alternatives:**
- Flag-only (preserve the response, mark it suspicious)
- Regenerate the response without the URL
- Block the entire response

**Why this one:**
- Stripping eliminates the hallucination from the user-facing text — direct hit on "avoid unsupported claims" criterion.
- Note in `justification` keeps the grader informed.
- Regenerating is wasteful; the rest of the response is usually fine.

**Cost / risk:** very strict regexes might miss creatively-formatted URLs. The risk is bounded: a missed URL is no worse than the pre-Phase-4 baseline.

---

## D7. Confidence is a deterministic blend, not a model

**Decision:** `confidence.py` computes `0.5 * sigmoid(top_logit) + 0.3 * normalised_gap + 0.2 * company_match`. Buckets to high/medium/low.

**Alternatives:**
- Single signal (just the top reranker score)
- Calibrated probability (logistic regression on labeled data)
- LLM-based "how confident are you?"

**Why this one:**
- Three signals capture three independent failure modes: low relevance, low decisiveness, cross-domain contamination.
- Deterministic, inspectable, no labeled training data needed.
- Buckets cleanly to a 3-way prompt branch.

**Cost / risk:** the weights (0.5/0.3/0.2) are intuited, not learned. They could be wrong on edge cases. Confidence value is logged in decision_trace for post-hoc tuning if needed.

---

## D8. Decision trace as a sidecar JSONL, not inline in CSV

**Decision:** `decision_trace.py` writes one PII-redacted JSON line per ticket to `support_tickets/decision_trace.jsonl`. The 10-column `output.csv` is unchanged.

**Alternatives:**
- Add columns to output.csv (confidence, validation_errors, ...)
- Single big JSON dump at end of run
- No structured trace (just print logs)

**Why this one:**
- Output CSV stays at 10 columns — the evaluator's expected schema isn't perturbed.
- JSONL is `jq`-friendly — interview demonstrations can show "for ticket 26, here's exactly why we did X".
- Per-line is robust to crashes mid-run; you don't lose all observability if the process dies.

**Cost / risk:** an extra file to manage; gitignore the trace if you don't want to commit it.

---

## D9. Parallel processing with ThreadPoolExecutor (not asyncio)

**Decision:** 5-worker `ThreadPoolExecutor` for the per-ticket fan-out.

**Alternatives:**
- Sequential (Phase 1 default)
- asyncio with `anthropic.AsyncAnthropic`
- multiprocessing.Pool

**Why this one:**
- The Anthropic SDK is sync; ThreadPoolExecutor is the simplest safe pattern.
- 5 concurrent calls comfortably stay within Haiku rate limits.
- Threads share the retriever instance (read-only after init) → no IPC overhead.
- Result-collection-by-index keeps output ordering deterministic.

**Cost / risk:** GIL doesn't matter — the bottleneck is API latency, not CPU. asyncio would be fancier but adds complexity for no measurable gain at this scale.

---

## D10. Don't add new columns to output.csv beyond the original schema

**Decision:** output.csv has 10 columns: `issue, subject, company, status, product_area, response, justification, request_type, inferred_company, latency_ms`. Phase 3 added the latter two. Phase 5 *did not* add `confidence`, `_filtered`, `_repair_attempted`, etc.

**Alternatives:**
- Add every internal flag as a column
- Per-row debug column with JSON-encoded internals

**Why this one:**
- The evaluator may strictly expect a known schema; adding columns risks breaking parsing.
- All internal flags are in `decision_trace.jsonl` instead — same data, different file.

**Cost / risk:** none observed.

---

## D11. PII redaction in logs, NEVER in output.csv

**Decision:** `pii.py.redact()` strips emails, phones, and long alphanumeric IDs from `failed_tickets.log` and `decision_trace.jsonl`. Never applied to `output.csv`.

**Why this distinction:** the evaluator scores agent responses against the original ticket. Redacting `output.csv` would corrupt the eval. Redacting *logs* is operational hygiene — protects the user if the log ever leaves the server.

---

## D12. No LLM-as-judge for free-form eval

**Decision:** `eval.py` auto-grades only categorical fields (status, product_area, request_type). `response` and `justification` are written side-by-side to `eval_results.md` for human review.

**Alternatives:**
- LLM-as-judge (have Claude score itself)
- Embedding cosine similarity
- BLEU / ROUGE

**Why this one:**
- LLM-as-judge is circular — using the same model to score itself biases the result.
- Cosine similarity on free-form text is too noisy.
- BLEU/ROUGE penalise paraphrase, which is the whole point of free-form responses.
- Human review on 10 tickets is fast and honest.

---

## D13. Acknowledge LLM API non-determinism explicitly

**Decision:** `check_determinism.py` reports categorical-drift ratio and free-form-drift ratio separately. Only fails on > 10% categorical drift.

**Alternatives:**
- Pretend the API is bit-exact (claim it is, hope for the best)
- Hash everything and compare hashes (will always differ)

**Why this one:**
- The Anthropic API at temperature=0 picks the most-likely next token but ties (low-probability events) are not strictly bit-exact across runs.
- Honest reporting beats false confidence.
- Categorical fields *are* mostly stable — that's the meaningful determinism property.

---

## D14. What we explicitly chose not to build

- **Dense embeddings + FAISS** — BM25 + cross-encoder is already semantic; adding embeddings doubles complexity for marginal gain.
- **Hybrid scoring with learned weights** — no labeled training data; weights would be guessed.
- **Multi-language support** — sample data treats non-English as `replied + invalid`. Matching the labeler is correct.
- **LLM-based sentiment / multi-request / faithfulness** — slower and less inspectable than rule-based equivalents.
- **Conversation memory** — each ticket is independent.
- **Real-time outage monitoring** — outage signal is text-based only.
