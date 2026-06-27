# DATASET_ANALYSIS.md — Phase 2

Complete audit of the four CSVs + image folders. **Sample-size reality check up front:** `sample_claims.csv` has **20 labeled** rows; `claims.csv` (the actual test set) has **44 input-only** rows; `user_history.csv` covers **47 users**. Any distribution over 20 rows is a **weak signal**, not a statistically meaningful pattern.

Every finding is tagged:
- **(a) STRUCTURAL/SCHEMA** — trust fully (enum sets, object→part mapping, file layout, semantic invariants that hold by definition).
- **(b) EMPIRICAL (n=20)** — a weak prior only. **Never** encode as logic.

---

## 1. File inventory & shapes

| File | Rows | Role | Columns |
|---|---|---|---|
| `claims.csv` | 44 | test (inputs only) | `user_id, image_paths, user_claim, claim_object` |
| `sample_claims.csv` | 20 | dev (inputs + 10 labels) | 4 inputs + 10 prediction columns |
| `user_history.csv` | 47 | per-user risk context | `user_id, past_claim_count, accept_claim, manual_review_claim, rejected_claim, last_90_days_claim_count, history_flags, history_summary` |
| `evidence_requirements.csv` | 11 | rulebook | `requirement_id, claim_object, applies_to, minimum_image_evidence` |
| `output.csv` (provided) | header only | submission template | the 14 columns in order |

**(a) STRUCTURAL.** `image_paths` is `;`-separated; `user_claim` turns are `\|`-separated; image_id = filename без extension. CSV is fully quoted (`QUOTE_ALL`).

---

## 2. `claim_object` distribution

| object | test (claims.csv, n=44) | sample (n=20) |
|---|---|---|
| car | 18 | 8 |
| laptop | 13 | 6 |
| package | 13 | 6 |

**(a) STRUCTURAL:** exactly three object types; the test set spans all three with car most common. **(b) EMPIRICAL:** rough 2:1.5:1.5 mix — used only for cost estimation, not logic.

---

## 3. Image-count distribution per claim

| #images | test (n=44) | sample (n=20) |
|---|---|---|
| 1 | 13 | 11 |
| 2 | 24 | 9 |
| 3 | 7 | 0 |

**(a) STRUCTURAL:** claims have **1–3 images**; the test set includes 3-image claims that the sample never exercises → multi-image aggregation **must** be tested even though sample maxes at 2. Image folders: sample has 20 case dirs, test has 44 case dirs; `img_1.jpg … img_N.jpg`. Total images ≈ 20 sample + ~**89 test** (13·1 + 24·2 + 7·3 = 13+48+21 = **82** test images; minor count differences resolved at runtime by globbing the folder).

**Implication:** cost/latency scale with image count, not just row count (→ IMPLEMENTATION_PLAN operational analysis).

---

## 4. Label distributions in `sample_claims.csv` (all **(b) EMPIRICAL, n=20** unless noted)

### 4.1 `claim_status`
`supported` 13 · `contradicted` 5 · `not_enough_information` 2.
→ **Supported-heavy.** A model that defaults to "supported" scores ~65% on sample and likely fails the hidden set. We watch contradicted/NEI recall specifically.

### 4.2 `evidence_standard_met` / `valid_image`
`evidence_standard_met`: true 18 / false 2. `valid_image`: true 18 / false 2.
**(a) STRUCTURAL insight (not a frequency):** the two `false` sets are **not identical** — `case_008` is `valid_image=false, evidence_standard_met=true`; `case_018` is `valid_image=false, evidence_standard_met=false`; `case_006` is `valid_image=true, evidence_standard_met=false`. → the two booleans are **independent axes** (authenticity/usability vs. sufficiency-for-this-claim). This is a definitional finding, trusted fully.

### 4.3 `issue_type` (n=20)
`dent` 3 · `crack` 3 · `broken_part` 3 · `unknown` 3 · `scratch` 2 · `none` 2 · `stain` 1 · `crushed_packaging` 1 · `torn_packaging` 1 · `water_damage` 1.
**(a) STRUCTURAL:** `glass_shatter` and `missing_part` are **in the spec but absent from the sample** → the hidden set may use them; the model must emit them.

### 4.4 `object_part` (n=20)
car: `rear_bumper` 2, `front_bumper` 2, `windshield` 1, `side_mirror` 1, `headlight` 1, `door` 1. laptop: `screen` 2, `hinge` 1, `keyboard` 1, `corner` 1, `trackpad` 1. package: `seal` 2, `package_corner` 1, `package_side` 1, `contents` 1. plus `unknown` 1.
**(a) STRUCTURAL:** many legal parts unseen (`hood, taillight, fender, quarter_panel, body`; `lid, port, base`; `box, label, item`). Do not assume the seen subset.

### 4.5 `severity` (n=20)
`medium` 11 · `low` 4 · `unknown` 2 · `none` 2 · `high` 1.
**(a) STRUCTURAL invariants observed (semantic, hold by definition — see §6):** NEI rows → `unknown`; `issue_type=none` rows → `severity=none`.

### 4.6 `risk_flags` (n=20) — every distinct combination
`none` 11 · and 9 multi-flag rows. Flags actually exercised: `claim_mismatch`, `user_history_risk`, `manual_review_required`, `wrong_angle`, `damage_not_visible`, `blurry_image`, `non_original_image`, `cropped_or_obstructed`, `wrong_object`, `text_instruction_present`.
**(a) STRUCTURAL:** **never exercised in sample** but legal: `low_light_or_glare`, `wrong_object_part`, `possible_manipulation`. The threat model must still produce them.

---

## 5. Per-row map (the highest-value artifact at n=20)

Reading all 20 rows is fully tractable and worth more than any aggregate. Key rows and what each teaches (all **(b)** as data points, but they *illustrate* the **(a)** invariants in §6):

| case | object | claim vs image | status | teaches |
|---|---|---|---|---|
| 001 | car | rear-bumper dent, shown | supported | clean positive baseline |
| 002 | car | Hinglish, front-bumper scratch; 2 imgs (context+closeup) | supported | multilingual; multi-image where close-up carries evidence; `supporting=img_1` |
| 005 | car | claims **severe** rear damage, image shows **minor scratch** | contradicted | `claim_mismatch` (severity) drives contradicted; `issue_type=scratch` (the *visible* issue), `severity=low` |
| 006 | car | rambling, finally claims headlight; image shows another part | NEI | `evidence_standard_met=false`, `wrong_angle;damage_not_visible`, `issue_type=unknown`, `supporting=none`; **`valid_image=true`** (image fine, just wrong content) |
| 007 | car | door dent; img_1 blurry, img_2 clear | supported | per-image quality; aggregate still supported; `blurry_image` flag **with** `valid_image=true`; `supporting=img_2` |
| 008 | car | claims hood scratch; image shows **severe front-end** damage, suspected non-original | contradicted | **`valid_image=false` + `claim_status=contradicted`** (the key counterexample); `evidence_standard_met=true`; `non_original_image`; `issue_type=broken_part`, `severity=high` |
| 013 | laptop | long hedge, finally "screen shattered" | supported | extract final claim from noisy convo; `issue_type=crack` |
| 014 | laptop | claims trackpad physical damage; area visible, no damage | contradicted | `damage_not_visible` + `issue_type=none` + `severity=none`; history adds `user_history_risk;manual_review_required` |
| 017 | package | water damage clearly visible; risky user | **supported** | **history does NOT override visual evidence** — supported stands, `user_history_risk;manual_review_required` overlaid |
| 018 | package | "item missing"; images don't show contents | NEI | `evidence_standard_met=false`, `cropped_or_obstructed;damage_not_visible`, `valid_image=false`, `supporting=none` |
| 019 | package | claims crushed shipping box; image shows a **different object** | contradicted | `wrong_object;claim_mismatch`; `issue_type=unknown, object_part=unknown` yet contradicted |
| 020 | package | claims torn-open seal; seal intact; instruction text in image | contradicted | `damage_not_visible` + `text_instruction_present` (ignore embedded instruction); `issue_type=none`, `severity=none` |

---

## 6. Cross-field invariants observed (tag: **(a) STRUCTURAL/semantic** — candidates for hard rules)

Derived from the rows above; each holds across all 20 and is semantically sound (so it should generalize). These feed DECISION_ENGINE and the Pydantic model. Each is marked **HARD** (encode as invariant) or **SOFT** (strong prior; allow VLM override with logged reason).

1. **HARD:** `evidence_standard_met == false` ⇒ `claim_status == not_enough_information`. (case_006, case_018.) Semantically: insufficient evidence ⇒ can't support or contradict.
2. **HARD:** `claim_status == not_enough_information` ⟺ `supporting_image_ids == none`. (NEI rows cite none; all supported/contradicted cite ≥1.)
3. **HARD:** `claim_status == not_enough_information` ⇒ `severity == unknown`. (case_006, case_018.)
4. **HARD:** `claim_status == supported` ⇒ `evidence_standard_met == true` **and** `issue_type ∉ {none, unknown}`. (All 13 supported rows.)
5. **SOFT:** `issue_type == none` ⇒ `severity == none` and status ≠ supported (part visible, no damage ⇒ contradicted if claim asserted damage). (case_014, case_020.)
6. **SOFT:** `issue_type == unknown` ⇒ status ≠ supported (NEI, or contradicted when a `wrong_object`/`claim_mismatch` signal is present — case_019).
7. **NOT AN INVARIANT (explicit anti-finding):** `valid_image == false` does **NOT** imply NEI. **Counterexample case_008.** The prescribed starting tree's "`valid_image==false` ⇒ NEI" rule is **over-strict** and would mislabel case_008. → flagged to reviewer; we gate on invariant #1 instead. (→ DESIGN_REVIEW.)

---

## 7. `user_history.csv` audit → interpretable, bounded risk weights

Columns are clean integers + a `history_flags` token set + free-text `history_summary`. Ranges across 47 users: `past_claim_count` 0–14, `rejected_claim` 0–7, `last_90_days_claim_count` 0–9.

**(a) STRUCTURAL — primary signal:** `history_flags` is a `;`-separated set drawn from `{none, user_history_risk, manual_review_required}`. In every labeled row where the user's `history_flags` contains `user_history_risk` or `manual_review_required`, the corresponding token appears in the output `risk_flags` (case_005, 008, 014, 017, 018, 019, 020). → **Rule:** map `history_flags` tokens directly into the risk overlay.

**(b) EMPIRICAL → bounded secondary score (interpretable, no magic coefficients):** derive a small risk score from the numeric fields to *corroborate* (not invent) `user_history_risk` when `history_flags` is `none` but the numbers are extreme. Proposed interpretable, bounded signals (each 0/1, capped):
- `rejection_rate = rejected_claim / max(past_claim_count, 1)` ≥ 0.4 → elevated
- `last_90_days_claim_count` ≥ 4 → elevated (burst behavior)
- `manual_review_claim / max(past_claim_count,1)` ≥ 0.4 → review-prone

These thresholds are **priors derived from the field ranges**, not fitted to labels; they only *add* `user_history_risk`/`manual_review_required`, and — per the spec — **never change `claim_status`** (the history layer is additive overlay only; case_017 proves visual evidence wins). Document the thresholds in code; keep them few and legible.

**Critical generalization note:** because risk is computed from history *fields*, it generalizes to the 47 (and any unseen) users by construction — we never memorize "user_005 is risky."

---

## 8. `evidence_requirements.csv` audit → the rulebook

11 rules. `claim_object ∈ {all, car, laptop, package}`; `applies_to` is an issue-family string; `minimum_image_evidence` is a natural-language standard.

| requirement_id | object | applies_to (issue family) |
|---|---|---|
| REQ_GENERAL_OBJECT_PART | all | general claim review |
| REQ_GENERAL_MULTI_IMAGE | all | multi-image rows |
| REQ_CAR_BODY_PANEL | car | dent or scratch |
| REQ_CAR_GLASS_LIGHT_MIRROR | car | crack, broken, or missing part |
| REQ_CAR_IDENTITY_OR_SIDE | car | vehicle identity or orientation |
| REQ_LAPTOP_SCREEN_KEYBOARD_TRACKPAD | laptop | screen, keyboard, or trackpad |
| REQ_LAPTOP_BODY_HINGE_PORT | laptop | hinge, lid, corner, body, or port |
| REQ_PACKAGE_EXTERIOR | package | crushed, torn, or seal damage |
| REQ_PACKAGE_LABEL_OR_STAIN | package | water, stain, or label damage |
| REQ_PACKAGE_CONTENTS | package | contents or inner item |
| REQ_REVIEW_TRUST | all | reviewability (usable, relevant, grounded) |

**(a) STRUCTURAL design use:** map `(claim_object, issue_family)` → the relevant rule(s). The full rule text is BAKED INTO the cached system prompt (via `format_rulebook`, not a live tool round-trip — `select_rules` is the family-relevant selector kept for offline/audit use), and the per-claim user message points the model at the rules for that `claim_object`, so the *evidence-sufficiency judgment is grounded in the rulebook*, not improvised. `REQ_REVIEW_TRUST` underpins `valid_image` (authenticity/usability); `REQ_GENERAL_MULTI_IMAGE` underpins per-image evaluation + aggregation. Issue→family mapping is a small deterministic table (e.g., `dent/scratch → car body panel`; `crack/glass_shatter/broken_part/missing_part → glass/light/mirror`; `crushed/torn → package exterior`; `water/stain → package label/stain`; `missing item → package contents`).

---

## 9. Patterns that look robust vs. won't generalize

**Likely robust (structural/semantic):** the §6 HARD invariants; object→part enum mapping; history_flags→risk mapping; evidence-rule selection by family; "primary part region must be visible to support."

**Won't generalize (do NOT encode):** object→issue frequencies ("cars are mostly dents"); the supported:contradicted:NEI ratio; which specific severities pair with which issues; per-user risk identities; the fact that sample never shows `glass_shatter`/`missing_part`/3-image rows.

---

## 10. Data-quality concerns to handle in code

- Image file present but unreadable/corrupt; image count in folder ≠ count in `image_paths` (glob the folder, reconcile, flag).
- Conversation with no clear claim, or claim only at the very end (parse the *final* asserted part+condition).
- `user_id` in claims with no history row (default to `history_flags=none`, zero counts).
- Non-ASCII text (Hinglish, Devanagari) — preserve encoding (UTF-8) on read **and** write; never re-encode the echoed input columns.
- Embedded `;` and `\|` inside quoted fields must survive the round-trip unchanged (I1).

---

### Bottom line
Trust the **(a)** structural facts and semantic invariants; treat all **(b)** frequencies as priors only. The build's reliability rests on the rule-based layers (which generalize by construction), not on patterns mined from 20 rows.
