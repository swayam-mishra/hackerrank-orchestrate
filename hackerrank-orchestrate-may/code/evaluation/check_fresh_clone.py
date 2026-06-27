"""
Simulates the experience of a fresh user landing on the repo. Verifies the
documented setup path works without surprises.

Checks:
  1. Every package imported by the code is in requirements.txt (no implicit deps).
  2. .env.example has the keys the runner reads.
  3. README mentions every entry point we ship.
  4. python -c 'import src.<module>' works for each module without ANTHROPIC_API_KEY set.

Exits non-zero on any failure.
"""
import ast
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]  # evaluation/ -> code/ -> repo root
CODE_DIR = REPO_ROOT / "code"
SRC_DIR = CODE_DIR / "src"
EVAL_DIR = CODE_DIR / "evaluation"
REQUIREMENTS = REPO_ROOT / "requirements.txt"
ENV_EXAMPLE = REPO_ROOT / ".env.example"
README = REPO_ROOT / "README.md"  # single project README lives at the project root

# Modules that ship with Python — we don't expect these in requirements.txt.
STDLIB = {
    "os", "sys", "re", "json", "csv", "time", "math", "threading",
    "datetime", "pathlib", "collections", "concurrent", "subprocess",
    "ast", "typing", "string", "urllib",
}

# First-party code lives in the `src` package; scripts import it as `from src...`.
FIRST_PARTY = {"src"}

# Every .py file we ship (the package + the thin entry point + eval scripts).
PY_FILES = [CODE_DIR / "main.py"] + sorted(SRC_DIR.rglob("*.py")) + sorted(EVAL_DIR.glob("*.py"))

# Leaf modules that must import cleanly with no API key and no network.
IMPORT_ONLY_MODULES = [
    "src.config", "src.pii", "src.prompts",
    "src.decision.taxonomy", "src.decision.confidence", "src.decision.degrade",
    "src.decision.risk_gate", "src.decision.sentiment",
    "src.retrieval.normalize", "src.retrieval.multi_request", "src.retrieval.prefilter",
    "src.validation.validator", "src.validation.output_filter", "src.validation.faithfulness",
    "src.observability.coverage", "src.observability.decision_trace", "src.observability.failures",
]

# Map of import-name → pip-package-name (some differ).
IMPORT_TO_PACKAGE = {
    "anthropic": "anthropic",
    "rank_bm25": "rank-bm25",
    "numpy": "numpy",
    "pandas": "pandas",
    "dotenv": "python-dotenv",
    "tqdm": "tqdm",
    "rich": "rich",
    "langdetect": "langdetect",
    "sentence_transformers": "sentence-transformers",
}


def check(name, ok, detail=""):
    mark = "OK" if ok else "FAIL"
    print(f"  [{mark}] {name}{('  — ' + detail) if detail else ''}")
    return ok


def _imports_from(path: Path) -> set:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except Exception:
        return set()
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for n in node.names:
                out.add(n.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                out.add(node.module.split(".")[0])
    return out


def main():
    print("Fresh-clone simulation\n")
    failures = 0

    # 1. Every external import is in requirements.txt.
    all_imports = set()
    for p in PY_FILES:
        all_imports |= _imports_from(p)
    external = all_imports - STDLIB - FIRST_PARTY
    req_text = REQUIREMENTS.read_text(encoding="utf-8") if REQUIREMENTS.exists() else ""
    missing_in_req = []
    for imp in sorted(external):
        pkg = IMPORT_TO_PACKAGE.get(imp, imp)
        if pkg.lower() not in req_text.lower():
            missing_in_req.append((imp, pkg))
    if not check("All external imports are in requirements.txt",
                 not missing_in_req,
                 f"missing: {missing_in_req}" if missing_in_req else ""):
        failures += 1

    # 2. .env.example has ANTHROPIC_API_KEY.
    env_text = ENV_EXAMPLE.read_text(encoding="utf-8") if ENV_EXAMPLE.exists() else ""
    if not check(".env.example contains ANTHROPIC_API_KEY",
                 "ANTHROPIC_API_KEY" in env_text):
        failures += 1

    # 3. README mentions every entry point.
    readme_text = README.read_text(encoding="utf-8") if README.exists() else ""
    entry_points = ["main.py", "eval.py", "check_determinism.py", "check_submission.py"]
    missing_entry = [e for e in entry_points if e not in readme_text]
    if not check("README references every entry-point script",
                 not missing_entry,
                 f"missing: {missing_entry}" if missing_entry else ""):
        failures += 1

    # 4. Every module is import-clean (no top-level side effects that crash without
    # ANTHROPIC_API_KEY). Run each as a subprocess with cwd=code/ so `src` resolves.
    env = os.environ.copy()
    env.pop("ANTHROPIC_API_KEY", None)
    for m in IMPORT_ONLY_MODULES:
        res = subprocess.run(
            [sys.executable, "-c", f"import {m}"],
            cwd=str(CODE_DIR), env=env, capture_output=True, text=True,
        )
        ok = res.returncode == 0
        if not check(f"import {m} works without ANTHROPIC_API_KEY", ok,
                     res.stderr.strip()[-160:] if not ok else ""):
            failures += 1

    print()
    if failures:
        print(f"{failures} check(s) failed.")
        sys.exit(1)
    print("All fresh-clone checks passed.")
    sys.exit(0)


if __name__ == "__main__":
    main()
