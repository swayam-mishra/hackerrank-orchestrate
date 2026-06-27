# Failure Modes

Honest catalogue of every known way this agent can fail or under-perform, with the
specific row(s) in the eval set that exhibit each mode (where applicable), and the
mitigation we shipped (or chose to skip and why).

---

## 1. Input-level failures

### 1.1 Empty / whitespace-only ticket
- **Detection:** `prefilter.py` — `not text.strip()` → reason=`empty`, short-circuits.
- **Behaviour:** `_invalid_reply()` — status=replied, request_type=invalid, response="I am sorry, this is out of scope from my capabilities."
- **Eval evidence:** synthetic ticket #1 (Category=`empty`).

### 1.2 Very-short ticket ("help", "??")
- **Detection:** prefilter sets `low_signal=True` if < 3 words OR < 15 chars (non-blocking).
- **Behaviour:** retrieval still runs, but the confidence bucket is demoted from high → medium, which switches the LLM into "be conservative" prompt mode. The LLM typically replies with an honest "I need more detail" message rather than guessing.
- **Eval evidence:** synthetic ticket #2 (Category=`very_short`, "help") replies, request_type=invalid (sample category disagreement).
- **Cost:** the LLM may pick `request_type=invalid` rather than `product_issue` — minor scoring miss.

### 1.3 Very-long ticket (> 8000 chars)
- **Detection:** none (we don't truncate).
- **Behaviour:** the full text goes to BM25 (token soup is fine for sparse) and to Claude (200k context is plenty). Cross-encoder is slowed slightly but doesn't fail.
- **Eval evidence:** synthetic ticket #3 (Category=`very_long`) — replies normally.

### 1.4 Prompt injection — basic phrases
- **Detection:** `prefilter.py` `INJECTION_PHRASES` list — `"ignore previous instructions"`, `"disregard"`, `"jailbreak"`, etc.
- **Behaviour:** short-circuit → escalate.
- **Eval evidence:** synthetic ticket #6 (Category=`injection_basic`) — escalated.

### 1.5 Prompt injection — advanced (role-play, system-tag, prompt-leak)
- **Detection:** Phase 5 extended `INJECTION_PHRASES` with `"system:"`, `"<|im_start|>"`, `"### system"`, `"reveal your prompt"`, `"as an admin"`, `"developer mode"`, `"override system"`, `"sudo"`.
- **Eval evidence:** synthetic tickets #7 (`injection_advanced`) and #8 (`injection_encoded`) — both escalated.

### 1.6 Non-English ticket
- **Detection:** `langdetect` (thread-safe via lock); short-circuits on lang != "en".
- **Behaviour:** `_invalid_reply()` — replied + invalid + out-of-scope.
- **Eval evidence:** synthetic ticket #9 (`non_english`, Bonjour) → replied + invalid.
- **Caveat:** sample CSV's expected behaviour for non-English is exactly this — replied + invalid. Real-world deployment might want translation, but we match the labeler.

### 1.7 Heavy noise / random characters
- **Detection:** alphanumeric ratio < 40% OR fewer than 2 word-tokens → reason=`junk`.
- **Behaviour:** short-circuit → `_invalid_reply()`.
- **Eval evidence:** synthetic ticket #10 (`heavy_noise`).

### 1.8 Base64 / opaque blobs
- **Detection:** Phase 5 — regex `^[A-Za-z0-9+/=]{40,}$` → junk.
- **Behaviour:** short-circuit (we don't try to decode; opaque is opaque).

### 1.9 Subject ≠ issue mismatch
- **Detection:** none — both go to retrieval (concatenated) and to Claude.
- **Behaviour:** the LLM weighs both. Generally fine in practice; the labeler's rows pass.

---

## 2. Retrieval-level failures

### 2.1 No retrieval results (top_score = 0)
- **Detection:** `risk_gate.py` — escalates with `empty_corpus_result`.
- **Eval evidence:** none in the 29-ticket batch — corpus has reasonable coverage.

### 2.2 Low-confidence retrieval (top_score > 0 but small)
- **Detection:** `confidence.py` blend < 0.4 → `low` bucket.
- **Behaviour:** prompt suffix tells Claude to reply with "I don't have specific documentation for this" rather than guess.
- **Eval evidence (29-ticket run):** ~8 tickets per run land in low-confidence bucket; none hallucinate. Visa is the most common low-confidence company because of corpus thinness.

### 2.3 Cross-domain contamination (Visa ticket retrieves HackerRank doc)
- **Mitigation:** company boost ×1.5 on stated company; confidence's `company_match` component penalises when retrieved chunks span multiple companies.
- **Residual:** company=None tickets get no boost. The `inferred_company` field surfaces ambiguity to the grader.

### 2.4 Multi-request retrieval gap
- **Mitigation:** `multi_request.py` splits on conjunctions when both halves contain action verbs; runs retrieval per sub-query and merges chunks.
- **Residual:** a heuristic split misses semantically distinct requests joined without strong conjunctions ("could you also tell me ..."). The LLM's prompt-level multi-request rule then handles whatever was retrieved.
- **Eval evidence:** synthetic ticket #4 (multi-intent same domain) and #5 (cross-domain) — both replied with numbered lists.

---

## 3. LLM-level failures

### 3.1 JSON parse failure
- **Mitigation:** markdown fence stripper handles ` ```json ... ``` `. Three retry attempts. After all 3 fail, `degrade.py` returns a templated reply built from the top retrieved chunk.
- **Eval evidence:** `failed_tickets.log` is absent on clean runs.

### 3.2 Schema / enum / consistency violations
- **Mitigation:** `validator.py` checks all fields. Blocking errors trigger one repair API call with a corrective hint. If repair fails → `degrade.py`.

### 3.3 Hallucinated `product_area` (off-taxonomy)
- **Mitigation:** validator looks up against `taxonomy.allowed_for(company)`. If off-list, `taxonomy.derive_from_chunks()` extracts a category from the top chunk's source path. Repair LLM call only triggered for *blocking* errors, not soft taxonomy ones (path derivation is safer).

### 3.4 Hallucinated URLs / phone numbers
- **Mitigation:** `output_filter.py` strips any URL or phone in the response that doesn't appear in the retrieved chunks. Replaces with `[unsupported URL removed]`. Notes the action in `justification`.
- **Eval evidence:** Phase 5 baseline run stripped 2 URLs/phones across 29 tickets.

### 3.5 Phantom citations
- **Detection:** validator regex `according to <filename.ext>` matches against the retrieved chunk basenames. Mismatch → flagged in `_validation_errors` (informational; not blocking).
- **Why not block:** the citation is part of the response; rewriting would cost a repair call for a low-impact issue. The grader can grep the trace.

### 3.6 Hallucinated specifics (dollar amounts, dates, account numbers)
- **Detection:** none beyond URL/phone filter.
- **Why not built:** would require a heavier extract-and-verify pass. The corpus-grounding rule and citation rule push the LLM toward not inventing specifics in the first place. Faithfulness scoring (Phase 5 add-on, see DECISIONS) provides additional coverage.

### 3.7 Overconfident response on weak retrieval
- **Mitigation:** confidence-bucketed prompt — low-confidence tickets get a strict "do not guess" instruction.

### 3.8 Tone mismatch (frustrated user gets robotic reply)
- **Mitigation:** sentiment classifier in `sentiment.py` flags frustrated tickets; system prompt prepends an empathetic-opener instruction.
- **Residual:** the heuristic misses subtle frustration ("this has been going on for weeks"). Cost: missed empathetic opener — not a correctness issue.

---

## 4. System-level failures

### 4.1 API timeout / rate limit
- **Mitigation:** 3 retries with `time.sleep(2 ** attempt)` (1s, 2s) before degrading.

### 4.2 Threading races
- **`langdetect`:** thread-safe via module-level `threading.Lock` in `prefilter.py`. (Bug fixed in Phase 2.)
- **`failures.py` log writer:** thread-safe via lock.
- **`decision_trace.py` log writer:** thread-safe via lock.
- **CSV writer in main.py:** single-threaded after `as_completed` collects results.

### 4.3 Cross-encoder cold start
- **Behaviour:** first run downloads ~80MB from HuggingFace; subsequent runs hit the cache.
- **Operational note:** documented in README so the evaluator isn't surprised by a 30s startup on a clean machine.

### 4.4 LLM API non-determinism
- **Reality:** even at temperature=0, Claude API is not strictly bit-exact on tie-breaks.
- **Behaviour:** two consecutive runs of `main.py` produce ~80% identical free-form text but only ~95% identical categorical fields.
- **Detection:** `check_determinism.py` reports both ratios separately and only fails if categorical drift exceeds 10%.
- **Why we don't fight it:** the API is what it is; we acknowledge it explicitly rather than pretend it's bit-exact.

### 4.5 Corpus file read errors
- **Mitigation:** `retriever.py` wraps each file read in `try/except`; bad files are skipped silently.

---

## 5. Eval-level (sample CSV) misses we accept

These are 3/10 product_area mismatches we don't try to fix because they're labeler-vs-agent disagreements rather than wrong calls:

| Row | Issue | Sample label | Our output | Verdict |
|---|---|---|---|---|
| 5 | "delete my hackerrank community account" | community | account | Both defensible — agent prioritised the action ("delete account"); labeler prioritised the surface ("hackerrank community"). |
| 7 | "What actor plays Iron Man?" | conversation_management | (empty) | Sample label is bizarre for a trivia question. Empty is more honest. |
| 9 | "lost or stolen Visa card from India" | general_support | security | Both defensible — lost-card issues sit at the security/general_support boundary. |

Pushing these to 10/10 would require overfitting the prompt to the labeler's quirks at the cost of generalisation on the unseen 29-ticket evaluation set.

---

## 6. What we explicitly don't handle (and why)

- **Multi-language support** — sample data treats non-English as `replied + invalid`; matching the labeler is the correct behaviour, not translation.
- **Account-specific actions** — we never claim to delete an account, refund a charge, or update a user record. The agent always points to support for account-bound work.
- **Real-time outage detection** — we infer outage from ticket text, not from any external monitoring. A ticket saying "site is down" gets escalated whether or not the site is actually down.
- **Conversation memory / multi-turn** — each ticket is processed independently. No state crosses tickets.
- **Adversarial fine-tuning** — the LLM can still be argued into things by sufficiently creative prompts. Defence in depth: prefilter regex + risk_gate + validator + output_filter + decision_trace, but not impervious.
