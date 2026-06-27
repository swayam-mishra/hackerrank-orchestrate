# ENGINEERING_CONVENTIONS.md — binding rules for Stage B

These conventions are **binding** for the implementation and override any looser habit. They exist to keep the build modular, maintainable, and fast to debug at n=20-row error-analysis speed. Read alongside IMPLEMENTATION_PLAN §0 (layout) and DECISION_ENGINE (the logic these modules implement).

---

## 1. One responsibility per module; small, single-purpose functions
Follow IMPLEMENTATION_PLAN §0 layout **exactly** — one concern per file. A function does one thing; if it needs a paragraph to name, split it. No "utils.py" junk drawers; name modules for their concern.

## 2. The deterministic layers are PURE functions
`decision/evidence.py`, `decision/consistency.py`, `decision/aggregate.py`, `decision/tree.py`, `decision/severity.py`, `risk/history.py` are **pure**: typed facts in → typed result out.
- **No** file I/O, **no** API calls, **no** network, **no** clock/random, **no** global/module state inside them.
- They must be runnable in a unit test and re-runnable on **cached** `PerceptionFacts` with **zero** API calls. This is what makes decision-logic changes free to iterate (EVALUATION_STRATEGY §2).
- Side effects (reading CSVs/images, calling Anthropic, writing the trace) live only in `perception/`, `agent.py`, `io/`, and `pipeline.py`.

## 3. `PerceptionFacts` — the ONE seam between perception and decision
A single typed Pydantic model is the **only** contract the decision layer consumes. Downstream code consumes `PerceptionFacts`, **never** a raw Anthropic response object. That seam is the whole game for testability.

- **Lives in:** `src/schema.py` (the typed-contracts module), alongside the output contract `OutputRow`. Both are "the data contracts"; keeping them in one small module is one responsibility (define the contracts), not two.
- **Produced by:** `agent.py` (maps the `submit_decision` tool args + quality-gate signals + parsed claim into `PerceptionFacts`). Raw SDK objects never leave the agent layer.
- **Consumed by:** every `decision/*` function and the pipeline. `risk/history.py` takes the history row **separately** — see the deliberate exclusion below.

**Shape (typed; no loose dicts):**
```python
class ImageFact(BaseModel):           # one per submitted image
    image_id: str                     # "img_1"
    usable: bool                      # decodable & not unusable-blurry
    quality_flags: list[QualityFlag]  # blurry_image | cropped_or_obstructed | low_light_or_glare | wrong_angle
    authenticity: Authenticity        # original | non_original | possible_manipulation
    relevant_to_claim: bool           # shows the claimed object/part region
    visible_object: str               # VLM-identified object class
    visible_part: ObjectPart          # per-object enum | unknown
    visible_issue_type: IssueType     # enum | none | unknown
    visible_severity: Severity        # none|low|medium|high|unknown (soft)
    visual_cue: str                   # the named, locatable cue grounding the read ("" if none)
    image_text: str                   # transcribed in-image text (data, never instruction)

class PerceptionFacts(BaseModel):     # the seam — NO history inside (see below)
    user_id: str
    claim_object: ClaimObject         # car | laptop | package (input, trusted)
    claimed_part: ObjectPart          # parsed from convo | unknown
    claimed_issue_family: IssueFamily # parsed | unknown
    claim_text_instruction_present: bool
    images: list[ImageFact]
    # aggregate VLM read (cross-image):
    object_matches_claim: Tri         # true | false | unknown
    part_assessable: bool             # claimed part clearly visible & evaluable in ≥1 image
    visible_issue_type: IssueType     # aggregate
    visible_object_part: ObjectPart   # aggregate
    severity_estimate: Severity
    vlm_confidence: float             # 0..1 (soft; biases abstention only)
    contradiction_signals: list[ContradictionSignal]  # wrong_object | wrong_object_part | claim_mismatch
```
- **Deliberate exclusion:** `PerceptionFacts` contains **no user-history data**. The decision tree therefore *cannot* read history — which is how we provably enforce "history never overrides clear visual evidence" (DECISION_ENGINE §11, case_017). History enters only via `risk/history.py` as an **additive overlay** after `claim_status` is decided.
- Enums (`IssueType`, `ObjectPart`, …) are imported from the same `schema.py` `Literal`s used by `OutputRow` and the `submit_decision` schema — one definition, no drift.

## 4. Types everywhere; typed models, never loose dicts
Type hints on **every** signature. Pass data as Pydantic models / dataclasses. Loose dicts crossing module boundaries are banned — they are the #1 debugging time-sink. (Internal-only, short-lived dicts inside one function are fine.)

## 5. All tunables in `config.py` — zero magic numbers
Model id, image long-edge (2576 / context 1568), round cap (≤6), retry/backoff, blur/brightness thresholds, history numeric thresholds (rejection-rate ≥0.4, ≥4 claims/90d, review-rate ≥0.4), concurrency, paths, **prompt version string**. Logic references `config.X`; no literals scattered in branches. `config` is passed in explicitly (see §7), not imported as a global at use sites in pure functions — pure functions receive the specific thresholds they need as typed params or a small frozen `Thresholds` model.

## 6. Per-row structured audit trace (JSONL) — first-class artifact
One JSONL record per row, written by `pipeline.py`, capturing: `request_id`, model, token usage (incl. `cache_read_input_tokens`), **every tool call + its result**, the **cited visual cue**, the **decision-tree branch that fired**, the **matched evidence rule id**, **invariants applied**, and **final overrides**. Plus the full `PerceptionFacts` (so the row is re-decidable offline). When a row is wrong, the trace names the layer to fix in seconds. Secrets are never written (redaction on).

## 7. Explicit dependency passing — no singletons/globals
`client` (Anthropic) and `config` are constructed once in `main.py`/`cli.py` and **passed explicitly** down the call chain. No module-level Anthropic client, no global config, no hidden state. Every component can be constructed and run in isolation in a test (inject a fake/recorded client). Pure functions take only their typed inputs.

## 8. Debug CLI (`src/cli.py`) — used constantly in error analysis
`python -m src.cli --case case_008 [--verbose] [--from-cache] [--sample|--test]`:
- runs the full pipeline for **one** case id;
- `--verbose` prints the full trace (tool calls, cue, branch, matched rule, invariants, before/after of each post-check) and the final row vs. the label (if sample);
- `--from-cache` re-runs **only the deterministic decision layer** on cached `PerceptionFacts` (no API call) — instant iteration on `tree.py`/`evidence.py`/etc.
This is the primary instrument for EVALUATION_STRATEGY §3 (read every mismatch).

## 9. GUARDRAIL — do NOT over-abstract
Modularity here = **obvious, well-named files + pure functions + typed contracts**. It does **NOT** mean base classes, plugin systems, DI frameworks, registries, or interfaces/ABCs for things with a single implementation. No "strategy pattern" for one decision tree; no abstract `Tool` hierarchy for three tools (plain functions + a small dispatch dict). Premature abstraction is a maintainability tax and is on the project reject list. **Flat and obvious beats clever indirection.** If a layer of indirection doesn't remove a concrete, present pain, don't add it.

---

### How these map to the planned modules (no new concepts, just sharper seams)
- `schema.py` — `OutputRow` **and** `PerceptionFacts` + all `Literal` enums + invariants (§3, §4).
- `agent.py` — owns the SDK; emits `PerceptionFacts`; raw responses never escape (§3, §7).
- `decision/*`, `risk/history.py` — pure functions over `PerceptionFacts`/history/thresholds (§2).
- `config.py` — all tunables, passed explicitly (§5, §7).
- `pipeline.py` — orchestration + JSONL trace (§6).
- `cli.py` — single-case debug runner with `--from-cache` (§8).  ← **new file vs the original §0 layout**

Two additions to IMPLEMENTATION_PLAN §0: `src/cli.py` (debug runner) and the explicit `PerceptionFacts` model in `schema.py`. Everything else in the plan already complies.
