import os
import pathlib
import sys
from collections import defaultdict

import anthropic
import pandas as pd
from dotenv import load_dotenv

# Put code/ on sys.path so the `src` package is importable when run as a script.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from src.config import DATA_DIR, SAMPLE_CSV, SUPPORT_TICKETS_DIR  # noqa: E402

load_dotenv()

RESULTS_MD = SUPPORT_TICKETS_DIR / "eval_results.md"


def _norm(value) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return str(value).strip().lower()


def main():
    from src.retrieval.retriever import Retriever
    from src.agent import process_ticket

    if not os.getenv("ANTHROPIC_API_KEY"):
        print("[error] ANTHROPIC_API_KEY not set. Copy .env.example to .env and add your key.")
        sys.exit(1)

    print("Loading corpus...")
    retriever = Retriever(str(DATA_DIR))
    print(f"  {len(retriever.chunks)} chunks indexed")

    client = anthropic.Anthropic()
    df = pd.read_csv(SAMPLE_CSV)
    total = len(df)

    matches = {"status": 0, "product_area": 0, "request_type": 0}
    by_company_matches = defaultdict(lambda: {"status": 0, "product_area": 0,
                                               "request_type": 0, "total": 0})
    repair_attempted = 0
    repair_succeeded = 0
    phantom_count = 0
    confidences = []
    mismatches = []
    side_by_side = []

    for i, (_, row) in enumerate(df.iterrows(), start=1):
        issue = str(row["Issue"]) if pd.notna(row["Issue"]) else ""
        subject = str(row["Subject"]) if pd.notna(row["Subject"]) else ""
        company = str(row["Company"]) if pd.notna(row["Company"]) else "None"

        result = process_ticket(issue, subject, company, retriever, client, ticket_idx=i)

        expected_status = row.get("Status", "")
        expected_pa = row.get("Product Area", "")
        expected_rt = row.get("Request Type", "")
        expected_resp = row.get("Response", "")

        actual_status = result.get("status", "")
        actual_pa = result.get("product_area", "")
        actual_rt = result.get("request_type", "")
        actual_resp = result.get("response", "")

        co = company.strip().lower() or "none"
        by_company_matches[co]["total"] += 1
        for col, exp, act in [
            ("status", expected_status, actual_status),
            ("product_area", expected_pa, actual_pa),
            ("request_type", expected_rt, actual_rt),
        ]:
            if _norm(exp) == _norm(act):
                matches[col] += 1
                by_company_matches[co][col] += 1
            else:
                mismatches.append({
                    "row": i, "column": col,
                    "expected": str(exp), "actual": str(act),
                    "issue": issue[:100],
                })

        if result.get("_repair_attempted"):
            repair_attempted += 1
            if result.get("_repair_succeeded"):
                repair_succeeded += 1
        if result.get("_confidence") is not None:
            confidences.append(result["_confidence"])
        for ve in result.get("_validation_errors") or []:
            if ve.startswith("phantom_citation"):
                phantom_count += 1

        side_by_side.append({
            "row": i,
            "issue": issue,
            "expected_response": str(expected_resp)[:300] if expected_resp else "",
            "actual_response": actual_resp[:300] if actual_resp else "",
        })

        print(f"  [{i:02d}/{total}] status={actual_status}  pa={actual_pa or '—'}  rt={actual_rt}")

    print(f"\nEval on {total} sample tickets:")
    for col in ["status", "product_area", "request_type"]:
        pct = 100 * matches[col] / total if total else 0
        print(f"  {col:14s} {matches[col]}/{total}  ({pct:.1f}%)")

    print(f"\nPer-company:")
    for co, c in sorted(by_company_matches.items()):
        if c["total"] == 0:
            continue
        print(f"  {co:12s}  status={c['status']}/{c['total']}  pa={c['product_area']}/{c['total']}  rt={c['request_type']}/{c['total']}")

    if confidences:
        avg_conf = sum(confidences) / len(confidences)
        print(f"\nConfidence avg: {avg_conf:.2f}")
    if repair_attempted:
        print(f"Validator repairs: {repair_succeeded}/{repair_attempted} succeeded")
    if phantom_count:
        print(f"Phantom citations flagged: {phantom_count}")

    with open(RESULTS_MD, "w", encoding="utf-8") as f:
        f.write(f"# Eval results — {total} sample tickets\n\n")
        f.write("## Accuracy\n\n")
        for col in ["status", "product_area", "request_type"]:
            pct = 100 * matches[col] / total if total else 0
            f.write(f"- **{col}**: {matches[col]}/{total} ({pct:.1f}%)\n")
        f.write("\n## Mismatches\n\n")
        if not mismatches:
            f.write("_None._\n")
        for m in mismatches:
            f.write(f"- Row {m['row']} `{m['column']}`: expected `{m['expected']}`, got `{m['actual']}`\n")
            f.write(f"  - Issue: {m['issue']}\n")
        f.write("\n## Response side-by-side (first 300 chars)\n\n")
        for sbs in side_by_side:
            f.write(f"### Row {sbs['row']}\n")
            f.write(f"**Issue:** {sbs['issue'][:200]}\n\n")
            f.write(f"**Expected:** {sbs['expected_response']}\n\n")
            f.write(f"**Actual:** {sbs['actual_response']}\n\n")
            f.write("---\n\n")

    print(f"\nDetailed report written to {RESULTS_MD}")


if __name__ == "__main__":
    main()
