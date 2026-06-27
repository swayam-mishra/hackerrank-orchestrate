import csv
import os
import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

import anthropic
import pandas as pd
from dotenv import load_dotenv
from tqdm import tqdm

from src.config import DATA_DIR, INPUT_CSV, OUTPUT_CSV

load_dotenv()

OUTPUT_COLUMNS = ["issue", "subject", "company", "status", "product_area", "response", "justification", "request_type", "inferred_company", "latency_ms"]

MAX_WORKERS = 5


def main():
    from src.retrieval.retriever import Retriever
    from src.agent import process_ticket
    from src.observability.coverage import reset as reset_coverage
    from src.observability.decision_trace import reset as reset_trace

    if not os.getenv("ANTHROPIC_API_KEY"):
        print("[error] ANTHROPIC_API_KEY not set. Copy .env.example to .env and add your key.")
        sys.exit(1)

    reset_trace()       # truncate decision_trace.jsonl from any previous run
    reset_coverage()    # truncate coverage_gaps.log from any previous run

    print("Loading corpus...")
    retriever = Retriever(str(DATA_DIR))
    print(f"  {len(retriever.chunks)} chunks indexed from {DATA_DIR}")

    client = anthropic.Anthropic()

    df = pd.read_csv(INPUT_CSV)
    total = len(df)

    rows = []
    for row in df.itertuples(index=False):
        rows.append((
            str(row.Issue) if pd.notna(row.Issue) else "",
            str(row.Subject) if pd.notna(row.Subject) else "",
            str(row.Company) if pd.notna(row.Company) else "None",
        ))

    results = {}

    from src.observability.failures import log_failure
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {
            executor.submit(process_ticket, issue, subject, company, retriever, client, i): i
            for i, (issue, subject, company) in enumerate(rows, start=1)
        }
        with tqdm(total=total, desc="Processing", unit="ticket") as pbar:
            for future in as_completed(futures):
                i = futures[future]
                issue, subject, company = rows[i - 1]
                try:
                    result = future.result()
                except Exception as e:
                    log_failure(i, type(e).__name__, str(e), issue[:120])
                    handoff = (
                        f"ESCALATED TO HUMAN AGENT\n\n"
                        f"Reason: Worker error ({type(e).__name__})\n\n"
                        f"Original issue (preview): {issue[:200]}"
                    )
                    result = {
                        "status": "escalated", "product_area": "",
                        "response": handoff,
                        "justification": f"Worker error: {type(e).__name__}.",
                        "request_type": "invalid",
                        "inferred_company": "",
                        "latency_ms": 0,
                    }
                results[i] = (issue, subject, company, result)
                status = result.get("status", "escalated")
                product_area = result.get("product_area", "")
                request_type = result.get("request_type", "invalid")
                tqdm.write(f"  [{i:02d}/{total}] {status} | {product_area or '—'} | {request_type}")
                pbar.update(1)

    counts = {"replied": 0, "escalated": 0}
    by_company = defaultdict(lambda: {"replied": 0, "escalated": 0})
    latencies = []

    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=OUTPUT_COLUMNS)
        writer.writeheader()
        for i in range(1, total + 1):
            issue, subject, company, result = results[i]
            status = result.get("status", "escalated")
            product_area = result.get("product_area", "")
            request_type = result.get("request_type", "invalid")
            latency_ms = result.get("latency_ms", 0)
            writer.writerow({
                "issue": issue,
                "subject": subject,
                "company": company,
                "status": status,
                "product_area": product_area,
                "response": result.get("response", ""),
                "justification": result.get("justification", ""),
                "request_type": request_type,
                "inferred_company": result.get("inferred_company", ""),
                "latency_ms": latency_ms,
            })
            counts[status] = counts.get(status, 0) + 1
            by_company[company][status] = by_company[company].get(status, 0) + 1
            latencies.append((i, latency_ms))

    print(f"\nDone. Output written to {OUTPUT_CSV}")
    print(f"\nSummary:")
    print(f"  Replied:   {counts.get('replied', 0)}")
    print(f"  Escalated: {counts.get('escalated', 0)}")
    print(f"\nBy company:")
    for company, c in sorted(by_company.items()):
        print(f"  {company:12s}  replied={c.get('replied', 0)}  escalated={c.get('escalated', 0)}")

    if latencies:
        ms_values = [ms for _, ms in latencies if ms > 0]
        if ms_values:
            avg_ms = sum(ms_values) // len(ms_values)
            min_ms = min(ms_values)
            max_idx, max_ms = max(latencies, key=lambda x: x[1])
            print(f"\nLatency:")
            print(f"  Avg: {avg_ms} ms  Min: {min_ms} ms  Max: {max_ms} ms (ticket {max_idx})")

    total_in = sum(r.get("_tokens_in", 0) for _, _, _, r in results.values())
    total_out = sum(r.get("_tokens_out", 0) for _, _, _, r in results.values())
    if total_in or total_out:
        api_calls = sum(1 for _, _, _, r in results.values() if r.get("_tokens_in"))
        avg_in = total_in // api_calls if api_calls else 0
        avg_out = total_out // api_calls if api_calls else 0
        # Haiku 4.5 pricing: $1.00 / MTok input, $5.00 / MTok output
        cost = (total_in / 1_000_000) * 1.00 + (total_out / 1_000_000) * 5.00
        print(f"\nTokens:    {total_in:,} in / {total_out:,} out  (avg {avg_in:,} in / {avg_out:,} out per ticket)")
        print(f"Est. cost: ${cost:.4f}  (Haiku 4.5: $1/MTok in, $5/MTok out)")

    filtered_count = sum(1 for _, _, _, r in results.values() if r.get("_filtered"))
    if filtered_count:
        print(f"\nOutput filter: {filtered_count} response(s) had unsupported URLs/phones stripped.")

    confidences = [r.get("_confidence") for _, _, _, r in results.values()
                   if r.get("_confidence") is not None]
    if confidences:
        avg_conf = sum(confidences) / len(confidences)
        buckets = defaultdict(int)
        for _, _, _, r in results.values():
            b = r.get("_confidence_bucket")
            if b:
                buckets[b] += 1
        print(f"\nConfidence: avg {avg_conf:.2f}  "
              f"(high={buckets['high']}, medium={buckets['medium']}, low={buckets['low']})")

    repair_count = sum(1 for _, _, _, r in results.values() if r.get("_repair_attempted"))
    repair_succeeded = sum(1 for _, _, _, r in results.values() if r.get("_repair_succeeded"))
    if repair_count:
        print(f"Validator repairs: {repair_succeeded}/{repair_count} succeeded.")

    phantom_count = sum(
        1 for _, _, _, r in results.values()
        for e in (r.get("_validation_errors") or [])
        if e.startswith("phantom_citation")
    )
    if phantom_count:
        print(f"Phantom citations flagged: {phantom_count}")

    degraded_count = sum(1 for _, _, _, r in results.values() if r.get("_degraded"))
    if degraded_count:
        print(f"Degraded responses (template fallback): {degraded_count}")

    # Faithfulness aggregation
    ratios = [r.get("_faithfulness_ratio") for _, _, _, r in results.values()
              if r.get("_faithfulness_ratio") is not None]
    if ratios:
        avg_faith = sum(ratios) / len(ratios)
        low_faith = sum(1 for x in ratios if x < 0.7)
        print(f"\nFaithfulness: avg ratio {avg_faith:.2f}  ({low_faith} ticket(s) below 0.7)")

    # Citation presence
    cited = sum(1 for _, _, _, r in results.values() if r.get("_has_citation"))
    replied = sum(1 for _, _, _, r in results.values() if r.get("status") == "replied")
    if replied:
        print(f"Citations: {cited}/{replied} replied responses cite a source file")


if __name__ == "__main__":
    main()
