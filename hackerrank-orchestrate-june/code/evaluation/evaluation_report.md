# Evaluation Report

## 1. Methodology

`dataset/sample_claims.csv` (20 labeled rows) is the only labeled data; `dataset/claims.csv`
(44 rows) is unlabeled. **Every accuracy number is DIRECTIONAL at n=20 — no confidence
intervals or significance tests.** Effort is therefore weighted toward the rule-based layers
(which generalize by construction) and toward proving the system is image-grounded.

How to (re)produce the numbers:

```bash
python code/main.py --split sample --no-resume       # fresh live perception -> sample_predictions.csv
python code/evaluation/main.py                       # per-column + per-class recall + confusion + mismatches
python code/evaluation/main.py --variance a.csv b.csv  # run-to-run stability across repeated live runs
python -m evaluation.grounding_tests --n 6           # blank-drop + image-swap
```

Metrics computed (`evaluation/run_eval.py`):
- **Exact-match** accuracy for the fixed enums/bools: `claim_status`, `evidence_standard_met`,
  `valid_image`, `issue_type`, `object_part`, `severity` (+ a 3×3 `claim_status` confusion matrix
  **and per-class precision/recall/F1** — so the rare/costly classes the aggregate hides, e.g.
  `contradicted` recall, are first-class numbers).
- **Perception-variance** (`--variance`): per-column run-to-run stability across repeated live runs,
  so a non-deterministic-perception swing isn't mistaken for a real gain.
- **Set-based** (order-insensitive Jaccard + precision/recall/F1, and exact-set rate) for the
  multi-label `risk_flags` and `supporting_image_ids`.
- **Free-text** (`evidence_standard_met_reason`, `claim_status_justification`) is **not
  auto-graded** — read manually. **LLM-as-judge is intentionally not used** (self-preference,
  poor calibration at this scale).
- **Regression diff** (`evaluation/main.py --regression prev.csv curr.csv`): per-cell diff across
  all rows (sample + test) on every logic change, to catch unintended changes even where accuracy
  isn't measurable.
- **Manual error analysis**: read every mismatch (tractable at n=20); root-cause by layer; prefer
  rule fixes over prompt tweaks. Use `python -m src.cli --case <id> --verbose` (or `--from-cache`).

### Visual-grounding tests (mandatory)
- **blank-drop** — re-run with images replaced by a blank image; outputs should collapse toward
  `not_enough_information` / `valid_image=false`. If they don't, the system leans on text priors (bug).
- **image-swap** — re-run each claim with another claim's images; outputs should change
  (`wrong_object` / `claim_mismatch` / different `issue_type`). If stable, perception isn't driving (bug).

## 2. Current validation status (deterministic layer)

### Per-column accuracy on the labeled sample (n=20 — DIRECTIONAL; no confidence intervals)

**Prompt v4 — two independent LIVE perception runs** (this prompt produces the current `output.csv`):

| column | run 1 | run 2 | run-to-run stability |
|---|---|---|---|
| claim_status | 90% | 95% | 95% (only `case_004` flips) |
| evidence_standard_met | 100% | 100% | 100% |
| valid_image | 90% | 90% | 100% |
| object_part | 90% | 90% | 100% |
| issue_type | 70% | 75% | 95% |
| severity | 70% | 75% | 95% |
| risk_flags (mean F1) | 0.82 | 0.86 | 80% exact-set |
| supporting_image_ids (mean F1) | 0.93 | 0.97 | 95% |

Per-class `claim_status` recall (what the aggregate hides): **`contradicted` 80% (4/5) in BOTH runs** (vs 60% on the v3 baseline), `supported` 92–100%, `not_enough_information` 100%.

> **Honest reading (n=20; two live runs + a `--variance` check):**
> - **Real, stable gains:** `claim_status` 85% (v3) → **90–95%**, and **`contradicted` recall 60% → 80%** — confirmed in both runs, with `claim_status` 95% run-to-run stable (only one genuine boundary row, `case_004`, jitters). These are the high-value wins: supported↔contradicted is the costliest error class.
> - **Flat — NOT claimed as a gain:** `issue_type` and `severity` are 70–75% across both runs vs 75/70 on v3 — within n=20 noise. The v4 few-shot exemplars + severity recalibration did **not** measurably move them; reported, not asserted.
> - **Perception is stable** (not the feared ±15%): 95–100% run-to-run on every column except `risk_flags` (80% exact-set — expected multi-label quality-flag jitter on borderline rows). Reproduce: `python code/evaluation/main.py --variance run_a.csv run_b.csv`.

**Also verified without the API:**

- **115 unit tests pass** (`pytest -q`) covering schema invariants, the decision tree (all branches +
  the contradiction-signal ordering), the sample rows as fixtures, the history overlay (additive-only)
  + per-driver MRR attribution, evidence/aggregate/severity logic, the visual-cue + confidence +
  cross-image gates, the self-consistency merge, the perceptual-hash fingerprint + deterministic
  injection screen, byte-exact I/O echo, and the quality gate.
- **`case_008` end-to-end via cached facts** (`cli --case case_008 --from-cache`) reproduces the
  label exactly on all graded columns: `contradicted`, `valid_image=false`, `broken_part`,
  `front_bumper`, `severity=high`, `risk_flags=claim_mismatch;non_original_image;user_history_risk;manual_review_required`,
  `supporting=img_1`. This is the case that distinguishes our NEI gate (`evidence_standard_met`)
  from the prescribed `valid_image` rule, and confirms the history overlay does not flip status.

Determinism boundary (stated honestly): the decision tree, evidence/consistency/severity logic,
risk scoring, and output validation are **pure code** and deterministic. VLM perception (Opus 4.8)
is **not** deterministic (no temperature). Raw `PerceptionFacts` are cached per case, so decision-logic
changes are re-evaluated offline and deterministically (`--from-cache`).

## 3. Operational analysis (cost / latency / rate limits) — MEASURED

<!-- OPERATIONAL_METRICS:START (generated from run_metrics.json) -->
_MEASURED from `code/artifacts/run_metrics.json` (model `claude-opus-4-8`). Regenerate with `python -m src.observability --split <split>`._

- **Rows:** 44 processed, 0 errored, 0 safe-default.
- **Model calls:** 221 total — mean 5.02, median 6.0, p95 9.0 per row.
- **Tokens:** input(uncached) 294,939; output 210,051; cache-read 2,404,517; cache-write 110,411 (total input incl. cache 2,809,867).
- **Images processed:** 82.
- **Cost (USD):** **$8.6184 total** = input $1.4747 + output $5.2513 + cache-write $0.6901 + cache-read $1.2023; **$0.1959/claim**. Prices/MTok: {'input': 5.0, 'output': 25.0, 'cache_write': 6.25, 'cache_read': 0.5}.
- **Prompt caching (is it working?):** **85.6% of input tokens served from cache** (2,404,517 cache-read tok) — within-row breakpoint is effective.
- **Latency / runtime:** ~801s wall-clock (measured, concurrency 4); per row mean 68.31s, median 44.19s, p95 169.95s; throughput ~3.3 rows/min. (A `--split <split>` backfill that lacks the run's wall-clock reports the *summed* per-row time, ~3005s, instead — the concurrent wall-clock is the live-run figure.)
- **Rate limits / retries:** escaped 429s 0 (SDK internal retries not counted); in-loop validation retries 4.
- **Operational mix:** manual_review_required rate 80% (drivers {'history': 29, 'cross_image': 3, 'injection': 8, 'authenticity': 5, 'perception_disagreement': 10} — history-driven is label-required, the rest is the tunable automation gap); claim_status distribution {'supported': 19, 'contradicted': 19, 'not_enough_information': 6}; error classes none.

**Caveats (unchanged):** numbers above are for the multi-round agent loop. The **Batch API 50% discount does NOT apply** to a multi-round tool loop (only single-shot calls). Caching is within-row: the system+tools prefix alone (~1.7k tok) is below Opus 4.8's 4096-token minimum, so cross-row prefix caching does not kick in; the high cache-read % above comes from reusing the image-bearing first turn across tool rounds.
<!-- OPERATIONAL_METRICS:END -->

### Strategies (design)
- **Concurrency**: bounded thread pool (`config.concurrency`, default 4) — within standard tier RPM/TPM at this scale.
- **Retry**: SDK auto-retry (exp backoff) on 429/5xx (`api_max_retries=4`); per-row checkpoint (resumable); bounded tool rounds + forced finalize prevent runaway loops.
- **Caching**: cache breakpoint at the end of the image-bearing first user turn → tool-loop rounds 2+ reuse it at ~0.1× (effectiveness shown by the cache-read % above).
- **Token control**: client-side image resize (no silent server downsizing); context images at 1568 px with full-res detail on demand via `inspect_image`; bounded rounds; decoded bytes reused.

## 5. Likely high-impact failure categories (where misses will cluster)
1. `contradicted` vs `supported` on severity/nature mismatch (`claim_mismatch`) — the subtlest call.
2. `not_enough_information` vs `contradicted` at the evidence-sufficiency boundary (claimed part not shown).
3. Exact `object_part` / `issue_type` on the spec values unseen in the sample (`glass_shatter`,
   `missing_part`, `taillight`, …).
4. `risk_flags` set completeness (multi-label).
5. `supporting_image_ids` selection on multi-image rows.

Generalization rests on the rule layers (decision tree, invariants, risk mapping, evidence-rule
selection, enum constraints) plus the grounding tests — not on 20-row accuracy.
