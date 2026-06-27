"""Terminal entry point (per the project contract). Delegates to src.main.

  python code/main.py        # read support_tickets/support_tickets.csv -> output.csv

The real batch runner lives in src/main.py; this thin wrapper just puts the
code/ directory on sys.path so the `src` package is importable when the script
is launched directly (e.g. `python code/main.py` from the repo root).
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from src.main import main  # noqa: E402

if __name__ == "__main__":
    main()
