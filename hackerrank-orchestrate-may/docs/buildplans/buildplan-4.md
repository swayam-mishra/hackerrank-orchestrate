# buildplan-4.md — Phase 4 (Production Hardening + Evaluation)

## Context

Phase 1–3 produced a working agent: 28/29 replied, 1/29 correctly escalated, ~37s, ~$0.07/run,
with citations, sentiment-aware tone, multi-request handling, and full observability.

What's still missing is *production muscle*: reliability under transient API failures, safety
around unsupported claims and PII, structured information for human agents handling
escalations, and — most critically — **a way to actually measure accuracy before submission**.

Phase 4 is performance/efficiency + safety + measurement. No retrieval changes, no prompt
restructuring. All five additions are layered on top of the stable Phase 3 pipeline.

Total estimated effort: ~170 min (~3h).

---

## Final file structure

```
code/
├── main.py            ← updated: eval-mode summary, output-filter stats
├── agent.py           ← updated: backoff, output filter call, warm-handoff fields
├── prompts.py         ← unchanged
├── prefilter.py       ← unchanged
├── normalize.py       ← unchanged
├── retriever.py       ← unchanged
├── risk_gate.py       ← unchanged
├── sentiment.py       ← unchanged
├── config.py          ← unchanged
├── failures.py        ← updated: PII redaction before write
├── pii.py             ← NEW: regex-based PII redactor
├── output_filter.py   ← NEW: unsupported URL/phone scrubber
├── eval.py            ← NEW: run against sample_support_tickets.csv, report accuracy
└── README.md          ← updated: Phase 4 results table
support_tickets/
├── output.csv          ← unchanged schema
├── failed_tickets.log  ← entries are now PII-redacted
└── eval_results.md     ← NEW: accuracy report from eval.py
```

---

## Change 1 — Warm handoff for escalated tickets (30 min)

### What it does

Currently escalated rows have a generic `"This ticket has been escalated to a human agent."`
response with `product_area=""`. Phase 4 fills the `response` with structured handoff context
so the human agent doesn't start from zero.

### Approach

Modify `_escalated()` in `agent.py` to accept optional `issue`, `chunks`, and `extra_context`
parameters. When called, build a structured response:

```
ESCALATED TO HUMAN AGENT

Reason: <one-line escalation reason>

Original issue (preview): <first 200 chars>

Retrieved documentation (top sources):
  - <basename of source_file 1>
  - <basename of source_file 2>
  - <basename of source_file 3>

Agent reasoning: <justification>
```

For risk-gate escalations (injection, empty corpus): no chunks available, just reason + issue
preview.
For API/parse failure escalations: chunks may be available (retrieve happened before LLM
call); include them.
For Claude-decided escalations: Claude's own response text stays; we *append* the source
filenames so the human can verify quickly.

### Files

- `code/agent.py` — `_escalated()` signature + body; call sites updated to pass `issue` and
  `chunks` where available
- `code/main.py` — worker-error fallback in main.py also calls `_escalated()` shape

### Why this matters for scoring

Direct hit on evaluation criterion: *"warm handoff — when escalating, pass the full context,
retrieved docs, and agent reasoning to the human agent so they don't start from zero."*

---

## Change 2 — PII redaction before logging (30 min)

### What it does

Strips emails, phone numbers, and long random-looking IDs from anything written to
`failed_tickets.log` or printed to the console with ticket content. Cybersecurity hygiene —
support tickets routinely contain customer PII.

### Approach

New `code/pii.py`:

```python
import re

EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE = re.compile(r"(?:\+\d{1,3}[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}")
LONG_ID = re.compile(r"\b[A-Za-z0-9_-]{20,}\b")  # cs_live_xxxx, session tokens, UUIDs

def redact(text: str) -> str:
    if not text:
        return text
    text = EMAIL.sub("[EMAIL]", text)
    text = PHONE.sub("[PHONE]", text)
    text = LONG_ID.sub("[ID]", text)
    return text
```

`failures.py` — apply `redact()` to `message` and `issue_preview` before writing the JSONL line.

`agent.py` — apply `redact()` to the API-error console print (`print(f"[agent] API error...")`).

### Files

- `code/pii.py` — NEW
- `code/failures.py` — import + redact
- `code/agent.py` — redact API-error print

### What we don't redact

The `output.csv` itself. The grader expects to see the original ticket → response mapping;
PII redaction there would corrupt the evaluation. PII redaction is for *logs*, not *outputs*.

---

## Change 3 — Exponential backoff on rate limits (20 min)

### What it does

Currently any `anthropic.*Error` → immediate `_escalated()`. Transient rate limits and
connection errors should retry with backoff before giving up.

### Approach

Modify the existing `for attempt in range(2)` loop in `agent.py` to `range(3)` with three
classes of exception handling:

```python
import time

for attempt in range(3):
    try:
        message = client.messages.create(...)
        # ... parse, return on success
    except json.JSONDecodeError as e:
        if attempt == 2:
            return _escalated(...)
        # else: retry immediately
    except (anthropic.RateLimitError, anthropic.APIConnectionError,
            anthropic.APIStatusError) as e:
        if attempt == 2:
            return _escalated(f"API error after retries: {type(e).__name__}.", t0, ...)
        time.sleep(2 ** attempt)  # 1s, 2s, 4s
    except Exception as e:
        return _escalated(...)  # unknown errors: escalate immediately
```

### Files

- `code/agent.py` — modify the retry loop only

### Why backoff vs immediate escalate

Rate limits are transient by definition. Escalating a ticket to human review because Claude
hit a 1-second rate-limit dip is operationally wrong. With 3 attempts at 1s/2s/4s backoff,
total worst-case added latency per ticket is 7 seconds — well within the 5-worker pool
budget.

---

## Change 4 — Output filter for hallucinated URLs and phones (45 min)

### What it does

Scans every generated `response` for URLs and phone numbers. Any URL or phone that does NOT
appear in the retrieved chunks is treated as hallucinated and stripped from the response.
Notes the strip in `justification` so the grader sees we caught it.

### Approach

New `code/output_filter.py`:

```python
import re

URL = re.compile(r"https?://[^\s<>\"\)\]]+")
PHONE = re.compile(r"(?:\+\d{1,3}[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}")

def find_unsupported(response: str, chunks: list) -> dict:
    chunk_blob = " ".join(c["text"] for c in chunks)
    return {
        "urls": set(URL.findall(response)) - set(URL.findall(chunk_blob)),
        "phones": set(PHONE.findall(response)) - set(PHONE.findall(chunk_blob)),
    }

def scrub(response: str, unsupported: dict) -> str:
    for url in unsupported["urls"]:
        response = response.replace(url, "[unsupported URL removed]")
    for phone in unsupported["phones"]:
        response = response.replace(phone, "[unsupported phone removed]")
    return response
```

In `agent.py` after `json.loads(raw)`:

```python
unsupported = find_unsupported(result["response"], chunks)
if unsupported["urls"] or unsupported["phones"]:
    result["response"] = scrub(result["response"], unsupported)
    result["justification"] += f" [Output filter: removed {len(unsupported['urls'])} unsupported URL(s), {len(unsupported['phones'])} phone(s).]"
    result["_filtered"] = True
```

Track `_filtered` count in `main.py` summary.

### Files

- `code/output_filter.py` — NEW
- `code/agent.py` — apply filter after parse, before return
- `code/main.py` — sum `_filtered` flag, print in summary

### Why strip vs flag

Stripping is more aggressive but addresses the eval criterion *"avoid unsupported claims or
hallucinated policies"* directly. The `[unsupported URL removed]` placeholder + justification
note keeps the grader transparent about what happened.

---

## Change 5 — Eval mode against sample CSV (45 min) — **MOST IMPORTANT**

### What it does

Runs the agent against `sample_support_tickets.csv` (10 labeled rows) and reports
column-by-column accuracy. This is the only way to measure actual quality before submission.

### Approach

New `code/eval.py`:

```python
import csv
import pandas as pd
from pathlib import Path
import anthropic
from dotenv import load_dotenv

from agent import process_ticket
from retriever import Retriever

load_dotenv()

REPO_ROOT = Path(__file__).parent.parent
SAMPLE_CSV = REPO_ROOT / "support_tickets" / "sample_support_tickets.csv"
RESULTS_MD = REPO_ROOT / "support_tickets" / "eval_results.md"

def normalize(s):
    return str(s).strip().lower() if s else ""

def main():
    retriever = Retriever(str(REPO_ROOT / "data"))
    client = anthropic.Anthropic()
    df = pd.read_csv(SAMPLE_CSV)

    matches = {"status": 0, "product_area": 0, "request_type": 0}
    mismatches = []

    for i, row in enumerate(df.itertuples(index=False), start=1):
        result = process_ticket(
            str(row.Issue) if pd.notna(row.Issue) else "",
            str(row.Subject) if pd.notna(row.Subject) else "",
            str(row.Company) if pd.notna(row.Company) else "None",
            retriever, client, ticket_idx=i,
        )
        for col, expected, actual in [
            ("status", row.Status, result.get("status", "")),
            ("product_area", getattr(row, "Product Area", ""), result.get("product_area", "")),
            ("request_type", getattr(row, "Request Type", ""), result.get("request_type", "")),
        ]:
            if normalize(expected) == normalize(actual):
                matches[col] += 1
            else:
                mismatches.append({
                    "row": i, "column": col,
                    "expected": expected, "actual": actual,
                    "issue": str(row.Issue)[:80],
                })

    total = len(df)
    print(f"Eval on {total} sample tickets:")
    for col in ["status", "product_area", "request_type"]:
        pct = 100 * matches[col] / total
        print(f"  {col:14s} {matches[col]}/{total}  ({pct:.1f}%)")

    # Write detailed mismatches to markdown
    with open(RESULTS_MD, "w", encoding="utf-8") as f:
        f.write(f"# Eval results on {total} tickets\n\n")
        for col in ["status", "product_area", "request_type"]:
            pct = 100 * matches[col] / total
            f.write(f"- **{col}**: {matches[col]}/{total} ({pct:.1f}%)\n")
        f.write("\n## Mismatches\n\n")
        for m in mismatches:
            f.write(f"- Row {m['row']} `{m['column']}`: expected `{m['expected']}`, got `{m['actual']}` — issue: {m['issue']}\n")

if __name__ == "__main__":
    main()
```

### What it doesn't measure

`response` and `justification` are free-form text. Auto-evaluating them requires either:
- semantic similarity (embedding cosine — too noisy on short text)
- LLM-as-judge (slow + circular)
- BLEU/ROUGE (string overlap — bad for paraphrase)

We skip auto-eval on those columns and write side-by-side to `eval_results.md` for human
review during the interview prep window.

### Files

- `code/eval.py` — NEW
- `support_tickets/eval_results.md` — generated output

### How to run

```bash
python code/eval.py
```

Optional: add a flag to `main.py` for eval mode (`python code/main.py --eval`) — but a separate
script is simpler and matches the "entry-point contract preserved" rule.

---

## Build order (lowest risk first → highest value)

1. **Change 5 (eval mode)** — establish baseline accuracy on sample CSV BEFORE any other
   change. We need a number to compare against.
2. **Change 3 (backoff)** — pure resilience, zero behaviour change on the happy path.
3. **Change 2 (PII redaction)** — observability only, doesn't touch output.csv.
4. **Change 1 (warm handoff)** — affects escalated rows only (1 row currently). Re-run eval to
   confirm no regression on `replied` rows.
5. **Change 4 (output filter)** — affects every replied row (highest blast radius). Re-run
   eval immediately after to measure impact.
6. **Final eval run** — capture before/after numbers for README + interview narrative.

---

## Verification

1. **After Change 5**: `python code/eval.py` prints baseline accuracy. Capture the numbers.
2. **After Change 3**: full `python code/main.py` run; confirm 28/1 split + ~37s runtime; no
   regression in clean runs.
3. **After Change 2**: trigger a synthetic failure (e.g. ticket with email "test@example.com")
   and confirm `failed_tickets.log` shows `[EMAIL]` not the literal address.
4. **After Change 1**: inspect the escalated row in `output.csv`; confirm `response` has
   structured handoff context including source filenames.
5. **After Change 4**: re-run `eval.py`; confirm sample CSV still passes, plus check 1-2
   responses for stripped placeholders if Claude hallucinated URLs.
6. **Final eval run**: write Phase 4 results to README. Compare:
   - Baseline (Phase 3): X% status accuracy, Y% product_area accuracy
   - Phase 4 final: X'% status, Y'% product_area
   - Filter stats: N URLs stripped, M phones stripped across the run

---

## Interview prep — what Phase 4 buys you

| Decision | Why |
|---|---|
| Backoff before escalating | Transient rate limits aren't human-review tickets. 7s worst-case retry < 1 wasted human handoff. |
| PII redaction in logs only, not outputs | Logs are operational; outputs are evaluator-facing. Redacting outputs corrupts the eval. |
| Strip hallucinated URLs/phones | Direct hit on "avoid unsupported claims" criterion. Filter is post-LLM, deterministic, inspectable. |
| Warm handoff with source filenames | Even when the agent escalates, the human agent gets the retrieval result so they don't start from zero. |
| Eval mode against sample CSV | Only way to know your real accuracy before submission. Captures pre-Phase-4 vs post-Phase-4 delta. |
| Skipping LLM-as-judge for response/justification | Circular and slow. Human review during interview prep is more honest. |

---

## What not to change

- `prefilter.py`, `retriever.py`, `risk_gate.py`, `prompts.py`, `sentiment.py`, `normalize.py`,
  `config.py` — all stable from Phase 3
- Output CSV schema — still 10 columns
- Entry point `code/main.py` — preserved
