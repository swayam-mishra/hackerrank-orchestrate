# HackerRank Orchestrate — Build Plan

## What you're building

A terminal-based support triage agent that reads 56 support tickets from a CSV, retrieves relevant documentation from a local corpus, and writes structured predictions (reply vs escalate, category, response, justification) to an output CSV.

No web calls. No hallucination. Corpus-grounded only.

---

## Final file structure

```
repo/
├── code/
│   ├── main.py          ← entry point (terminal CLI, do not rename)
│   ├── retriever.py     ← BM25 + cross-encoder reranker
│   ├── risk_gate.py     ← hard escalation rules, no LLM
│   ├── prefilter.py     ← adversarial/junk/multilingual detection
│   ├── agent.py         ← orchestrates everything, calls Claude API
│   ├── prompts.py       ← all prompt strings live here only
│   ├── config.py        ← thresholds, constants, model names
│   └── README.md        ← setup + how to run + design decisions
├── support_issues/
│   ├── sample_support_issues.csv   ← 108 labeled rows (reference)
│   ├── support_issues.csv          ← 56 input rows (what you run against)
│   └── output.csv                  ← your predictions go here
├── data/
│   ├── hackerrank/      ← 438 docs (already scraped)
│   ├── claude/          ← 320 docs (already scraped)
│   └── visa/            ← 14 docs (thin — handle carefully)
├── .env                 ← ANTHROPIC_API_KEY (never commit)
├── .env.example         ← template (commit this)
├── requirements.txt
└── AGENTS.md            ← do not touch
```

---

## Build order

Build in this exact order. Each step has no dependency on the next.

### Step 1 — config.py

Constants only. No logic.

```
ESCALATION_KEYWORDS = [fraud, hack, breach, outage, down, identity theft, 
                       legal, lawsuit, unauthorized, security vulnerability]
RETRIEVAL_TOP_K_INITIAL = 20     # BM25 retrieves this many
RETRIEVAL_TOP_K_FINAL = 5        # reranker keeps this many
SCORE_THRESHOLD_HACKERRANK = 0.3
SCORE_THRESHOLD_CLAUDE = 0.3
SCORE_THRESHOLD_VISA = 0.5       # higher threshold = more conservative = fewer hallucinations
CLAUDE_MODEL = "claude-haiku-4-5-20251001"
RERANKER_MODEL = "cross-encoder/ms-marco-MiniLM-L-6-v2"  # phase 2 only
EMBEDDING_TEMP = 0               # always 0, non-negotiable
```

---

### Step 2 — prefilter.py

Runs before retrieval. Pure python, no API calls, no LLM.

**What it detects:**
- Junk / garbage characters (random symbols, no real words)
- Non-English text (French, Hindi, etc.) — classify as `invalid`, still reply with "out of scope"
- Prompt injection attempts ("ignore previous instructions", "you are now", "disregard")
- Completely empty or whitespace-only tickets

**What it returns:**
```python
{
  "is_valid": bool,
  "reason": str,        # "junk" | "injection_attempt" | "non_english" | "empty" | "ok"
  "should_shortcircuit": bool   # if True, skip retrieval entirely
}
```

**Why this exists:** the sample data has French tickets and adversarial inputs. handling them before retrieval means the LLM never sees garbage, and your output CSV has clean `invalid` classifications instead of wrong answers.

---

### Step 3 — retriever.py

Local search over the corpus. No API calls.

**How it works:**

1. On init, loads all `.md` and `.txt` files from `data/` recursively
2. Chunks each file at 500 chars with 100 char overlap
3. Tags each chunk with `{text, source_file, company}`
4. Builds a BM25Okapi index over all chunks

**retrieve() method:**
1. Takes `query`, `company` (optional), `top_k=5`
2. Scores all chunks with BM25
3. If company is known, applies ×1.5 score multiplier to matching company chunks (boost, not hard filter — important for Visa where corpus is thin)
4. If company is None, run unfiltered BM25 across all three corpora — no boost applied
5. Returns top 5 by final score + the top score value

**Phase 2 only — rerank() method (add after core pipeline works):**
1. Change top_k to 20 in retrieve()
2. Run cross-encoder (`cross-encoder/ms-marco-MiniLM-L-6-v2`) on each chunk paired with the query
3. Return top 5 by reranker score + the top reranker score value
4. Add `sentence-transformers` to requirements.txt when doing this

**Why BM25 first:** get a working end-to-end run, verify output quality on the sample CSV, then layer the reranker in. cleaner build, better interview narrative.

---

### Step 4 — risk_gate.py

Hard rules. No LLM. Runs after retrieval but before the Claude call.

**Inputs:** `issue_text`, `prefilter_result`, `top_bm25_score`, `company`

**Escalation triggers (in order):**

1. Prefilter flagged injection attempt → `escalated`, `invalid`
2. Any escalation keyword found in issue text → `escalated`
3. Top BM25 score below threshold for that company → `escalated` (corpus can't support an answer)
4. Company is Visa AND BM25 score below 0.5 → `escalated` (conservative Visa handling)

**Returns:**
```python
{
  "should_escalate": bool,
  "reason": str,
  "status": "escalated" | None   # None means proceed to LLM
}
```

**Why no LLM here:** LLMs can be manipulated. A ticket that says "this is not fraud, just help me" could bypass an LLM escalation check. Rules can't be manipulated.

---

### Step 5 — prompts.py

All prompt strings live here. Nothing hardcoded in agent.py.

**System prompt (the main one):**
```
You are a support triage agent for {company}. 

You must answer ONLY using the provided documentation excerpts below. 
Do not use any knowledge outside of these excerpts.
If the excerpts do not contain enough information to answer confidently, say so.

Respond in valid JSON only. No markdown. No explanation outside the JSON.

Output format:
{
  "status": "replied" | "escalated",
  "product_area": "<category or empty string if escalated>",
  "response": "<user-facing answer>",
  "justification": "<1-2 sentences explaining your decision>",
  "request_type": "product_issue" | "feature_request" | "bug" | "invalid"
}

Rules:
- response must be grounded in the excerpts, not your training data
- if the ticket is out of scope for {company}, set request_type to "invalid"
- product_area should match the support category (e.g. screen, account, travel_support)
- if status is "escalated", product_area must be an empty string
- justification must reference the documentation, not general knowledge
- if company is unknown, infer the best company from the ticket content before answering
```

---

### Step 6 — agent.py

Orchestrates everything. One function: `process_ticket(issue, subject, company)`.

**Flow:**
```
1. prefilter(issue + subject)
   └─ if shortcircuit → return immediately with invalid classification

2. retriever.retrieve(query, company, top_k=5) → top 5 chunks + top BM25 score

3. risk_gate(issue, prefilter_result, top_bm25_score, company)
   └─ if escalate → return escalated with blank product_area

4. build prompt: system prompt + retrieved chunks + ticket
   └─ call Claude API (temperature=0, model=claude-haiku-4-5-20251001)
   └─ parse JSON response

5. return structured result dict
```

**Error handling:**
- API call fails → escalate with reason "api_error"
- JSON parse fails → retry once, then escalate
- Any unhandled exception → escalate, log the error

---

### Step 7 — main.py

Terminal CLI. Entry point. This is what the evaluator runs.

**Behavior:**
```bash
python code/main.py
```

- Reads `support_issues/support_issues.csv`
- Shows a progress bar per ticket (use `rich` or `tqdm`)
- Prints a one-line summary per ticket as it processes: `[12/56] replied | screen | product_issue`
- Writes results to `support_issues/output.csv` as it goes (not at the end — so partial runs are recoverable)
- At the end, prints a summary: total replied, total escalated, breakdown by company

**Output CSV columns (exact, 7 columns matching sample_support_issues.csv):**
`issue, subject, company, status, product_area, response, justification, request_type`

output.csv does not exist yet — main.py creates it. row-by-row writing handles this automatically.

---

### Step 8 — README.md (inside code/)

Must include:
1. Setup: `pip install -r requirements.txt`, copy `.env.example` to `.env`, add API key
2. How to run: `python code/main.py`
3. Design decisions section — explain BM25 + reranking choice, escalation logic, Visa gap handling
4. Known failure modes — where the agent breaks (multilingual edge cases, very short tickets, ambiguous company)
5. Dependencies listed with versions

---

## Dependencies (requirements.txt)

Phase 1 (build this now):
```
anthropic>=0.25.0
rank-bm25>=0.2.2
pandas>=2.0.0
python-dotenv>=1.0.0
tqdm>=4.66.0
rich>=13.0.0
langdetect>=1.0.9
```

Phase 2 (add only after core pipeline works):
```
sentence-transformers>=2.7.0
```

## .env.example (commit this file)

```
ANTHROPIC_API_KEY=your_key_here
```

---

## Escalation reference (from sample data)

Only escalate for:
- Site-wide outages ("none of the pages are accessible")
- Active fraud / identity theft claims
- Security vulnerability reports
- High-risk account disputes (hacked account, unauthorized access)
- Visa tickets where corpus score is too low

Do NOT escalate:
- Out-of-scope questions ("What actor plays Iron Man?") → `replied` + `invalid`
- Feature requests → `replied` + `feature_request`
- Junk/gibberish → `replied` + `invalid`
- Multilingual tickets → `replied` + `invalid`

---

## Interview prep — decisions you need to defend

| Decision | Why |
|---|---|
| BM25 + reranker over pure embeddings | deterministic, no GPU, reranker adds semantic precision without FAISS complexity |
| Risk gate before LLM | LLMs can be manipulated, rules cannot |
| Visa score threshold at 0.5 vs 0.3 | corpus has 14 files vs 438 — higher bar prevents hallucination |
| Company boost not hard filter | hard filtering Visa corpus (14 files) would leave too many tickets with zero results |
| Prefilter before retrieval | keeps garbage out of the LLM context, saves API cost, cleaner invalid classifications |
| temperature=0 | determinism is a scoring criterion |
| Write output row by row | partial runs are recoverable, safer for a 24h window |

---

## What not to do

- Do not call any external URLs at runtime — corpus is local only
- Do not hardcode the API key anywhere
- Do not rename `code/main.py`
- Do not let the LLM decide escalation — that's risk_gate.py's job
- Do not generate responses without retrieved chunks in the prompt
- Do not commit `.env`