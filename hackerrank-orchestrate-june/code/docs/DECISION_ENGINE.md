# DECISION_ENGINE.md — Phase 7

The deterministic core. It consumes **perception facts** (from the VLM via `submit_decision`) + **deterministic post-check outputs** (evidence sufficiency, object/part consistency, multi-image aggregation, risk overlay) and produces the final `claim_status`, `severity`, and the cross-field-consistent output row. Every output is traceable to a deterministic rule or a logged VLM observation.

> **One open item requires reviewer sign-off:** the treatment of `valid_image` in the NEI gate deviates from the prescribed starting tree because the ground-truth labels contradict the prescribed rule. See §7. Everything else follows the prescribed tree, refined against the data.

---

## 1. Definitions (precise)

- **`supported`** — the image evidence is sufficient (`evidence_standard_met=true`) **and** the VLM isolates a concrete `issue_type ∉ {none, unknown}` that **matches the claimed issue family on the claimed part**.
- **`contradicted`** — the image evidence is sufficient to evaluate, **and** it conflicts with the claim: a contradiction signal is set (`wrong_object` / `wrong_object_part` / `claim_mismatch`) **or** the claimed part is visible and **undamaged** (`issue_type=none`).
- **`not_enough_information` (NEI)** — the image evidence is **not sufficient** to evaluate the claim (`evidence_standard_met=false`), **or** the VLM abstains on the issue (`issue_type=unknown`) with no contradiction signal.

These are mutually exclusive and exhaustive over the perception facts.

---

## 2. Input facts to the engine (all logged)

From perception (`submit_decision`) + post-checks:

```
claim_object            ∈ {car, laptop, package}            # input
claimed_part            ∈ object_part enum | unknown        # claim parse
claimed_issue_family    ∈ family set | unknown              # claim parse
visible_issue_type      ∈ issue_type enum                   # VLM
visible_object_part     ∈ object_part enum                  # VLM
object_matches_claim    ∈ {true,false,unknown}              # consistency validator
part_assessable         ∈ {true,false}                      # per claimed part visible & evaluable
evidence_standard_met   ∈ {true,false}                      # evidence validation (code, rule-grounded)
valid_image             ∈ {true,false}                      # quality/authenticity gate
severity_estimate       ∈ {none,low,medium,high,unknown}    # VLM, soft
supporting_candidates   = [image_ids that ground the read]  # aggregation
contradiction_signals   ⊆ {wrong_object, wrong_object_part, claim_mismatch}
quality_flags           ⊆ {blurry_image, cropped_or_obstructed, low_light_or_glare, wrong_angle}
authenticity_flags      ⊆ {non_original_image, possible_manipulation}
text_instruction_present∈ {true,false}
history_overlay         ⊆ {user_history_risk, manual_review_required}
vlm_confidence          ∈ [0,1] (model-stated, soft)
```

---

## 3. The decision tree (deterministic, ordered; first match wins)

Refined from the prescribed starting tree against the data (DATASET_ANALYSIS §6). Ordering matters.

```
def decide(f) -> claim_status:
    # ── 0. Hard NEI gate (the safe invariant; replaces "valid_image==false") ──
    if f.evidence_standard_met == False:
        return NEI                      # case_006, case_018

    # evidence_standard_met == True from here on
    # ── 1. Contradiction by object/part/claim mismatch ──
    if "wrong_object" in f.contradiction_signals:        # case_019
        return CONTRADICTED
    if "wrong_object_part" in f.contradiction_signals:
        return CONTRADICTED
    if "claim_mismatch" in f.contradiction_signals:      # case_005, case_008
        return CONTRADICTED

    # ── 2. Contradiction by "claimed part visible & undamaged" ──
    if f.part_assessable and f.visible_issue_type == "none":   # case_014, case_020
        return CONTRADICTED

    # ── 3. VLM abstains on the issue (no contradiction found) ──
    if f.visible_issue_type == "unknown":
        return NEI

    # ── 4. Support: concrete issue that matches the claim ──
    if (f.visible_issue_type not in {"none","unknown"}
        and issue_matches_claim(f.visible_issue_type, f.claimed_issue_family)
        and f.object_matches_claim != False):
        return SUPPORTED                # case_001,002,003,004,007,009,010,011,012,013,015,016,017

    # ── 5. Concrete issue but does NOT match the claim → mismatch → contradicted ──
    if f.visible_issue_type not in {"none","unknown"}:
        # mark claim_mismatch in risk_flags (the visible damage isn't what was claimed)
        return CONTRADICTED

    # ── 6. Default safe posture ──
    return NEI
```

`issue_matches_claim(visible, claimed_family)`: deterministic table mapping each `issue_type` to its family and checking membership (e.g. `dent/scratch → body_panel`; `crack/glass_shatter/broken_part/missing_part → glass_light_mirror or structural`; `crushed_packaging/torn_packaging → package_exterior`; `water_damage/stain → surface`). If `claimed_family == unknown`, accept any concrete visible issue on the claimed/visible part (avoid over-strictness) but lower confidence.

**Walked against all 20 sample rows → matches every label** (with §7's gate). E.g. case_008: evidence true → not wrong_object/part → `claim_mismatch` set (severe damage ≠ claimed hood scratch) → CONTRADICTED ✓ (even though `valid_image=false`). case_017: evidence true, no contradiction, `water_damage` matches → SUPPORTED ✓ (history overlay added but status unchanged).

---

## 4. Evidence-sufficiency logic (`evidence_standard_met`) — [CODE], rule-grounded

```
part_assessable = (claimed part region is clearly visible in ≥1 relevant, usable image,
                   per the matched evidence_requirements rule for (claim_object, claimed_issue_family))
evidence_standard_met = part_assessable AND at_least_one_relevant_usable_image
```
- Aggregated across images (one clear relevant image suffices even if another is blurry — case_007).
- Grounded in the matched rule text via `lookup_evidence_requirement` (e.g. REQ_CAR_BODY_PANEL requires a panel angle where surface marks are assessable).
- `valid_image=false` due to **quality** (blurry/corrupt/cropped) typically also makes `evidence_standard_met=false`; `valid_image=false` due to **authenticity** (non_original) does **not** by itself reduce sufficiency (case_008).

## 5. Contradiction logic
A claim is contradicted (given sufficient evidence) iff any:
- `wrong_object` (object shown ≠ claim_object) — case_019
- `wrong_object_part` (a different part is shown, claimed part contradicted)
- `claim_mismatch` (visible damage's nature/severity ≠ claimed) — case_005, case_008
- claimed part visible & `issue_type=none` (no damage where damage was claimed) — case_014, case_020

## 6. Support logic
Supported iff: `evidence_standard_met=true` AND `issue_type ∉ {none,unknown}` AND the issue matches the claimed family AND object matches AND ≥1 supporting image. (Requires a named, locatable visual cue — guards against hallucination V2.)

## 7. ⚠️ The `valid_image` deviation (REQUIRES REVIEWER SIGN-OFF)

The prescribed starting tree lists **"NEI if `valid_image == false`"** as branch 1, and Phase-0 guidance says "encode `claim_status` must be NEI when `valid_image` is false" as a Pydantic invariant.

**The ground-truth labels contradict this.** Sample **case_008** has `valid_image=false` **and** `claim_status=contradicted` (`evidence_standard_met=true`). Encoding `valid_image==false ⇒ NEI` as a hard rule would mislabel case_008 and any "non-original image that still clearly contradicts the claim" case.

**Resolution adopted (pending your confirmation):**
- The **hard NEI gate is `evidence_standard_met == false`** (holds on all sample rows; semantically sound).
- `valid_image` is computed and reported independently (authenticity/usability), and is a **strong prior** toward NEI — but the common quality-driven `valid_image=false` already implies `evidence_standard_met=false`, so it reaches NEI through the gate anyway. The only divergence is the authenticity case (non-original yet contradicting), which the data says should be `contradicted`.
- We therefore **do not** encode `valid_image==false ⇒ NEI` as an inviolable Pydantic invariant.

**If you prefer strict adherence to the prescribed tree** (accepting the case_008 miss on the sample), say so and we will gate NEI on `valid_image==false` instead. This is the single decision-logic item we are surfacing rather than silently choosing.

---

## 8. Cross-field invariants (encoded in the Pydantic model — impossible states can't be emitted)

**HARD (model-level validators; violation → repair pass → safe-default):**
1. `evidence_standard_met == False` ⇒ `claim_status == not_enough_information`.
2. `claim_status == not_enough_information` ⟺ `supporting_image_ids == "none"`.
3. `claim_status == not_enough_information` ⇒ `severity == "unknown"`.
4. `claim_status == supported` ⇒ `evidence_standard_met == True` **and** `issue_type ∉ {none, unknown}` **and** `supporting_image_ids != none`.
5. `object_part` ∈ the enum for `claim_object` (per-object `Literal`).
6. all `risk_flags` ∈ allowed set; if any non-`none` flag present, `none` is removed; set is deduped + canonically ordered.
7. `claim_status`, `issue_type`, `severity` ∈ their allowed `Literal`s.

**SOFT (strong defaults; VLM may override only with a logged reason):**
8. `issue_type == none` ⇒ `severity == none` and `claim_status != supported`.
9. `issue_type == unknown` ⇒ `claim_status != supported`.
10. `claim_status == contradicted` ⇒ `supporting_image_ids != none` (cite the image grounding the contradiction).

> Invariant #1 (not the prescribed `valid_image` one) is the load-bearing NEI rule — see §7.

---

## 9. Severity logic — [LLM] soft + [CODE] invariants
- VLM estimates `severity` from visible damage extent, with explicit `unknown` when ambiguous. **No** bounding-box/area-ratio or detector geometry — deliberately soft.
- Code enforces: NEI ⇒ `unknown` (inv. 3); `issue_type=none` ⇒ `none` (inv. 8). Otherwise pass through the VLM estimate.
- Severity is reported regardless of status (a contradicted-by-mismatch row still reports the *visible* severity — case_008 → `high`, case_005 → `low`).

## 10. Confidence handling
- `vlm_confidence` is soft and used only to **bias toward abstention**: low confidence on the issue ⇒ prefer `issue_type=unknown` ⇒ NEI rather than a risky supported/contradicted.
- No numeric confidence is emitted (not in the contract). Confidence influences abstention, not a field.

## 11. Risk-flag assembly (final `risk_flags`) — [CODE]
Union, then normalize (inv. 6):
```
risk_flags = quality_flags ∪ authenticity_flags ∪ contradiction_signals
           ∪ ({text_instruction_present} if present)
           ∪ history_overlay                 # user_history_risk / manual_review_required
add manual_review_required if: authenticity_flags nonempty
                               OR (claim_status != supported AND user_history_risk present)
                               OR history demands review
if risk_flags empty: risk_flags = {none}
```
History overlay is **additive** — it never participates in §3 (the decision tree never reads history). This enforces "history never overrides clear visual evidence" (case_017: supported + `user_history_risk;manual_review_required`).

## 12. Explainability / traceability
Each emitted row carries (in the audit log, not the CSV): the branch of §3 that fired, the matched evidence rule id, the invariants applied, the VLM cue(s) cited, and any override logic. `evidence_standard_met_reason` and `claim_status_justification` are generated from these facts (Explanation Generator), so every CSV row is backed by a logged, inspectable rationale.

## 13. Worked examples (sample)
| case | gate/branch | result |
|---|---|---|
| 006 | inv-gate: evidence_standard_met=false | NEI, severity=unknown, supporting=none ✓ |
| 008 | evidence true → claim_mismatch → §3.1 | contradicted, severity=high, supporting=img_1, valid_image=false ✓ |
| 014 | evidence true → part visible & issue=none → §3.2 | contradicted, issue=none, severity=none ✓ |
| 017 | evidence true → water_damage matches → §3.4 | supported; risk overlay adds user_history_risk;manual_review_required ✓ |
| 019 | evidence true → wrong_object → §3.1 | contradicted, issue=unknown, part=unknown ✓ |
