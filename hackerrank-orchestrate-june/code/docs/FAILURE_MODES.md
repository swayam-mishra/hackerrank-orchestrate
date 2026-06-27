# FAILURE_MODES.md — Phase 4

Plausible failure modes across every layer, with **detection**, **mitigation**, and **fallback**. "Fallback" is the behavior that keeps the batch producing a *valid, safe* row even when the layer fails. Default safe posture for unresolved failures: `claim_status=not_enough_information`, `supporting_image_ids=none`, `severity=unknown`, plus the most specific applicable `risk_flags`, and `manual_review_required` when uncertainty is material.

Distinct from THREAT_MODEL (adversarial) — this is about *honest* failures, incl. our own bugs.

---

## 1. Vision / VLM perception

| # | Failure | Detection | Mitigation | Fallback |
|---|---|---|---|---|
| V1 | Misses real damage (false negative) → wrongly `damage_not_visible`/contradicted | sample error analysis; blank-drop/image-swap tests; multi-image cross-read | `inspect_image` deterministic crop/zoom on the claimed region for re-examination at full res; resize to 2576 px long edge (don't lose detail to server downsize) | if low confidence, abstain → `issue_type=unknown` → NEI rather than a wrong contradiction |
| V2 | Hallucinates damage that isn't there (false positive) → wrongly supported | error analysis; image-swap test (should change output) | require the VLM to name the *specific* visible cue and which image/region; deterministic check that `supporting_image_ids` is non-empty for supported | if cue is vague/unlocatable, downgrade to NEI |
| V3 | Wrong `object_part` (right damage, mislabeled location) | object/part consistency validator (claimed part ∈ object's enum; visible part vs claimed) | per-object `object_part` enum constrains output; VLM told the allowed parts for this `claim_object` | if part can't be determined → `unknown` |
| V4 | Wrong `issue_type` (e.g. `crack` vs `glass_shatter`) | error analysis on labeled rows | enumerate full issue list + short definitions in prompt; map close calls to nearest allowed value | if undecidable → `unknown` (never invent a value) |
| V5 | Severity miscalibration (minor read as severe / vice-versa) | compare to sample `severity`; treat as deliberately soft | document severity as a soft VLM signal with explicit `unknown` abstention; **no** bounding-box/area math | `unknown` when ambiguous |
| V6 | Detail below resolution threshold (hairline crack, small scratch) | VLM reports "possible but unclear" | `inspect_image` crop/zoom of the original full-res region the model points to | if still unclear → NEI + `damage_not_visible` |
| V7 | Non-determinism: same input, different perception across runs | regression diff across runs; the determinism boundary is documented | decision is computed by deterministic code from logged facts; only perception varies | accept perception variance; the *decision rule* is stable given facts |
| V8 | Multilingual claim misread | sample includes Hinglish; spot-check | Claude handles natively; no translation layer to fail | if claim genuinely unparseable → NEI |

## 2. OCR / image-text screening

| # | Failure | Detection | Mitigation | Fallback |
|---|---|---|---|---|
| O1 | Misses instruction text in image → no `text_instruction_present` | injection-string test images | VLM transcribes all visible text as data; phrase screen on transcription; optional `rapidocr-onnxruntime` fallback | even if missed, the *decision* is deterministic and can't be steered by image text |
| O2 | False `text_instruction_present` on benign text (a sign, a label) | review flagged rows | screen only for *instruction-like* patterns (imperatives about the claim/system), not all text | over-flagging only adds a risk flag + review; never changes the decision wrongly |
| O3 | OCR dependency fails to install / load | import guard | OCR is optional; default path is the VLM transcription | degrade silently to VLM-only screening |

## 3. Claim understanding (conversation parsing)

| # | Failure | Detection | Mitigation | Fallback |
|---|---|---|---|---|
| C1 | Picks the wrong target part from a rambling convo | sample case_006/013/018 | instruction to extract the *final consolidated* asserted part + condition; log the extracted claim | if multiple/none → NEI with reason |
| C2 | Treats conversation as instruction (lets it steer the decision) | injection test on `user_claim` | `user_claim` delimited as untrusted; decision in code | ignored as data |
| C3 | Over-reads severity words ("pretty bad") as ground truth | case_005 | severity comes from the *image*, not the claim's adjectives | claim adjectives never set `severity` |
| C4 | Empty / nonsensical conversation | length/parse check | proceed on images alone if a target part is inferable; else NEI | NEI + reason |

## 4. Evidence validation (sufficiency vs rulebook)

| # | Failure | Detection | Mitigation | Fallback |
|---|---|---|---|---|
| EV1 | Declares evidence sufficient when the claimed part isn't actually shown | object/part consistency cross-check; case_006 | sufficiency keyed to "claimed part + claimed condition assessable", grounded in the matched `evidence_requirements` rule | if claimed part not assessable → `evidence_standard_met=false` → NEI |
| EV2 | Declares insufficient when a clear image exists (too strict) | case_007 (blurry+clear) error analysis | aggregate across images; one sufficient relevant image meets the standard | prefer the clearest relevant image |
| EV3 | Wrong rule selected for the issue family | unit test the issue→family→rule map | deterministic `(claim_object, issue_family) → rule` table fed to `lookup_evidence_requirement` | default to REQ_GENERAL_OBJECT_PART + REQ_REVIEW_TRUST |
| EV4 | `valid_image` conflated with `evidence_standard_met` | case_008 (the two differ) | keep them independent axes (authenticity/usability vs sufficiency) | compute separately; gate decision on `evidence_standard_met`, not `valid_image` |

## 5. Decision engine

| # | Failure | Detection | Mitigation | Fallback |
|---|---|---|---|---|
| D1 | Emits an impossible state (e.g. supported with `issue_type=unknown`) | Pydantic invariants; unit tests | encode HARD invariants in the model (DATASET_ANALYSIS §6): NEI⟺`supporting=none`; NEI⇒`severity=unknown`; supported⇒evidence true & issue∉{none,unknown} | model raises → repair pass → safe-default row |
| D2 | History overrides clear visual evidence | case_017 regression test | risk overlay is **additive**; tree keys only on evidence + visual signals; history can add flags, not flip status | enforce in code; unit-tested |
| D3 | Over-strict `valid_image⇒NEI` rule mislabels contradicted-but-non-original (case_008) | case_008 regression | gate NEI on `evidence_standard_met==false`, NOT on `valid_image` (⚠️ deviates from prescribed starting tree — see DESIGN_REVIEW) | flagged for reviewer sign-off before Stage B |
| D4 | Supported-bias (defaults to supported under uncertainty) | confusion matrix on sample; class-balance watch | symmetric, evidence-driven tree; abstain to NEI on low confidence | NEI under uncertainty |
| D5 | Decision not explainable/traceable | every decision must cite a rule or logged VLM observation | log: tool calls, VLM rationale, matched rule, invariant applied, override logic | row without provenance is a bug → fail the unit test |

## 6. Risk assessment

| # | Failure | Detection | Mitigation | Fallback |
|---|---|---|---|---|
| R1 | Misses `history_flags` mapping | unit test on history join | direct token copy from `history_flags`; default `none` if user missing | absent history ⇒ no history flags (safe) |
| R2 | Magic/over-fit risk coefficients | review; DATASET_ANALYSIS §7 | few, interpretable, bounded thresholds from field ranges; documented | thresholds in config, legible |
| R3 | `manual_review_required` spam or never-set | flag-frequency check on sample | set on: history review tokens, authenticity flags, or borderline contradiction/NEI with elevated user risk | conservative default: set when uncertainty is material |
| R4 | Duplicate/ordering issues in `risk_flags` set | output validator | canonical ordering + dedupe; drop `none` if other flags present | validator normalizes |

## 7. Multi-image aggregation

| # | Failure | Detection | Mitigation | Fallback |
|---|---|---|---|---|
| M1 | Picks wrong `supporting_image_ids` | case_002/007/010 (close-up carries evidence) | cite the specific image(s) that ground the decision; close-up > context shot when both relevant | if none sufficient → `none` (⇒ NEI) |
| M2 | 3-image rows untested (sample maxes at 2) | test set has 7 such rows; synthetic test | aggregation logic is N-agnostic; add a 3-image smoke test | per-image eval then aggregate |
| M3 | Duplicate images double-counted | decode-hash dedupe | dedupe before sufficiency count | treat as one |

## 8. Output formatting / I-O

| # | Failure | Detection | Mitigation | Fallback |
|---|---|---|---|---|
| F1 | Column order/name drift | schema-derived writer; header assertion | write columns from the single Pydantic source in fixed order | fail fast if header ≠ contract |
| F2 | Input columns not echoed byte-for-byte (`;`, `\|`, Hinglish mangled) | round-trip test on inputs | carry the 4 input fields verbatim; UTF-8 read+write; `csv` `QUOTE_ALL` | never reformat inputs |
| F3 | Row count ≠ 44 / dropped rows on error | count assertion | per-row try/except → safe-default row; never skip | always emit one row per input row |
| F4 | Booleans/sets serialized wrong (`True` vs `true`, list vs `;`-join) | validator + golden compare | serializer maps bool→`true/false`, set→sorted `;`-join, empty→`none` | validator normalizes before write |
| F5 | Enum value outside allowed list leaks out | Pydantic `Literal` + final validator | constrain at generation (strict tool) and validate at write | coerce to nearest allowed or `unknown`; log |

## 9. Pipeline / operational

| # | Failure | Detection | Mitigation | Fallback |
|---|---|---|---|---|
| P1 | API error / rate limit (429/5xx) | SDK exceptions | SDK auto-retry (exp backoff) + bounded manual retry; checkpoint per row | after retries exhausted → safe-default row + `manual_review_required` |
| P2 | Tool-loop runaway (model never finalizes) | iteration counter | hard cap on tool rounds; then force `submit_decision` (tool_choice) | forced final decision on the evidence gathered so far |
| P3 | Cost/latency blow-up | token accounting per row | prompt caching on stable prefix; resize images; cap rounds; cache image bytes | predictable ceiling; report in eval |
| P4 | Cache silently not hitting (prefix invalidated) | check `usage.cache_read_input_tokens` | freeze system+tools+rulebook prefix; no timestamps/UUIDs in prefix; deterministic tool order | functional even if cache misses (just costlier) |
| P5 | Partial run / crash mid-batch | resumable checkpoint (per-row JSONL) | write each row's result to a log as computed; resume from checkpoint | re-run only missing rows |
| P6 | Schema-validation retry loops forever | retry counter | at most 1–2 repair retries on Pydantic failure | then safe-default row |

---

## Cross-cutting safe-default row (the universal fallback)

When any layer fails unrecoverably for a row, emit:
`evidence_standard_met=false`, `evidence_standard_met_reason="automated review could not assess this claim (<cause>)"`, `risk_flags=<specific flags>;manual_review_required`, `issue_type=unknown`, `object_part=unknown` (or claimed part if known), `claim_status=not_enough_information`, `claim_status_justification="routed to manual review"`, `supporting_image_ids=none`, `valid_image=false`, `severity=unknown`.

This guarantees E1 (44 valid rows) and is conservative (never an undeserved "supported"). It is the single most important reliability property of the system.
