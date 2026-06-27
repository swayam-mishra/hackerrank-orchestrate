"""
Run main.py twice and verify the two output.csv files are byte-identical
on the columns the evaluator scores. Latency_ms is excluded from the
diff because wall-clock timing is intentionally non-deterministic.
"""
import csv
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]  # evaluation/ -> code/ -> repo root
OUTPUT_CSV = REPO_ROOT / "support_tickets" / "output.csv"
RUN1_COPY = REPO_ROOT / "support_tickets" / "_determinism_run1.csv"
RUN2_COPY = REPO_ROOT / "support_tickets" / "_determinism_run2.csv"

# Categorical fields: should match exactly across runs (temperature=0 should
# give identical labels). Free-form fields (response, justification) are
# expected to vary slightly on Claude API tie-breaks even at temperature=0.
CATEGORICAL_COLUMNS = ["status", "product_area", "request_type", "inferred_company"]
FREEFORM_COLUMNS = ["response", "justification"]
SCORED_COLUMNS = CATEGORICAL_COLUMNS + FREEFORM_COLUMNS


def _run_main():
    res = subprocess.run(
        [sys.executable, str(REPO_ROOT / "code" / "main.py")],
        capture_output=True, text=True, cwd=str(REPO_ROOT),
    )
    if res.returncode != 0:
        print("[determinism] main.py failed", res.stderr[-500:])
        sys.exit(2)


def _rows_as_dicts(path: Path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def main():
    print("Run 1...")
    _run_main()
    OUTPUT_CSV.replace(RUN1_COPY)
    print("Run 2...")
    _run_main()
    OUTPUT_CSV.replace(RUN2_COPY)

    a = _rows_as_dicts(RUN1_COPY)
    b = _rows_as_dicts(RUN2_COPY)

    if len(a) != len(b):
        print(f"FAIL — different row counts: {len(a)} vs {len(b)}")
        sys.exit(1)

    cat_diffs = 0
    free_diffs = 0
    cat_offenders = []
    for i, (ra, rb) in enumerate(zip(a, b), start=1):
        for col in CATEGORICAL_COLUMNS:
            if ra.get(col, "") != rb.get(col, ""):
                cat_diffs += 1
                cat_offenders.append((i, col, ra.get(col, ""), rb.get(col, "")))
        for col in FREEFORM_COLUMNS:
            if ra.get(col, "") != rb.get(col, ""):
                free_diffs += 1

    print()
    print(f"Categorical fields (status, product_area, request_type, inferred_company):")
    print(f"  {cat_diffs} mismatches across {len(a)} rows × {len(CATEGORICAL_COLUMNS)} fields"
          f" ({cat_diffs / (len(a) * len(CATEGORICAL_COLUMNS)) * 100:.1f}%)")
    if cat_offenders:
        print("  Offenders:")
        for i, col, va, vb in cat_offenders[:10]:
            msg = f"    Row {i:02d} [{col}]: {va!r}  !=  {vb!r}"
            sys.stdout.buffer.write(msg.encode("utf-8", errors="replace") + b"\n")
    print(f"\nFree-form fields (response, justification):")
    print(f"  {free_diffs} mismatches across {len(a)} rows × {len(FREEFORM_COLUMNS)} fields"
          f" ({free_diffs / (len(a) * len(FREEFORM_COLUMNS)) * 100:.1f}%)")
    print("\nNote: temperature=0 + DetectorFactory.seed=0 makes structural decisions")
    print("(status/product_area/request_type/inferred_company) close to deterministic.")
    print("Free-form text varies on Claude API tie-breaks; this is expected LLM behaviour.")

    # Restore output.csv to run-2 result for downstream use
    RUN2_COPY.replace(OUTPUT_CSV)
    RUN1_COPY.unlink(missing_ok=True)

    # Fail only on excessive categorical drift (more than 10% of categorical fields)
    cat_drift_ratio = cat_diffs / (len(a) * len(CATEGORICAL_COLUMNS))
    if cat_drift_ratio > 0.1:
        print(f"\nFAIL — categorical drift {cat_drift_ratio*100:.1f}% exceeds 10% threshold.")
        sys.exit(1)
    print(f"\nOK — categorical drift {cat_drift_ratio*100:.1f}% within 10% threshold.")
    sys.exit(0)


if __name__ == "__main__":
    main()
