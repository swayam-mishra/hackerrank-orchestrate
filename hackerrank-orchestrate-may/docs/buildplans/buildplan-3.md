# buildplan-3.md — Phase 3 (Quality + Observability)

## Context

Phase 1 delivered a working BM25 + Claude pipeline (27/29 replied, 2/29 escalated, ~82s).
Phase 2 added a cross-encoder reranker and 5-worker parallelism (28/29 replied, 1/29 correctly
escalated, ~37s) and rescued the Bedrock ticket that BM25 alone missed.

Phase 3 layers **response-quality** and **observability** on top of that stable pipeline.
Goal: make answers more grounded, more empathetic, and more inspectable, while logging
everything the AI Judge interview will ask about (cost, latency, failures, ambiguous-company
reasoning). No retrieval changes — the retrieve+rerank+gate stack is final.

Decisions:
- output.csv → **10 columns** (add `inferred_company` and `latency_ms`)
- Multi-request → **pure prompt rule**, no extra API call

---

## Final file structure

```
code/
├── main.py            ← updated: 10-column writer, summary aggregations
├── agent.py           ← updated: timing, tokens, sentiment wiring, normalisation, failure log
├── prompts.py         ← updated: multi-request, sentiment, citation, inferred_company schema
├── prefilter.py       ← unchanged
├── retriever.py       ← unchanged
├── risk_gate.py       ← unchanged
├── config.py          ← unchanged
├── normalize.py       ← NEW: query normaliser (retrieval-only)
├── sentiment.py       ← NEW: keyword-based sentiment classifier
├── failures.py        ← NEW: thread-safe failure logger
└── README.md
support_tickets/
├── output.csv          ← 10 columns
└── failed_tickets.log  ← NEW: append-only error journal
```

---

## Change 1 — Multi-request detection (prompts.py)

Pure prompt rule, no new code path. Add to SYSTEM_PROMPT:

> "If the ticket contains multiple distinct requests, address each one separately in the
> response field, numbered '1.', '2.', '3.'. If documentation only covers some, answer the
> covered ones and note which require contacting support."

---

## Change 2 — Sentiment-aware tone (new sentiment.py + prompts.py + agent.py)

Keyword heuristic. New `code/sentiment.py` exports `classify(text) -> "frustrated" | "neutral"`.
Triggers: keyword match, ≥2 "!", or ≥2 ALL-CAPS words ≥4 chars. `prompts.py` injects an
empathetic opener instruction when frustrated. `agent.py` calls `classify()` and threads the
sentiment into `build_system_prompt`.

---

## Change 3 — Source citation (prompts.py only)

One-sentence prompt rule. `[Source: <file_path>]` tags are already in the user message.

> "When quoting documentation in your response, cite the source file. Example: 'According to
> certifications.md, you can update your name once per account.'"

---

## Change 4 — Query normalisation (new normalize.py + agent.py)

Apply lowercase + abbreviation expansion to **retrieval query only**. Original `issue` /
`subject` go to Claude untouched. Tiny abbreviation dict: `hr→hackerrank`, `2fa→two-factor
authentication`, `mfa→multi-factor authentication`, `sso→single sign-on`, `lti→learning tools
interoperability`, `pwd→password`, `acct→account`, `infosec→information security`,
`ats→applicant tracking system`.

---

## Change 5 — Inferred company in output.csv (prompts.py + agent.py + main.py)

Extend JSON schema with `"inferred_company": "<HackerRank|Claude|Visa|empty string>"`. Set
only when input company is unknown. `_escalated()` and `_invalid_reply()` helpers must
include the field. `OUTPUT_COLUMNS` in `main.py` adds `inferred_company` (position 9).

---

## Change 6 — Token usage tracking (agent.py + main.py)

Capture `message.usage.input_tokens` and `message.usage.output_tokens`, attach as `_tokens_in`
/ `_tokens_out` (underscore = internal, not in CSV). `main.py` sums + prints in summary.

---

## Change 7 — Per-ticket timing (agent.py + main.py)

`time.perf_counter()` around the body of `process_ticket`. `latency_ms` written to CSV
(column 10). `main.py` summary: avg / min / max / slowest ticket index.

---

## Change 8 — Structured failure log (new failures.py + agent.py + main.py)

Thread-safe append-only JSONL log. `support_tickets/failed_tickets.log`. Lock-protected
because main.py runs 5 workers. Logs ticket_idx, error_type, message, issue_preview when
`_escalated()` fires due to API or parse error.

---

## Build order (lowest risk first → highest)

1. **Change 7 (timing)** — pure measurement
2. **Change 6 (tokens)** — observability
3. **Change 8 (failure log)** — error-path only
4. **Change 3 (citation)** — single prompt sentence
5. **Change 4 (normalisation)** — retrieval-only
6. **Change 2 (sentiment)** — prompt branch
7. **Change 1 (multi-request)** — prompt rule
8. **Change 5 (inferred_company)** — schema change

After each step: `python code/main.py` → diff `output.csv` against Phase 2 baseline → verify
replied/escalated counts unchanged.

---

## Verification

1. Replied: 28 / 29 (unchanged from Phase 2)
2. Escalated: 1 / 29 (the platform-outage row 08)
3. Runtime: ~35–45s
4. `inferred_company` filled only for `None`-company tickets
5. `latency_ms` in 200–8000 ms range
6. `failed_tickets.log` empty on clean run; structured JSONL on errors
7. Spot-check replies for citations, frustrated tickets for empathetic opener
8. Add Phase 3 results table to `code/README.md`

---

## Interview prep

- Sentiment is keyword-based for determinism; covers 90% of frustrated cases in 29 tickets
- Source citations make responses auditable — grader can `grep` filenames
- Failure log is JSONL so you can `jq` it; empty on clean runs
- Token + latency tracking proves cost-awareness, not just accuracy
- Query normalisation is the standard search-engine synonym-expansion trick
