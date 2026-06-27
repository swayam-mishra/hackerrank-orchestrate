# SYSTEM_DESIGN.md — Phase 6

Component design. Each is labeled **[CODE]** (deterministic) or **[LLM]** (VLM/Claude). For each: purpose, inputs, outputs, confidence signals, failure modes (ref FAILURE_MODES), fallback. Every component must answer: **(1)** what failure mode it prevents, **(2)** why it's necessary, **(3)** what happens if removed. *A component that can't answer (1) concretely is cut.* Nothing below was cut because all earn their place; two candidates considered-and-folded are noted at the end.

Suggested layout: `src/{pipeline.py, agent.py, tools/, perception/, decision/, risk/, schema.py, config.py}`, `evaluation/{run_eval.py, evaluation_report.md}`, `prompts/`, `output.csv`, `README.md`, `requirements.txt`.

---

## 1. Image Ingestion / Normalization — [CODE]
- **Purpose:** resolve `image_paths` (`;`-split, prefix `dataset/`), glob the case folder, decode (Pillow), resize so long edge ≤ 2576 px, re-encode (JPEG/PNG), base64 for the API; map filenames→image_ids (`img_1`).
- **Inputs:** `image_paths`, filesystem. **Outputs:** list of `{image_id, bytes, w, h, ok}`.
- **Confidence signals:** decode success; dimension after resize; file present.
- **Failure modes:** D1/D2/D4 (corrupt/missing/oversized), M3 (dupes via decode-hash).
- **Fallback:** mark image `ok=false` → feeds Quality Gate; never crash.
- **(1)** Prevents crashes + token waste + lost detail from server-side downscale. **(2)** Everything downstream needs decoded, correctly-sized images and stable IDs. **(3)** Without it: crashes on bad files, silent server downsizing that breaks crop coordinates, wrong/missing `supporting_image_ids`.

## 2. Claim Understanding — [LLM] (single structured parse, or folded into the loop's first turn)
- **Purpose:** extract the *final consolidated* claim: target `object_part`, claimed `issue_type`/condition, claimed severity words (noted, not trusted). Handles Hinglish/rambling.
- **Inputs:** `user_claim` (delimited untrusted), `claim_object`. **Outputs:** `{claimed_part, claimed_condition, claimed_issue_family, notes}`.
- **Confidence signals:** whether a single clear target part was found.
- **Failure modes:** C1–C4 (wrong/ambiguous target, severity over-read, injection).
- **Fallback:** ambiguous → mark `claimed_part=unknown` → biases toward NEI.
- **(1)** Prevents evaluating the wrong part / being steered by claim text. **(2)** The conversation defines *what to check*; without the target, perception is unfocused. **(3)** Without it: model may inspect the wrong region, or obey claim instructions.

## 3. Quality & Authenticity Gate — [CODE] for quality signals + [LLM] for authenticity judgment
- **Purpose:** per-image usability + authenticity → sets `valid_image` and quality `risk_flags` (`blurry_image`, `low_light_or_glare`, `cropped_or_obstructed`) and authenticity flags (`non_original_image`, `possible_manipulation`).
- **Inputs:** decoded images; VLM authenticity read. **Outputs:** per-image `{usable, quality_flags, authenticity_flags}`, aggregated `valid_image`.
- **Confidence signals:** blur metric (variance-of-Laplacian as a cheap prior), brightness; VLM "original phone photo vs screenshot/stock?".
- **Failure modes:** D1, E1/E2/E3, O-series.
- **Fallback:** undecodable → `valid_image=false`. Authenticity is a **flag**, never an auto-reject (no ELA).
- **(1)** Prevents acting on unusable/forged images blindly. **(2)** Spec demands `valid_image` + authenticity/quality flags. **(3)** Without it: blurry/forged images silently scored as genuine; `valid_image` unset.
- **Note:** `valid_image` is **independent** of `evidence_standard_met` (case_008). The gate sets `valid_image`; it does **not** by itself force NEI.

## 4. Prompt-Injection / Untrusted-Text Handling — [CODE] wrapping + [LLM] transcription
- **Purpose:** delimit `user_claim` and image-derived text as untrusted; transcribe image text as data; screen for instruction-like phrases → `text_instruction_present`.
- **Inputs:** `user_claim`, VLM image-text transcription (optional `rapidocr-onnxruntime`). **Outputs:** `text_instruction_present` flag; sanitized context.
- **Confidence signals:** phrase-pattern matches.
- **Failure modes:** A1–A4, C5, O1–O3.
- **Fallback:** even if a phrase is missed, the deterministic decision can't be steered.
- **(1)** Prevents image/claim instructions from steering the verdict. **(2)** Spec requires `text_instruction_present`; THREAT_MODEL core control. **(3)** Without it: injected "approve this claim" could bias perception and the flag never fires (case_020).

## 5. Image Understanding (VLM perception loop) — [LLM]
- **Purpose:** look at the right region of the right image; report visible `issue_type`, affected `object_part`, severity, specific cues, per-image relevance. Drives tools `inspect_image`, `lookup_evidence_requirement`, `check_user_history`.
- **Inputs:** images, parsed claim, allowed enums for this `claim_object`, evidence rules. **Outputs (as `submit_decision` args):** perception facts → `issue_type, object_part, severity, per-image relevance, cues, evidence assessment`.
- **Confidence signals:** model's own stated certainty; whether it located a nameable cue; agreement across images.
- **Failure modes:** V1–V8.
- **Fallback:** low confidence → `issue_type=unknown` (abstain) → NEI.
- **(1)** Prevents the whole point of failing — actually seeing damage. **(2)** Images are the primary source of truth; only a VLM can read arbitrary damage. **(3)** Without it: no perception → system can't function.

## 6. `inspect_image` tool — [CODE] (deterministic crop/zoom)
- **Purpose:** return a zoomed crop of the **original full-res** image by named region or coordinates so the model re-examines sub-threshold detail.
- **Inputs:** `image_id`, `focus_area` (named region or bbox). **Outputs:** cropped image block.
- **Failure modes:** V1/V6 (missed detail).
- **Fallback:** invalid region → return center crop + note.
- **(1)** Prevents false negatives on small damage. **(2)** Recovers detail lost to whole-image downscale, no detector needed. **(3)** Without it: hairline cracks/small scratches missed → wrong contradictions.

## 7. Evidence Validation (sufficiency vs rulebook) — [CODE] decision, grounded by [LLM] facts
- **Purpose:** decide `evidence_standard_met` from: is the claimed part visible & assessable for the claimed condition, per the matched `evidence_requirements` rule, aggregated across images.
- **Inputs:** parsed claim, per-image relevance/visibility (from §5), matched rule(s) (from issue→family→rule table). **Outputs:** `evidence_standard_met` (+ reason), candidate `supporting_image_ids`.
- **Confidence signals:** at least one relevant image clearly shows the claimed region.
- **Failure modes:** EV1–EV4.
- **Fallback:** can't confirm the claimed region is assessable → `evidence_standard_met=false`.
- **(1)** Prevents "sufficient" when the claimed part isn't shown (case_006) and "insufficient" when a clear image exists (case_007). **(2)** Spec field + the **hard NEI gate**. **(3)** Without it: NEI/sufficiency decided by vibes, not the rulebook.

## 8. Object/Part Consistency Validator — [CODE]
- **Purpose:** check claimed/visible object matches `claim_object`; claimed `object_part` ∈ the object's enum; visible part vs claimed part → sets `wrong_object` / `wrong_object_part`.
- **Inputs:** `claim_object`, parsed claimed part, VLM-identified object & visible part. **Outputs:** consistency flags; normalized `object_part`.
- **Failure modes:** V3, B1/B2.
- **Fallback:** indeterminate → `object_part=unknown`.
- **(1)** Prevents wrong-object/wrong-part claims slipping through (case_019). **(2)** Enforces per-object enum; key contradiction signal. **(3)** Without it: a different-object photo could be scored as supported.

## 9. Multi-Image Aggregation — [CODE]
- **Purpose:** combine per-image perception into one decision; choose `supporting_image_ids`; dedupe; one-clear-image-suffices logic.
- **Inputs:** per-image facts. **Outputs:** aggregate `evidence_standard_met`, `supporting_image_ids`, conflict signal.
- **Failure modes:** M1–M3 (incl. untested 3-image rows).
- **Fallback:** conflict → prefer clearest relevant image; genuine conflict on claimed part → `claim_mismatch`/NEI.
- **(1)** Prevents a blurry image sinking a claim that a second image supports (case_007). **(2)** Test set has 1–3 images/row. **(3)** Without it: wrong `supporting_image_ids`, mishandled 3-image rows.

## 10. Risk Assessment (user history + image risk overlay) — [CODE]
- **Purpose:** compute the additive risk overlay → `user_history_risk`, `manual_review_required` (from `history_flags` + bounded numeric score), plus carry image/claim risk flags.
- **Inputs:** `user_history.csv` row, image/claim flags from §3/§4/§8. **Outputs:** final `risk_flags` set.
- **Confidence signals:** explicit `history_flags` tokens; numeric thresholds (DATASET_ANALYSIS §7).
- **Failure modes:** R1–R4, D2.
- **Fallback:** missing user → no history flags.
- **(1)** Prevents missing risk context **and** prevents history from overriding evidence. **(2)** Spec: history adds risk via `risk_flags`/justifications. **(3)** Without it: no `user_history_risk`/`manual_review_required`, or (worse) history wrongly flipping decisions.
- **Invariant:** this layer is **additive only** — it never sets/changes `claim_status` (case_017).

## 11. Decision Engine — [CODE]  (full spec → DECISION_ENGINE.md)
- **Purpose:** deterministic tree → `claim_status` from evidence-sufficiency + contradiction signals + issue match; enforce cross-field invariants.
- **Inputs:** all post-check facts. **Outputs:** `claim_status` + which invariant/branch fired.
- **Failure modes:** D1–D5.
- **Fallback:** uncertainty → NEI.
- **(1)** Prevents impossible states, supported-bias, history override, non-explainability. **(2)** The auditable, deterministic core. **(3)** Without it: non-deterministic, unexplainable verdicts.

## 12. Severity Engine — [LLM] estimate, [CODE] invariants
- **Purpose:** `severity ∈ {none,low,medium,high,unknown}`, VLM-estimated with abstention; code enforces NEI⇒`unknown`, `issue_type=none`⇒`severity=none`.
- **Failure modes:** V5.
- **Fallback:** ambiguous → `unknown`.
- **(1)** Prevents fabricated precision / illegal severity states. **(2)** Required field. **(3)** Without it: missing/inconsistent severity. **No** area-ratio geometry — soft signal by design.

## 13. Explanation Generator — [LLM] text from [CODE] facts
- **Purpose:** produce `evidence_standard_met_reason` and `claim_status_justification` — concise, image-grounded, may cite image IDs — derived from logged observations + the applied rule/branch.
- **Failure modes:** D5, hallucinated justification.
- **Fallback:** template from the decision facts if the model's text is empty/ungrounded.
- **(1)** Prevents unexplainable rows (spec E5). **(2)** Two required free-text fields. **(3)** Without it: empty/invented justifications.

## 14. Output Validation & Serialization — [CODE]
- **Purpose:** validate every row against the Pydantic source; normalize bool→`true/false`, set→sorted `;`-join, empty→`none`; write 14 columns in exact order; echo 4 inputs byte-for-byte; assert 44 rows.
- **Failure modes:** F1–F5.
- **Fallback:** validation failure → 1–2 repair retries → safe-default row.
- **(1)** Prevents enum/format/column/row-count drift. **(2)** Makes the submission evaluable (E1). **(3)** Without it: malformed `output.csv` → unscoreable.

## 15. Pipeline Orchestrator — [CODE]
- **Purpose:** per-row drive pre→loop→post; bounded tool rounds + forced finalize; retry/backoff; per-row checkpoint (resumable JSONL); audit log (request_id, model, tokens, tool calls+results, rationale, override logic).
- **Failure modes:** P1–P6.
- **Fallback:** unrecoverable row → safe-default row.
- **(1)** Prevents partial runs, runaway loops, cost blow-ups, lost audit trail. **(2)** Ties components together with reliability + auditability. **(3)** Without it: no resumability, no audit, no cost control.

## 16. Evaluation Pipeline — [CODE] (full spec → EVALUATION_STRATEGY.md)
- **Purpose:** score predictions vs `sample_claims.csv` (per-column), regression-diff across all rows, blank-drop / image-swap grounding tests, operational accounting.
- **(1)** Prevents shipping blind / overfitting. **(2)** Required `evaluation/` deliverable (E6/E7). **(3)** Without it: no evidence the system works or generalizes.

---

## Components considered and folded (not separate modules)
- **"Translation layer"** for Hinglish → **cut**; Claude handles multilingual natively (would only add failure surface).
- **Standalone OCR service** → **folded** into §4 as an *optional* fallback; default is the VLM's own transcription (no required dependency).
- **Object detector for `inspect_image`** → **cut** (ARCHITECTURE_REVIEW): VLM + deterministic crop suffices; detector adds heavy deps.

## Data flow (one row)
ingest+resize → quality/authenticity gate (`valid_image`, quality flags) → claim parse → perception loop (tools: inspect_image / lookup_evidence_requirement / check_user_history) → submit_decision(perception facts) → [post: evidence-sufficiency → object/part consistency → multi-image aggregation → risk overlay → decision tree → severity invariants → explanation] → Pydantic validate → serialize row.

Determinism boundary: everything after `submit_decision` is deterministic code over logged facts.
