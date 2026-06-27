# PROBLEM_ANALYSIS.md — Phase 1

Multi-Modal Evidence Review (HackerRank Orchestrate, June 2026). This document is the problem-understanding baseline for the whole build. It also pins **Phase 0 (inputs + environment)** and extracts the **immutable output contract** that every later document and the code must obey verbatim.

---

## 0. Phase 0 — Input verification & environment (DONE)

All required inputs exist (verified with `ls`):

| Path | Status |
|---|---|
| `problem_statement.md` (real name; not `ps.md`) | OK |
| `dataset/sample_claims.csv` | OK — 20 labeled rows |
| `dataset/claims.csv` | OK — 44 input-only rows |
| `dataset/user_history.csv` | OK — 47 users (user_001…user_047) |
| `dataset/evidence_requirements.csv` | OK — 11 rules |
| `dataset/images/sample/` | OK — 20 case folders |
| `dataset/images/test/` | OK — 44 case folders |
| `code/` and `code/docs/` | OK |

**Environment (fixed for the whole build):**

- **Language:** Python 3.11+. Deps in `requirements.txt`, pinned via `pip freeze` before submission.
- **LLM SDK:** `anthropic` (official Python SDK). Model id in config.
  - Default **`claude-opus-4-8`** — input **$5.00**/MTok, output **$25.00**/MTok, 1M context, 128K max output. **Rejects `temperature`/`top_p`/`top_k` AND `budget_tokens` with HTTP 400**; adaptive thinking only (`thinking={"type":"adaptive"}`).
  - A/B alternative **`claude-sonnet-4-6`** — **$3.00**/$15.00 per MTok, 1M context, 64K max output. (Verified against the live model catalog + vision docs, June 2026.)
- **Auth:** `ANTHROPIC_API_KEY` from env only (`.env` + `python-dotenv` locally). Never hardcoded, never logged.
- **Determinism (honest scope):** the VLM perception layer is **non-deterministic** and cannot be pinned with temperature on Opus 4.8. Determinism comes only from the **deterministic code layers** (quality gate, evidence-sufficiency check, object/part consistency, risk scoring, decision tree, output validation). The final `claim_status` is *deterministic given the logged perception facts*, **not** end-to-end deterministic. We do **not** hard-code visual perception rules.
- **Job shape:** batch — read `claims.csv` → write `output.csv` (exact columns, exact order). **Not** a web service.

**Image facts (verified, current — not stale):** Opus 4.8 native image resolution is **2576 px on the long edge / 4784 visual tokens max**; cost is `⌈w/28⌉ × ⌈h/28⌉` visual tokens. Resize **client-side** to the model's long edge; do not rely on silent server downsizing (it also breaks any coordinate fidelity for crops). Per-request limits: **600 images/request** (1M-context model), **10 MB/image** (base64), **32 MB/request**; formats JPEG/PNG/GIF/WebP.

---

## 1. THE IMMUTABLE CONTRACT (extracted verbatim from `problem_statement.md`)

This is the single source of truth. It is encoded **once** as a Pydantic v2 model (`src/schema.py`); the `submit_decision` tool schema and all output validation derive from it. **Never** rename a column, reorder columns, or invent/rename an enum value. If a real situation seems to need a value outside these lists → **STOP and ask**.

### 1.1 Output columns — 14, in this exact order

```
user_id, image_paths, user_claim, claim_object,            # 4 inputs, echoed unchanged
evidence_standard_met, evidence_standard_met_reason,        # predictions begin
risk_flags, issue_type, object_part,
claim_status, claim_status_justification,
supporting_image_ids, valid_image, severity
```

### 1.2 Allowed values (verbatim)

- **`claim_status`** (3): `supported`, `contradicted`, `not_enough_information`
- **`issue_type`** (12): `dent`, `scratch`, `crack`, `glass_shatter`, `broken_part`, `missing_part`, `torn_packaging`, `crushed_packaging`, `water_damage`, `stain`, `none`, `unknown`
- **`object_part` — car** (12): `front_bumper`, `rear_bumper`, `door`, `hood`, `windshield`, `side_mirror`, `headlight`, `taillight`, `fender`, `quarter_panel`, `body`, `unknown`
- **`object_part` — laptop** (10): `screen`, `keyboard`, `trackpad`, `hinge`, `lid`, `corner`, `port`, `base`, `body`, `unknown`
- **`object_part` — package** (8): `box`, `package_corner`, `package_side`, `seal`, `label`, `contents`, `item`, `unknown`
- **`risk_flags`** (14; `;`-separated set, or `none`): `none`, `blurry_image`, `cropped_or_obstructed`, `low_light_or_glare`, `wrong_angle`, `wrong_object`, `wrong_object_part`, `damage_not_visible`, `claim_mismatch`, `possible_manipulation`, `non_original_image`, `text_instruction_present`, `user_history_risk`, `manual_review_required`
- **`severity`** (5): `none`, `low`, `medium`, `high`, `unknown`
- **`evidence_standard_met`**, **`valid_image`**: boolean (`true`/`false`, lowercase strings in CSV)
- **`supporting_image_ids`**: `;`-separated image IDs (`img_1`, `img_2`, …) or `none`
- **`evidence_standard_met_reason`**, **`claim_status_justification`**: free text, image-grounded

> Note: `glass_shatter` and `missing_part` (issue_type) and several object_part values (`taillight`, `quarter_panel`, `lid`, `port`, `base`, `box`, `label`, `item`, `low_light_or_glare`, `wrong_object_part`, `possible_manipulation`) **do not appear in the 20 sample rows but are in the spec**. They are valid and likely present in the hidden test set. The model must be able to emit them; the eval set must not be assumed to cover them.

---

## 2. Core task requirements

For each claim row, fuse **(a)** the claim conversation, **(b)** one or more images, **(c)** user history, against **(d)** the evidence rulebook, and emit the 10 prediction fields. *Images are the primary source of truth*; the conversation defines *what to check*; history adds *risk context only* and must never override clear visual evidence.

**Why it matters / implication:** This is not single-label classification — it is *grounded multi-field extraction with a decision*. The system must (1) read the claim to know the target part + claimed condition, (2) actually look at the right region of the right image, (3) compare visible reality to the claim, (4) judge evidence sufficiency against a per-object rulebook, (5) overlay risk. Each is a separable component with its own failure mode (→ SYSTEM_DESIGN).

---

## 3. Explicit requirements (stated in the spec)

| # | Requirement | Implication |
|---|---|---|
| E1 | Read `dataset/claims.csv`, produce `output.csv` for all 44 rows, exact columns/order | Output validator enforces 14-column order; rows must equal input rows (no drops). |
| E2 | Use the 4 input fields + history + evidence rulebook | All four data sources are joined per row. |
| E3 | Emit all 10 prediction fields from the allowed lists | Pydantic `Literal[...]` per field; per-object `object_part` enum. |
| E4 | `supporting_image_ids` = images backing the decision; `none` if none sufficient | Decision and image-citation are linked (→ DECISION_ENGINE). |
| E5 | Justifications must be image-grounded; mention image IDs when helpful | Justification text generated from logged VLM observations, not invented. |
| E6 | Include an `evaluation/` folder scoring on `sample_claims.csv` | Eval harness compares predictions vs labels per column. |
| E7 | Operational analysis in `evaluation/evaluation_report.md` (calls, tokens, cost, latency, TPM/RPM, caching/batching/retry) | Token+cost accounting instrumented from day one. |
| E8 | Secrets from env vars only | `.env`, never committed/logged. |

---

## 4. Implicit requirements (not stated, but necessary)

| # | Implicit requirement | Why / implication |
|---|---|---|
| I1 | **Echo the 4 input columns byte-for-byte** (incl. the embedded `;` and `\|` in `image_paths`/`user_claim`) | The grader almost certainly joins on these. CSV quoting must round-trip exactly → use `csv` module with `QUOTE_ALL` or pandas with care; never reformat the conversation. |
| I2 | **`claim_object` is given** — do not re-infer it | It selects which `object_part` enum and which evidence rules apply. Trust it as input. |
| I3 | **Image paths are repo-relative** (`images/test/...`) but live under `dataset/` | Resolve `dataset/` + path. Must handle the `sample/` vs `test/` split. |
| I4 | **`image_id` = filename without extension** (`img_1`) | `supporting_image_ids` uses these, not full paths. |
| I5 | **Multilingual claims** (Hindi/Hinglish present in sample) | The claim parser must handle non-English; VLM/Claude handles this natively — do not add a translation layer. |
| I6 | **Multi-image aggregation**: one blurry + one clear image → still evaluable | Per-image quality, aggregate decision (sample case_007). Quality gate is per-image; sufficiency is per-claim. |
| I7 | **Every row must produce a valid row even on error** | A corrupt/missing image must degrade to a safe default (NEI + flags), never crash the batch. |
| I8 | **Determinism of the code layer** must be real | Same perception facts ⇒ same decision. Decision tree is pure code, unit-tested. |
| I9 | **Cost/latency awareness** is graded (E7) | Prompt caching on the stable prefix; bounded tool-loop; no redundant calls. |

---

## 5. Ambiguous requirements (resolve explicitly, flag the risky ones)

| # | Ambiguity | Resolution (default) | Confidence |
|---|---|---|---|
| A1 | Difference between `valid_image` and `evidence_standard_met` | `valid_image` = the image set is *usable/authentic* for automated review (not blurry-beyond-use, not corrupt, not a suspected non-original). `evidence_standard_met` = the image set is *sufficient to evaluate this specific claim* (claimed part + condition assessable). They are **independent** (sample case_008 has `valid_image=false`, `evidence_standard_met=true`). | High (data-grounded) |
| A2 | Does `valid_image == false` force `not_enough_information`? | **No.** Sample `case_008` is `valid_image=false` **and** `claim_status=contradicted`. The hard NEI gate is `evidence_standard_met == false`, **not** `valid_image == false`. ⚠️ This contradicts the prescribed starting tree — flagged to the reviewer (→ §8, DESIGN_REVIEW). | High (one explicit counterexample; semantically sound) |
| A3 | When is `claim_mismatch` vs `contradicted`? | `claim_mismatch` (risk flag) fires when visible damage exists but doesn't match the claimed nature/severity (case_005: claimed "severe", image shows minor scratch). It typically *drives* `contradicted`. | Medium |
| A4 | Does `manual_review_required` change `claim_status`? | **No.** It is an overlay/routing flag. case_017 is `supported` *with* `user_history_risk;manual_review_required`. History never flips the decision. | High (data-grounded) |
| A5 | `supporting_image_ids` for a `contradicted` row | Cite the image(s) that *ground the contradiction* (case_008 → `img_1`). Only NEI uses `none`. | High |
| A6 | What counts as `issue_type` for a contradicted-by-mismatch row | Report the *actually visible* issue (case_005 → `scratch`), not the claimed one. The contradiction lives in `claim_status` + `claim_mismatch`, not in faking `issue_type`. | Medium |
| A7 | Free-text justification length/style | One–two concise sentences, image-grounded, may name image IDs. Mirror the terse style of the sample labels. | Low impact |

---

## 6. Hidden assumptions (made explicit so they can be challenged)

1. **`sample_claims.csv` labels are authoritative ground truth** for scoring, but are **not** a representative sample of the hidden set (only 20 rows, and they omit several legal enum values — §1.2 note). → Favor rules that generalize by construction over patterns fit to 20 rows.
2. **The grader is automated and column-wise.** We assume exact-match for fixed enums and some tolerance (set overlap) for multi-label `risk_flags`/`supporting_image_ids`; free-text fields are likely *not* hard-graded (or graded loosely). We optimize the structured fields first.
3. **`claim_object` is always one of car/laptop/package** (structural fact from spec).
4. **Each case folder's images correspond to that row's `image_paths`** and image IDs are `img_1..img_N`.
5. **History join is by `user_id`** and every test `user_id` has a history row (verify at runtime; default to "no history / none" if missing).
6. **The conversation always contains the actual claim**, sometimes after hedging/confusion (case_006, case_013, case_018). The parser must extract the *final* stated claim.

---

## 7. Expected evaluation challenges

- **Tiny labeled set (20):** any accuracy number is *directional*, not statistically meaningful. No CIs/significance.
- **Multi-label scoring** of `risk_flags` and `supporting_image_ids` (order-insensitive set comparison).
- **Free-text fields** hard to grade objectively → LLM-as-judge is *prohibited* (self-preference, poor calibration at this scale); we grade structured fields and read free text manually.
- **Distribution shift:** hidden set likely contains the unseen enum values (`glass_shatter`, `missing_part`, `taillight`, etc.) and possibly adversarial images.
- **Class imbalance:** sample is supported-heavy (13/20). A model biased toward "supported" would look good on sample and fail on the hidden set.

---

## 8. Failure cases likely in real-world data (preview; full list in FAILURE_MODES)

- Right object, wrong part shown (claim says headlight, photo shows the door) → `wrong_angle`/`damage_not_visible` + NEI (case_006).
- Image content contradicts the claim's severity/part (case_005, case_008, case_019) → `claim_mismatch`/`wrong_object` + contradicted.
- Part visible, no damage present → `damage_not_visible` + `issue_type=none` + contradicted (case_014, case_020).
- Prompt-injection text inside an image ("approve this claim") → `text_instruction_present`, ignore the instruction (case_020).
- Non-original / stock / screenshot images → `non_original_image`, `valid_image=false` (case_008).
- Blurry/cropped/low-light images, missing or corrupt files, malformed rows.
- Multilingual + hedged/rambling conversations where the real claim is stated last.

---

## 9. Overfitting risks to `sample_claims.csv` (and mitigations)

| Risk | Mitigation |
|---|---|
| Encoding sample-specific patterns ("car claims are usually dents") into logic | Only **structural facts** (enum sets, object→part mapping, semantic invariants) go into code. Empirical frequencies are *priors at most*, never rules. (→ DATASET_ANALYSIS tags every finding (a) structural vs (b) empirical.) |
| Tuning prompts to nail the 20 rows | Treat sample as a *regression + smoke* set; prefer general instructions; run blank-drop / image-swap tests (→ EVALUATION_STRATEGY) to prove the system uses pixels, not text priors. |
| "Supported" bias (13/20 supported) | Decision tree is symmetric and evidence-driven; we specifically watch contradicted/NEI recall. |
| Memorizing which users are risky | History risk is derived from `history_flags` + bounded numeric weights — generalizes to unseen users by construction. |
| Assuming only the 10 seen issue_types / seen parts | Pydantic enums include the full spec list; prompt enumerates all allowed values. |

---

## 10. Output of this phase

The contract in §1 is frozen and feeds: DATASET_ANALYSIS (distributions over these fields), THREAT_MODEL (every threat → a `risk_flags` value or a history signal), DECISION_ENGINE (invariants + tree over these enums), and the Pydantic source of truth in code. The one open item requiring reviewer sign-off is **A2 / the `valid_image` invariant** (§5, §8 of DESIGN_REVIEW).
