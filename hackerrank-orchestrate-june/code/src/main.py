"""Batch entry point: read claims.csv -> output.csv (14 columns, exact order).

  python -m src.main                     # full test set -> output.csv
  python -m src.main --split sample      # sample set -> artifacts/sample_predictions.csv
  python -m src.main --model claude-sonnet-4-6   # A/B alternative
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv

from src.anthropic_client import build_client, has_api_key
from src.config import load_config
from src.io.reader import read_claims, read_evidence_rules, read_history
from src.io.writer import write_output
from src.pipeline import run_split
from src.prompts import build_system_prompt


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    ap = argparse.ArgumentParser(description="Multi-modal evidence review batch runner.")
    ap.add_argument("--split", choices=["test", "sample"], default="test")
    ap.add_argument("--model", default=None, help="Override model id (default from config/env).")
    ap.add_argument("--out", default=None, help="Output CSV path.")
    ap.add_argument("--limit", type=int, default=None, help="Process only the first N rows.")
    ap.add_argument("--no-resume", action="store_true", help="Ignore checkpoint and rerun from scratch.")
    ap.add_argument("--from-cache", action="store_true",
                    help="Rebuild output from cached PerceptionFacts (no API; applies decision-logic changes).")
    args = ap.parse_args(argv)

    cfg = load_config(model=args.model)
    csv_path = cfg.sample_csv if args.split == "sample" else cfg.test_csv
    out_path = Path(args.out) if args.out else (
        cfg.output_csv if args.split == "test" else cfg.artifacts_dir / "sample_predictions.csv")

    claims = read_claims(csv_path)
    if args.limit:
        claims = claims[: args.limit]
    history = read_history(cfg.history_csv)

    if args.from_cache:  # deterministic regenerate, no API
        from src.pipeline import decide_from_cache
        rows = decide_from_cache(cfg, claims, history, split=args.split)
        write_output(rows, out_path, expected_count=len(claims))
        print(f"Wrote {len(rows)} rows from cache -> {out_path}")
        return 0

    rules = read_evidence_rules(cfg.evidence_csv)
    system_prompt = build_system_prompt(rules, cfg.prompt_version)

    if not has_api_key():
        print("ERROR: ANTHROPIC_API_KEY is not set (env or code/.env). Cannot run the perception loop.",
              file=sys.stderr)
        print(f"Inputs validated OK: {len(claims)} claims, {len(history)} histories, "
              f"{len(rules)} evidence rules, prompt {cfg.prompt_version}, model {cfg.model}.",
              file=sys.stderr)
        return 2

    client = build_client(cfg)
    rows = run_split(cfg, claims, history, system_prompt, client, split=args.split, resume=not args.no_resume)
    write_output(rows, out_path, expected_count=len(claims))
    print(f"Wrote {len(rows)} rows -> {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
