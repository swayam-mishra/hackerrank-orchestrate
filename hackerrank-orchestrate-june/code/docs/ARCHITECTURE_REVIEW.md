# ARCHITECTURE_REVIEW.md — Phase 5

The architecture is **already decided** (not re-derived here): a **Claude tool-use agent loop wrapped in deterministic pre- and post-checks**.

```
                 ┌──────────────────── per claim row ────────────────────┐
 claims.csv ─►   │  DETERMINISTIC PRE-CHECKS                              │
 (+ history,     │   • ingest & resize images (≤2576px long edge)        │
  rulebook)      │   • quality & authenticity gate (per image)           │
                 │   • claim parse (target part + claimed condition)     │
                 ├───────────────────────────────────────────────────────┤
                 │  AGENTIC PERCEPTION LOOP (Claude, adaptive thinking)   │
                 │   live tools (model-driven, bounded rounds):          │
                 │     - inspect_image(image_id, focus_area[, bbox])     │
                 │   (evidence rulebook is BAKED into the cached system  │
                 │    prompt — not a live tool; user-history is a CODE    │
                 │    post-check overlay, not a tool)                    │
                 │   finalize → submit_decision(...)  [strict schema]    │
                 ├───────────────────────────────────────────────────────┤
                 │  DETERMINISTIC POST-CHECKS                             │
                 │   • evidence-sufficiency check (code)                  │
                 │   • object/part consistency validator                 │
                 │   • user-history risk scoring (code)                  │
                 │   • decision tree → claim_status (code)               │
                 │   • Pydantic schema validation + repair               │
                 └───────────────────────────────────────────────────────┘  ─► output.csv
```

Key division of labor: **the VLM produces *observations* (perception facts); deterministic code produces the *decision*.** Evidence-sufficiency and user-history risk scoring are deterministic code, not LLM judgment. A single multimodal call with no tools and no deterministic gating is **rejected** (fails explainability, auditability, injection resistance) — not re-debated.

---

## 1. Why this beats the alternatives (3–5 bullets, grounded in spec requirements)

- **Explainability & auditability (spec E5, "justifications grounded in the images"; operational E7).** Every decision is traceable to a logged tool call, a named visual cue, a matched evidence rule, and a deterministic invariant/override. A single black-box multimodal call can assert "supported" but cannot be audited or unit-tested. The decision tree is pure code → reproducible and inspectable.
- **Determinism where it's allowed (spec "deterministic where possible").** Opus 4.8 perception can't be pinned (no temperature; §Phase 0). Putting `claim_status`, evidence-sufficiency, and risk scoring in **code** makes the decision *deterministic given the logged perception facts* — the only honest determinism available. A pure single-model approach is non-deterministic end-to-end.
- **Injection resistance (THREAT_MODEL).** Because the decision is computed by deterministic post-checks from VLM *observations*, an instruction embedded in an image or claim can at worst perturb an observation — it cannot reach `claim_status`. A single ungated call lets injected text directly steer the verdict.
- **Targeted re-examination without heavy deps.** `inspect_image` gives the model a deterministic crop/zoom of the **original full-resolution** image so sub-threshold detail (hairline cracks, small scratches) can be re-checked — addressing the #1 vision failure (false negatives) without an object detector.
- **Robustness & cost control (FAILURE_MODES P1–P4).** Pre-checks catch corrupt/oversized images before spending tokens; post-checks guarantee a valid row even on model error; bounded rounds + prompt caching bound cost. A pure-deterministic-only system (no VLM) **cannot do visual perception at all** — it's a non-starter for "images are the primary source of truth."

**Rejected alternatives, explicitly:** (i) *single multimodal call* — fails the three spec pillars above; (ii) *pure deterministic / classical CV* — cannot read arbitrary damage from photos, can't generalize to unseen issue types; (iii) *LLM-as-judge for the decision* — non-auditable and uncalibrated at this scale.

---

## 2. Implementation constraints for this architecture (lean, dependency-light)

| Constraint | Decision | Rationale |
|---|---|---|
| `inspect_image` backing | Deterministic **crop/zoom of the ORIGINAL full-res image** by named region (e.g. `front_bumper`, `top_left`) or model-supplied coordinates, re-encoded and returned as a new image block | Lets the model re-examine detail it under-saw; no detector needed |
| **No open-vocabulary detector** (Grounding DINO etc.) | Excluded | VLM + deterministic crop is sufficient; a detector adds heavy/GPU deps that hurt portability/reproducibility |
| Image resize | Resize client-side so the long edge ≤ **2576 px** (Opus 4.8 native max; verified June 2026, **not** the stale 1568) and never exceed 4784 visual tokens; never rely on silent server downsizing | Preserves detail at known cost; keeps crop coordinates meaningful |
| Severity | VLM estimate with explicit `unknown` abstention | Soft signal; **no** bounding-box area-ratio / detector geometry |
| Structured finalize | `submit_decision` tool with `strict: true`, `additionalProperties:false`, schema generated from the Pydantic source | Guarantees enum-valid, parseable output |
| Determinism boundary | perception = non-deterministic; sufficiency/risk/decision/validation = deterministic code | Honest determinism claim |

---

## 3. Genuine risks WITHIN this architecture (and mitigations)

| Risk | Mechanism | Mitigation |
|---|---|---|
| **Tool-loop runaway** (model keeps calling tools, never finalizes) | open-ended agent loop | **Hard iteration cap** (e.g. ≤ 6 tool rounds / row). On cap, make one final call with `tool_choice={"type":"tool","name":"submit_decision"}` and thinking off to force a clean structured decision from evidence gathered so far. |
| **Schema drift / invalid finalize** | model emits a value/shape outside the contract | `strict:true` tool + Pydantic `Literal` validation; on failure, **1–2 repair retries** with the validation error fed back; then safe-default row. |
| **Latency from multiple round-trips** | each tool round is a live API call (the multi-round loop **cannot** use the Batch API's 50% discount — that applies only to single-shot requests) | **Prompt caching** on the stable prefix (system prompt + tool defs + evidence_requirements table ≥4096 tokens) so each round re-reads the prefix at ~0.1×; **cap rounds**; **resize images**; reuse decoded image bytes; process rows concurrently within rate limits. |
| **Cache silently not hitting** | a timestamp/UUID/var in the prefix invalidates it (FAILURE_MODES P4) | freeze the prefix (no volatile bytes), deterministic tool order, verify `usage.cache_read_input_tokens > 0` in the eval report. |
| **Forced-tool + thinking interaction** | forcing a specific tool while adaptive thinking is on can conflict | default loop runs `tool_choice=auto` with thinking on; only the *final* forced `submit_decision` call disables thinking — sidesteps any incompatibility and is robust regardless. |
| **Over-trusting VLM perception** | hallucinated/missed damage | `inspect_image` re-check; require a named, locatable cue for `supported`; abstain to NEI on low confidence; blank-drop/image-swap tests prove pixel-grounding. |
| **Determinism over-claim** | implying end-to-end determinism | docs + eval state explicitly: deterministic *given perception facts*, not end-to-end. |

---

## 4. Where each spec pillar is satisfied

- *Images are primary source of truth* → perception loop + `inspect_image`; decision requires a visual cue to support.
- *History adds context, never overrides* → risk scoring is an **additive overlay** in post-checks; the decision tree never reads history to set `supported/contradicted` (DECISION_ENGINE).
- *Deterministic where possible* → all of sufficiency, risk, decision, validation are code.
- *Explainable* → full per-claim audit log (request_id, model, tokens, every tool call + result, VLM rationale, applied rule, override logic).

This architecture is the minimum structure that satisfies all four pillars; nothing in it is present that doesn't prevent a concrete failure mode catalogued in FAILURE_MODES / THREAT_MODEL.
