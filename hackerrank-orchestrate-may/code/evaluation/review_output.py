"""
Manual review helper: prints a one-line per-row summary of output.csv plus
unique values for the categorical columns and a flag for any rows that look
unusual (escalated, _degraded, _filtered, low confidence trace).
"""
import json
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]  # evaluation/ -> code/ -> repo root
OUTPUT = REPO_ROOT / "support_tickets" / "output.csv"
TRACE = REPO_ROOT / "support_tickets" / "decision_trace.jsonl"


def main():
    df = pd.read_csv(OUTPUT)
    print(f"Total rows: {len(df)}")
    print(f"Status counts: {df['status'].value_counts().to_dict()}\n")

    # Decision-trace sidecar lookup
    trace_by_idx = {}
    if TRACE.exists():
        with open(TRACE, encoding="utf-8") as f:
            for line in f:
                try:
                    e = json.loads(line)
                    trace_by_idx[e["ticket_idx"]] = e
                except Exception:
                    pass

    print("Per-row summary:")
    print("idx | status     | product_area                   | request_type    | conf  | issue")
    print("-" * 110)
    for i, row in df.iterrows():
        idx = i + 1
        issue = str(row["issue"])[:60].replace("\n", " ").replace("\r", " ")
        e = trace_by_idx.get(idx, {})
        conf = e.get("retrieval", {}).get("confidence", "?")
        flags = []
        if row["status"] == "escalated":
            flags.append("ESC")
        if "_degraded" in e.get("final", {}) and e["final"].get("degraded"):
            flags.append("DEGR")
        if e.get("output_filter", {}).get("urls_stripped", 0) > 0:
            flags.append("FILT")
        if e.get("validation", {}).get("errors"):
            errs = e["validation"]["errors"]
            if any(x.startswith("phantom_citation") for x in errs):
                flags.append("PHCITE")
        flag_str = ",".join(flags) or "-"
        print(f"  {idx:02d} | {row['status']:9s} | {str(row['product_area'])[:30]:30s} | "
              f"{str(row['request_type']):15s} | {conf:>5} | [{flag_str}] {issue}")

    print()
    print("Unique product_area:", sorted(df['product_area'].fillna('').unique().tolist()))
    print("Unique request_type:", sorted(df['request_type'].fillna('').unique().tolist()))
    inferred = df['inferred_company'].astype(str).str.strip()
    print(f"inferred_company filled: {(inferred != '').sum()} rows")


if __name__ == "__main__":
    main()
