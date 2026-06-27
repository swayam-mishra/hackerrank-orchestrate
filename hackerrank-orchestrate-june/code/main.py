"""Project terminal entry point (per the project contract). Delegates to src.main.

  python code/main.py                  # produce output.csv for dataset/claims.csv
  python code/main.py --split sample   # run the labeled dev set
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from src.main import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
