# IMPLEMENTATION_PLAN.md — Phase 10

Roadmap to working code. **Gated:** do not start until the reviewer says "go" (and ideally resolves the DESIGN_REVIEW §F open questions, esp. B1). Build **against `sample_claims.csv` first**, iterate with the Phase-8 eval, then run the full `claims.csv` to produce `output.csv`.

Terminal deliverables (match the submission requirements exactly):
- **`code.zip`** — full runnable solution + `evaluation/` + `README.md` + `requirements.txt`.
- **`output.csv`** — predictions for all **44** rows of `dataset/claims.csv`, correct 14-column order.
- **`chat_transcript`** — the conversation transcript of how the system was developed/used.

---

## 0. Target repo layout
```
code/
  src/
    config.py            # model id, paths, thresholds, caps (env-driven)
    schema.py            # SINGLE SOURCE OF TRUTH: OutputRow + PerceptionFacts (the seam) + Literals + invariants
    pipeline.py          # per-row orchestration, checkpointing, audit log
    agent.py             # Claude tool-use loop (bounded rounds, forced finalize)
    perception/
      ingest.py          # resolve/decode/resize/dedupe images
      quality_gate.py    # blur/brightness + valid_image + quality flags
    tools/
      inspect_image.py
      lookup_evidence_requirement.py
      check_user_history.py   # (may become code-only overlay; see DESIGN_REVIEW C)
    decision/
      evidence.py        # sufficiency (rule-grounded)
      consistency.py     # object/part validator
      aggregate.py       # multi-image
      tree.py            # decision tree → claim_status (pure, unit-tested)
      severity.py        # invariants over VLM severity
      explain.py         # justification text from facts
    risk/
      history.py         # history_flags map + bounded numeric score (additive overlay)
    io/
      reader.py          # stdlib csv, byte-exact input echo
      writer.py          # schema-ordered, normalized serialization
    main.py              # CLI entry: read claims.csv → output.csv
    cli.py               # debug runner: --case <id> [--verbose] [--from-cache]  (ENGINEERING_CONVENTIONS §8)
  evaluation/
    main.py / run_eval.py   # per-column metrics + confusion + regression diff
    grounding_tests.py      # blank-drop + image-swap
    adversarial/            # tiny behavioral set
    error_log.md
    evaluation_report.md    # metrics (caveated) + operational analysis
  prompts/                  # system prompt, tool descriptions, claim-parse, finalize
  output.csv
  README.md
  requirements.txt
```
Entry points per the project contract: `code/main.py` (terminal), `code/evaluation/main.py` (eval).

---

## 1. Build steps (each: objective · rationale · deps · validation · outcome)

**S1 — Project scaffold + config + secrets**
- *Objective:* repo skeleton, `requirements.txt` (`anthropic`, `pydantic>=2`, `pillow`, `python-dotenv`, `pytest`; optional `rapidocr-onnxruntime` behind a flag), `.env` loading, `config.py`.
- *Rationale:* foundation; secrets from env only.
- *Deps:* none. *Validation:* `python -c "import anthropic, pydantic, PIL"`; key loads from `.env`. *Outcome:* runnable shell.

**S2 — `schema.py` (single source of truth)** ⟵ do this before anything that emits values
- *Objective:* Pydantic v2 `OutputRow` with per-field `Literal[...]` (per-object `object_part`), the HARD invariants (DECISION_ENGINE §8), bool/set/`none` serialization helpers, and a function exporting the `submit_decision` JSON schema **from** the model.
- *Rationale:* prevents enum/column drift everywhere; the contract lives once.
- *Deps:* S1. *Validation:* unit test asserting literals == spec lists (DESIGN_REVIEW A); invariant tests (passing + violating cases). *Outcome:* validated contract + tool schema.

**S3 — I/O (reader/writer)**
- *Objective:* stdlib `csv` reader carrying 4 input fields as opaque strings; writer emitting 14 columns in order, normalized (`true/false`, sorted `;`-join, `none`), UTF-8/`\n`, `QUOTE_ALL`; 44-row assertion.
- *Rationale:* evaluable output; byte-exact echo (FAILURE F2).
- *Deps:* S2. *Validation:* round-trip test (read→write inputs == original bytes incl. `;`,`\|`, Hinglish); header == contract. *Outcome:* safe CSV in/out.

**S4 — Image ingest + quality/authenticity gate**
- *Objective:* resolve `dataset/`+paths, glob folder, decode, resize long edge ≤2576 (context images optionally ≤1568 per DESIGN_REVIEW F4), dedupe (decode-hash), base64; blur/brightness metrics; `valid_image` + quality flags; VLM authenticity hook.
- *Rationale:* prevents crashes/lost detail; sets `valid_image` (FAILURE D1/D4, V6).
- *Deps:* S1. *Validation:* unit tests on corrupt/missing/oversized/duplicate images; resize math; flag thresholds. *Outcome:* normalized images + per-image signals.

**S5 — Prompts + tools**
- *Objective:* system prompt (instruction hierarchy, untrusted-data delimiting, allowed enums for the row's object, abstention guidance); tool defs `inspect_image`, `lookup_evidence_requirement`, `check_user_history`; `submit_decision` (strict, schema from S2). Evidence rulebook loaded as a stable, cacheable table.
- *Rationale:* grounds perception; enables caching; injection-resistant.
- *Deps:* S2, S4. *Validation:* prompt assembles deterministically (no timestamps/UUIDs → cacheable); tool schemas validate. *Outcome:* ready agent context.

**S6 — Agent loop (`agent.py`)**
- *Objective:* Claude tool-use loop, adaptive thinking, `tool_choice=auto`, **hard round cap (≤6)**; prompt caching breakpoint on the stable prefix (system+tools+rulebook); on cap or `end_turn` without finalize → one forced call `tool_choice={"type":"tool","name":"submit_decision"}` (thinking off); SDK retry/backoff; per-call usage capture.
- *Rationale:* targeted perception, bounded cost (ARCHITECTURE §3), guaranteed structured finalize.
- *Deps:* S5. *Validation:* loop terminates; cap honored; forced finalize returns valid schema; `usage.cache_read_input_tokens>0` on row 2+. *Outcome:* perception facts per row.

**S7 — Deterministic post-checks (`decision/`, `risk/`)**
- *Objective:* evidence sufficiency (rule-grounded), object/part consistency, multi-image aggregation, history risk overlay (additive), severity invariants, **decision tree** (DECISION_ENGINE §3), explanation generator.
- *Rationale:* the auditable, deterministic core; honest determinism.
- *Deps:* S2, S6. *Validation:* **all 20 sample rows as decision-tree fixtures pass**; every tree branch covered; "history additive only" test (case_017); invariant tests. *Outcome:* final row fields from facts.

**S8 — Pipeline + checkpoint + audit (`pipeline.py`, `main.py`)**
- *Objective:* per-row pre→loop→post→validate→serialize; resumable per-row JSONL checkpoint (incl. cached perception facts); audit log (request_id, model, tokens, tool calls+results, rationale, branch, override); safe-default row on unrecoverable error; row-level concurrency within rate limits.
- *Rationale:* reliability, resumability, cost control, auditability (FAILURE P-series).
- *Deps:* S3–S7. *Validation:* full sample run produces 20 valid rows; kill-and-resume re-runs only missing rows; forced error → safe-default row, no crash. *Outcome:* end-to-end on sample.

**S9 — Evaluation harness (`evaluation/`)**
- *Objective:* per-column metrics + confusion + regression diff (sample+test); grounding tests (blank-drop, image-swap); adversarial behavioral set; error log; operational accounting.
- *Rationale:* Phase-8 validation; required deliverable (E6/E7).
- *Deps:* S8. *Validation:* metrics computed; grounding tests show outputs change when images removed/swapped (else flag bug); operational numbers captured. *Outcome:* `evaluation_report.md` draft.

**S10 — Iterate on sample (the real work)**
- *Objective:* read every mismatch, root-cause by layer, fix *rules* preferentially over prompt tweaks; re-run; regression-diff; re-run sample eval ≥2× to gauge perception stability.
- *Rationale:* highest-value activity at n=20 (EVAL §3); avoid overfit (DESIGN_REVIEW B2).
- *Deps:* S9. *Validation:* misses understood + addressed without perturbing unrelated rows; grounding tests still pass. *Outcome:* stable, explainable behavior.

**S11 — (optional) Model A/B**
- *Objective:* one-line switch to `claude-sonnet-4-6`; compare per-column metrics + cost on the 20 rows. *Rationale:* cost/latency vs quality (DESIGN_REVIEW B6). *Validation:* documented comparison. *Outcome:* model choice justified empirically.

**S12 — Full test run → `output.csv`**
- *Objective:* run all 44 rows; validate 44 rows × 14 columns, order, byte-exact echo. *Rationale:* the graded artifact. *Validation:* writer assertions pass; spot-read a few rows incl. a 3-image row. *Outcome:* `output.csv`.

**S13 — Finalize report + README + freeze deps + package**
- *Objective:* complete `evaluation_report.md` (metrics caveated + operational analysis); `README.md` (setup, env vars, how to run, determinism boundary, the B1 decision); `pip freeze > requirements.txt`; assemble `code.zip`; ensure `chat_transcript` captured.
- *Rationale:* reproducibility + submission completeness. *Validation:* fresh-venv `pip install -r requirements.txt` + `python code/main.py` reproduces `output.csv`; `code/evaluation/main.py` reproduces metrics. *Outcome:* all three deliverables ready.

**S14 — Submit** on the HackerRank Community Platform (mind the 2026-06-20 11:00 IST deadline).

---

## 2. Operational analysis (to finalize in `evaluation/evaluation_report.md`)

Report (required by spec E7): model calls, input/output tokens, images processed, cost with pricing assumptions, latency/runtime, TPM/RPM + batching/throttling/caching/retry. **Estimates below; measured numbers replace them in the report.**

**Pricing assumptions:** `claude-opus-4-8` = **$5/MTok input, $25/MTok output**; cache **write ×1.25**, **read ×0.1**; image tokens `⌈w/28⌉×⌈h/28⌉` (high-res claimed image ~2.7–4.8k tokens; downsampled context ~1.5k). (A/B: `claude-sonnet-4-6` = $3/$15.)

**Volume:** sample 20 rows / ~20 images; test 44 rows / ~82 images (13×1 + 24×2 + 7×3). Loop ~2–4 calls/row (cap 6) → ~**90–180 calls** test, ~**40–80** sample.

**Rough cost (test, order-of-magnitude):**
- Image input ≈ 82 imgs × ~3k tok = ~246k first-read; cached re-reads across rounds add ~×0.1 → ~**295k** effective image-input tokens.
- Stable prefix (~4–6k tok) cached across ~120 calls → ~**50k** effective.
- Conversation/tool text ≈ ~**50k**.
- ⇒ input ≈ **~0.4M tok × $5/M ≈ ~$2**. Output ≈ ~600 tok/row + tool rounds ≈ ~**60k × $25/M ≈ ~$1.5**. **Test ≈ ~$3–6.** Sample ≈ **~$1–2**. Whole project: a few dollars.

**Latency/runtime:** ~few s/call × 2–4 calls/row ⇒ ~10–30 s/row; 44 rows sequential ≈ ~10–20 min; with row-level concurrency (3–5 in flight) ≈ ~3–6 min.

**TPM/RPM + strategies:**
- **Caching:** stable prefix (system + tools + rulebook ≥4096 tok) cached; verify `cache_read_input_tokens>0`; freeze prefix (no volatile bytes), deterministic tool order. Primary cost lever.
- **Batching:** the **Batch API 50% discount applies to single-shot requests, NOT to a multi-round agent loop** — so it does **not** apply to our per-row loop. (If a row resolves in a single multimodal call with no tools, that specific call *could* batch — not our default path.) We state this explicitly in the report.
- **Throttling/concurrency:** small worker pool (3–5 rows) within tier RPM/TPM; SDK auto-retry (exp backoff) on 429/5xx + bounded manual retry; per-row checkpoint so a stall never loses completed work.
- **Token control:** resize images once; downsample context images to 1568 px (keep claimed region full-res via `inspect_image`); cap tool rounds; reuse decoded bytes.

---

## 3. Definition of done (Stage B exit)
- `output.csv`: 44 rows, 14 columns in exact order, byte-exact input echo, all enum-valid, no crashes.
- `evaluation/`: per-column metrics (caveated n=20) + confusion + regression diff + grounding tests (pass) + operational analysis.
- Unit tests green for every deterministic component (gate, evidence, consistency, tree incl. all 20 sample fixtures, risk, invariants, serializer).
- `code.zip` reproduces `output.csv` and metrics from a clean venv; `README.md` documents the determinism boundary and the B1 decision; `requirements.txt` pinned; `chat_transcript` captured.

> **Gate reminder:** implementation begins only after "go". Preferably resolve DESIGN_REVIEW §F (B1 `valid_image` rule; `check_user_history` tool vs overlay; model choice; context downsample) first — otherwise we proceed with the stated reversible defaults.
