"""
Pre-flight submission readiness check. Verifies every artefact the evaluator
needs is in place and well-formed. Exits non-zero if any check fails.
"""
import csv
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]  # evaluation/ -> code/ -> repo root
CODE_DIR = REPO_ROOT / "code"
DATA_DIR = REPO_ROOT / "data"
TICKETS_DIR = REPO_ROOT / "support_tickets"
OUTPUT_CSV = TICKETS_DIR / "output.csv"
INPUT_CSV = TICKETS_DIR / "support_tickets.csv"

EXPECTED_COLUMNS = ["issue", "subject", "company", "status", "product_area",
                    "response", "justification", "request_type",
                    "inferred_company", "latency_ms"]


def check(name, ok, detail=""):
    mark = "OK" if ok else "FAIL"
    print(f"  [{mark}] {name}{('  — ' + detail) if detail else ''}")
    return ok


def main():
    print("Submission readiness check\n")
    failures = 0

    # 1. Entry point intact
    if not check("code/main.py exists", (CODE_DIR / "main.py").is_file()):
        failures += 1

    # 2. Required modules (package layout under src/)
    required_modules = [
        "src/__init__.py", "src/main.py", "src/agent.py", "src/config.py",
        "src/prompts.py", "src/pii.py",
        "src/retrieval/prefilter.py", "src/retrieval/retriever.py",
        "src/retrieval/normalize.py", "src/retrieval/multi_request.py",
        "src/decision/risk_gate.py", "src/decision/sentiment.py",
        "src/decision/taxonomy.py", "src/decision/confidence.py", "src/decision/degrade.py",
        "src/validation/validator.py", "src/validation/output_filter.py",
        "src/validation/faithfulness.py",
        "src/observability/failures.py", "src/observability/decision_trace.py",
        "src/observability/coverage.py",
        "evaluation/eval.py",
    ]
    for m in required_modules:
        if not check(f"code/{m} exists", (CODE_DIR / m).is_file()):
            failures += 1

    # 3. requirements.txt + .env.example
    if not check("requirements.txt exists",
                 (REPO_ROOT / "requirements.txt").is_file()):
        failures += 1
    if not check(".env.example exists",
                 (REPO_ROOT / ".env.example").is_file()):
        failures += 1
    gitignore = REPO_ROOT.parent / ".gitignore"  # one shared .gitignore at the monorepo root
    env_ignored = False
    if gitignore.is_file():
        env_ignored = ".env" in gitignore.read_text(encoding="utf-8", errors="ignore")
    if not check(".env in .gitignore", env_ignored):
        failures += 1

    # 4. Data corpus
    if not check("data/ exists and non-empty",
                 DATA_DIR.is_dir() and any(DATA_DIR.iterdir())):
        failures += 1

    # 5. Input CSV
    if not check("support_tickets/support_tickets.csv exists", INPUT_CSV.is_file()):
        failures += 1

    # 6. Output CSV present + correct schema
    if OUTPUT_CSV.is_file():
        with open(OUTPUT_CSV, newline="", encoding="utf-8") as f:
            reader = csv.reader(f)
            header = next(reader, [])
            row_count = sum(1 for _ in reader)
        check("output.csv has 10 columns",
              header == EXPECTED_COLUMNS,
              f"got {len(header)} cols: {header}")
        check("output.csv has at least 1 data row", row_count >= 1,
              f"{row_count} rows")
        if header != EXPECTED_COLUMNS or row_count < 1:
            failures += 1
    else:
        check("output.csv exists", False)
        failures += 1

    # 7. README mentions setup + run (single project README at the project root)
    readme = REPO_ROOT / "README.md"
    if readme.is_file():
        text = readme.read_text(encoding="utf-8", errors="ignore").lower()
        check("README.md mentions 'pip install'", "pip install" in text)
        check("README.md mentions 'python code/main.py'", "main.py" in text)
        if "pip install" not in text or "main.py" not in text:
            failures += 1
    else:
        check("README.md exists", False)
        failures += 1

    print()
    if failures:
        print(f"{failures} check(s) failed.")
        sys.exit(1)
    print("All submission checks passed.")
    sys.exit(0)


if __name__ == "__main__":
    main()
