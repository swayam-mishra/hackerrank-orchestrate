"""Evaluation entry point (per the project contract).

  python code/evaluation/main.py                       # score artifacts/sample_predictions.csv vs labels
  python code/evaluation/main.py --pred path/to.csv    # score a specific predictions file
  python code/evaluation/main.py --regression a.csv b.csv   # per-cell diff across all rows

Generate sample predictions first with: python code/main.py --split sample
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))  # code/ on path for `src` + `run_eval`

from dotenv import load_dotenv  # noqa: E402

from run_eval import (  # noqa: E402
    evaluate, format_metrics, read_predictions, regression_diff, repeat_variance,
)
from src.config import load_config  # noqa: E402
from src.io.reader import read_sample_labels  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    ap = argparse.ArgumentParser(description="Score predictions against the labeled sample set.")
    ap.add_argument("--pred", default=None, help="predictions CSV (default artifacts/sample_predictions.csv)")
    ap.add_argument("--regression", nargs=2, metavar=("PREV", "CURR"), help="diff two predictions CSVs")
    ap.add_argument("--variance", nargs="+", metavar="CSV",
                    help="perception-variance: >=2 prediction CSVs from repeated runs -> per-column stability")
    a = ap.parse_args(argv)
    cfg = load_config()

    if a.variance:
        sets = [read_predictions(pathlib.Path(p)) for p in a.variance]
        v = repeat_variance(sets)
        print(json.dumps(v, indent=2, ensure_ascii=False))
        return 0

    if a.regression:
        prev = read_predictions(pathlib.Path(a.regression[0]))
        curr = read_predictions(pathlib.Path(a.regression[1]))
        diffs = regression_diff(prev, curr)
        print(f"{len(diffs)} changed cells across {len(set(prev) | set(curr))} cases.")
        for d in diffs:
            print(f"  {d}")
        return 0

    pred_path = pathlib.Path(a.pred) if a.pred else (cfg.artifacts_dir / "sample_predictions.csv")
    if not pred_path.exists():
        print(f"No predictions at {pred_path}.\nGenerate them first:  python code/main.py --split sample")
        return 1

    labels = read_sample_labels(cfg.sample_csv)
    preds = read_predictions(pred_path)
    metrics = evaluate(preds, labels)
    print(format_metrics(metrics))

    out = cfg.artifacts_dir / "sample_metrics.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(metrics, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nMetrics written to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
