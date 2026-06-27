# HackerRank Orchestrate — Build Plan 2 (Phase 2)

## What changes and why

Phase 1 runs BM25 keyword search → 5 chunks → Claude. It works but misses semantic paraphrases
("card replacement procedure" won't score well against "my card was stolen"). Phase 2 fixes that
with a cross-encoder reranker and trims token cost by sending fewer, higher-quality chunks.

---

## Change 1 — Add cross-encoder reranker to retriever.py

### What to do

1. Add `sentence-transformers` to `requirements.txt` (already listed as Phase 2 dep).

2. In `config.py`, replace `RETRIEVAL_TOP_K = 5` with two constants:
   ```
   RETRIEVAL_TOP_K_BM25 = 20    # BM25 candidates fed to reranker
   RETRIEVAL_TOP_K_FINAL = 3    # reranker keeps this many for LLM
   ```

3. In `retriever.py`, add a `rerank()` method to the `Retriever` class:
   - Load `CrossEncoder("cross-encoder/ms-marco-MiniLM-L-6-v2")` once on `__init__`
   - `rerank(query, chunks)` — scores each (query, chunk["text"]) pair, returns top 3 by score + top score value
   - Update `retrieve()` to use `RETRIEVAL_TOP_K_BM25 = 20` as default top_k

4. In `agent.py`, after `retriever.retrieve()`, call `retriever.rerank()`:
   ```python
   chunks, _ = retriever.retrieve(combined_text, company=norm_company)
   chunks, top_score = retriever.rerank(combined_text, chunks)
   ```
   Pass the reranker's `top_score` to `risk_gate.check()` instead of the BM25 score.
   (Note: risk_gate only checks for 0.0 now, so this is defensive rather than functional.)

### Why this improves quality

BM25 matches words. The cross-encoder reads query and chunk *together* and scores semantic
relevance — "card replacement" correctly scores high against "my card was stolen". The reranker
runs locally (~80MB model, ~50-150ms per ticket on CPU), no API call.

### Interview note

"BM25 gives fast candidate retrieval over 14k chunks. The cross-encoder reranks the top 20
candidates — it's too slow to run on all 14k but fast enough for 20. This is the standard
retrieve-then-rerank pattern used in production RAG systems."

---

## Change 2 — Reduce tokens sent to Claude

### What to do

In `config.py`, add:
```
MAX_TOKENS_RESPONSE = 512    # down from 1024 — responses are 150-300 tokens in practice
```

In `agent.py`, replace `max_tokens=1024` with `max_tokens=MAX_TOKENS_RESPONSE`.

With 3 reranked chunks instead of 5 BM25 chunks, each prompt is ~250-400 fewer input tokens.
Combined with the lower `max_tokens` cap, each API call costs roughly 30-40% less.

### Why this is safe

Observed output responses are 150-300 tokens. JSON with 5 fields never exceeds 400 tokens.
512 gives a 70% buffer above the observed maximum. If a response is truncated, `json.loads()`
will fail and the retry logic handles it.

---

## Change 3 — Parallel ticket processing in main.py

### What to do

Wrap the processing loop in a `ThreadPoolExecutor`. The Anthropic client is thread-safe.

```python
from concurrent.futures import ThreadPoolExecutor, as_completed

MAX_WORKERS = 5   # 5 concurrent API calls — stays within Haiku rate limits
```

Process tickets in parallel, write results in original row order to preserve CSV ordering.

### Why this is safe

- Anthropic Haiku rate limit: 1000 RPM / 100k TPM. At ~56 tickets × ~800 tokens = ~45k tokens
  total, 5 workers in parallel is well within limits.
- The retriever is read-only after init — no write locks needed.
- Results are collected by index and written in order after all futures complete.
- Fallback: if a worker raises, it returns `_escalated("worker_error")` — same as existing error handling.

### Expected speedup

Sequential at ~4s/ticket = ~224s for 56 tickets.
5 workers = ~45s for 56 tickets (~5× faster).

---

## Updated dependencies (requirements.txt additions)

```
sentence-transformers>=2.7.0
```

---

## Build order

1. `config.py` — update TOP_K constants, add MAX_TOKENS_RESPONSE
2. `retriever.py` — add CrossEncoder init + rerank() method, update retrieve() default top_k
3. `agent.py` — wire rerank() call, update max_tokens
4. `main.py` — add ThreadPoolExecutor parallel loop
5. `requirements.txt` — add sentence-transformers

---

## What not to change

- `prefilter.py` — no change
- `risk_gate.py` — no change (already simplified to injection + empty corpus)
- `prompts.py` — no change
- Entry point contract (`code/main.py`) — stays intact

---

## Interview prep additions

| Decision | Why |
|---|---|
| BM25 top 20 → reranker top 3 | BM25 is fast for candidate retrieval; cross-encoder is accurate but slow — standard RAG pattern |
| 3 chunks not 5 | Reranked chunks are higher quality; fewer tokens = cheaper + faster; 3 is enough context for most tickets |
| max_tokens 512 not 1024 | Observed outputs are <300 tokens; 512 is safe headroom with 30% cost reduction |
| 5 parallel workers | Haiku rate limits allow it; 5× speedup with zero quality tradeoff |
| ThreadPoolExecutor not asyncio | Anthropic SDK is sync; ThreadPoolExecutor is the simplest safe parallelism |
