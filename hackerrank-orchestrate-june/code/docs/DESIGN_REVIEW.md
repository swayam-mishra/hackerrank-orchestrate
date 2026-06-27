# DESIGN_REVIEW.md — Phase 9

Skeptical senior-engineer review *before* implementation. The Phase-5 architecture is **not** re-opened; this reviews implementation choices *within* it. Each finding: **severity**, the concern, and the **resolution** (or open question for the reviewer).

---

## A. Cross-document consistency pass (single source of truth)

Confirmed the allowed values, output columns, and column order are **identical** across PROBLEM_ANALYSIS, DATASET_ANALYSIS, THREAT_MODEL, DECISION_ENGINE, SYSTEM_DESIGN and match `problem_statement.md` verbatim:

- **14 columns, exact order:** `user_id, image_paths, user_claim, claim_object, evidence_standard_met, evidence_standard_met_reason, risk_flags, issue_type, object_part, claim_status, claim_status_justification, supporting_image_ids, valid_image, severity`. ✅ consistent everywhere.
- **`claim_status`** (3), **`issue_type`** (12 incl. `glass_shatter`,`missing_part`), **`severity`** (5), **`risk_flags`** (14 incl. `low_light_or_glare`,`wrong_object_part`,`possible_manipulation`), per-object **`object_part`** (car 12 / laptop 10 / package 8). ✅ enumerations match the spec lists verbatim in every doc.
- **Enforcement:** these are defined **once** in `src/schema.py` (Pydantic `Literal[...]`); the `submit_decision` tool schema and the output validator are generated **from** it. No doc or component re-declares enums independently → no drift possible at runtime. **Action item:** add a unit test asserting the schema's literals equal the spec lists (guards against accidental edits).

**Residual consistency risk:** the docs restate enums in prose for readability; if the spec changes, prose could lag the Pydantic source. **Mitigation:** the Pydantic model is authoritative; prose is illustrative; the schema-vs-spec unit test is the real guard.

---

## B. Findings

### B1 — [HIGH, OPEN] The `valid_image` NEI rule conflicts with ground truth
- **Concern:** Phase-0 guidance + the prescribed tree say "encode `claim_status == NEI` when `valid_image == false`." Sample **case_008** is `valid_image=false` **and** `contradicted`. Hard-encoding the prescribed invariant would mislabel it and the whole "non-original image that still clearly contradicts" class.
- **Resolution (adopted, pending sign-off):** gate NEI on **`evidence_standard_met == false`** (holds on all 20 rows, semantically sound); treat `valid_image` as an independent reported axis and a *soft* prior, **not** a hard NEI trigger (DECISION_ENGINE §7).
- **Open question for reviewer:** approve this deviation, or require strict adherence to the prescribed tree (accepting the case_008 miss)? *This is the one item we refuse to decide silently.*

### B2 — [MED] Over-fitting the decision tree to 20 rows
- **Concern:** the tree was "walked against all 20 sample rows → matches every label." That's suspiciously perfect; it risks encoding sample quirks.
- **Resolution:** the tree uses only **semantic** branches (evidence sufficiency, object/part/claim mismatch, issue match, abstention) — not row-specific patterns. The "matches all 20" check is a *sanity* test, not a fit target. Generalization rests on the grounding/adversarial/unit tests, not sample accuracy (EVALUATION_STRATEGY §10). **Watch:** if any future tweak only helps sample rows, reject it.

### B3 — [MED] Perception is the unbounded-variance layer; "matches all 20" can regress run-to-run
- **Concern:** Opus 4.8 perception is non-deterministic; a clean sample pass today may differ tomorrow, and the eval could mislead.
- **Resolution:** cache raw `submit_decision` perception facts per row; the decision tree is re-evaluated against cached facts (deterministic) for logic changes. Report perception variance honestly; never claim end-to-end determinism. Run the sample eval ≥2× to observe perception stability before trusting a number.

### B4 — [MED] Brittleness in claim parsing for rambling/multilingual conversations
- **Concern:** extracting the "final consolidated claim" from hedged convos (case_006/013/018) is error-prone; a wrong target part cascades.
- **Resolution:** parsing is an LLM step with explicit "extract the *final asserted* part+condition; if none/ambiguous, return unknown" → biases to NEI, not a wrong confident answer. Logged for audit. Acceptable failure mode (NEI) is safe.

### B5 — [MED] `claim_mismatch` is doing heavy lifting and is fuzzy
- **Concern:** branch §3.1/§3.5 lean on `claim_mismatch` (nature/severity mismatch), a soft VLM judgment; over/under-firing flips supported↔contradicted (the costliest error class, EVAL §9).
- **Resolution:** require the VLM to state *why* it's a mismatch (claimed vs observed nature/severity) and cite the cue; severity adjectives in the claim never set it (only the image does). Error analysis specifically tracks this class.

### B6 — [LOW/MED] Cost/latency of a multi-round loop × up to 3 images × 44 rows
- **Concern:** each tool round is a live call; the loop can't use Batch's 50% discount; image tokens are high at 2576 px (~2.7–4.8k tokens/image).
- **Resolution:** prompt-cache the stable prefix (verify `cache_read_input_tokens>0`); cap tool rounds (≤6) + forced finalize; resize once and reuse bytes; consider downsampling context (non-claimed-region) images to 1568 px while keeping the claimed region full-res via `inspect_image`; concurrency within rate limits. Full numbers in `evaluation_report.md`. **Note:** if cost is a concern, A/B Sonnet 4.6 ($3/$15) on the 20 labeled rows (DECISION_ENGINE/eval support this) — it accepts `temperature=0`, a minor determinism bonus for perception.

### B7 — [LOW] Optional OCR dependency (`rapidocr-onnxruntime`) adds weight
- **Concern:** ONNX runtime is a non-trivial dependency for a marginal gain over the VLM's own transcription.
- **Resolution:** **default OFF**; VLM transcription is the primary text-screen. Keep OCR behind a config flag, likely unused. (Aligns with "every dependency must justify itself.")

### B8 — [LOW] `inspect_image` named-region mapping is hand-built per object
- **Concern:** mapping `front_bumper`→a crop region is heuristic; a wrong crop wastes a round.
- **Resolution:** support both named regions (coarse: top/bottom/left/right/center quadrants + a few object-specific aliases) **and** model-supplied bbox coordinates; on an invalid region return a center crop + note. Low blast radius (just another look).

### B9 — [LOW] Free-text justification could hallucinate or be ungrounded
- **Concern:** spec wants image-grounded justifications; a chatty model may invent specifics.
- **Resolution:** generate justification from logged facts (cue + branch + rule); if empty/ungrounded, fall back to a deterministic template. Free text isn't auto-graded anyway (EVAL §1).

### B10 — [LOW] History numeric thresholds are still somewhat arbitrary
- **Concern:** "rejection_rate ≥ 0.4", "≥4 in 90 days" are judgment calls.
- **Resolution:** few, bounded, documented, derived from observed field ranges; they only *add* `user_history_risk`/`manual_review_required` and **never** change `claim_status`. Primary signal is the explicit `history_flags` token; numerics are corroboration. Low risk by construction.

### B11 — [LOW] Byte-exact echo of input columns is easy to get wrong
- **Concern:** pandas/`csv` can subtly alter quoting, line endings, or Unicode of `user_claim`/`image_paths`.
- **Resolution:** read with the stdlib `csv` module, carry the 4 input fields as opaque strings, write with `QUOTE_ALL`, UTF-8, `\n`. Round-trip unit test on the inputs. (FAILURE F2.)

### B12 — [LOW] Determinism over-claim in messaging
- **Concern:** calling the system "deterministic" oversells it.
- **Resolution:** docs/report consistently say "deterministic **given** perception facts." README states the boundary plainly.

---

## C. Complexity audit — is anything unnecessary?

| Component | Keep? | Justification (concrete failure prevented) |
|---|---|---|
| Agentic loop + tools | ✅ | targeted re-examination (V1/V6), grounded sufficiency (EV*), injection resistance |
| `inspect_image` (crop) | ✅ | false negatives on small damage (V1/V6) |
| `lookup_evidence_requirement` | ✅ | grounds sufficiency in the rulebook (EV3) |
| `check_user_history` (tool) | ⚠️→ **fold** | history is a deterministic join; exposing it as a *tool* is optional. **Decision:** compute history overlay in **code** (post-check) and pass a summary into the prompt; keep a thin `check_user_history` tool only if the model benefits from pulling it on demand. Removing the tool reduces rounds/cost. *Reviewer may weigh in.* |
| Quality gate (blur metric) | ✅ | cheap prior for `blurry_image`; complements VLM |
| OCR dependency | ✅ but OFF | optional fallback only (B7) |
| Object detector | ❌ already cut | heavy deps, VLM+crop suffices |
| Translation layer | ❌ already cut | Claude is multilingual |
| Severity geometry | ❌ already cut | soft VLM severity only |

Net: the only trim worth considering is **demoting `check_user_history` from a tool to a code-only overlay** (cost win, no capability loss). Flagged, not unilaterally changed.

---

## D. Security review (implementation-level)
- Secrets: `ANTHROPIC_API_KEY` from env only; `.env` git-ignored; never logged (log redaction on by default). ✅
- Untrusted data: claim + image text delimited, screened, never authoritative; decision in code (THREAT_MODEL). ✅
- Path handling: image paths resolved under `dataset/`; reject path-escape; this is a local synthetic dataset so no PII scrubbing/user_id hashing (explicitly out of scope — would also break the history join). ✅
- No auto-reject on authenticity (avoids ELA false-positive harm); `possible_manipulation` is review-only. ✅

## E. Operational review
- Resumable per-row checkpoint (re-run only missing rows after a crash). ✅
- Bounded rounds + retry/backoff + forced finalize (no runaway). ✅
- Cost ceiling estimated and reported; cache-hit verified. ✅
- One valid row per input row guaranteed (safe-default). ✅

## F. Open questions for the reviewer (decide before Stage B)
1. **B1 (HIGH):** approve gating NEI on `evidence_standard_met==false` (deviation from the prescribed `valid_image==false` rule), or require strict adherence?
2. **C/`check_user_history`:** keep it as a model-callable tool, or fold history into a code-only overlay (cheaper)? Default proposal: code-only overlay + summary in prompt.
3. **Model choice:** default `claude-opus-4-8`, with a one-line A/B to `claude-sonnet-4-6` decided empirically on the 20 rows. Any preference / cost ceiling to honor?
4. **Cost vs fidelity:** OK to downsample non-claimed-region/context images to 1568 px (keeping the claimed region full-res via `inspect_image`) to cut image tokens?

Absent direction, we proceed with the defaults above (B1 adopted, history folded to code-only overlay, Opus 4.8 with Sonnet A/B, context-image downsample on) — all reversible.
