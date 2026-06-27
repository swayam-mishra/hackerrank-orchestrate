"""Single-case debug runner — the primary instrument for error analysis.

  python -m src.cli --case case_008 --split sample --verbose
  python -m src.cli --case case_008 --from-cache       # decision-only, no API (cached facts)

--from-cache re-runs ONLY the pure decision layer on previously cached PerceptionFacts,
so you can iterate on tree.py/evidence.py/etc. instantly with zero API calls.
"""
from __future__ import annotations

import argparse
import json

from dotenv import load_dotenv

from src.config import load_config
from src.decision.assemble import build_decision
from src.io.reader import ClaimInput, read_claims, read_evidence_rules, read_history, read_sample_labels
from src.prompts import build_system_prompt
from src.schema import PerceptionFacts


def _find(claims: list[ClaimInput], case: str) -> ClaimInput | None:
    for c in claims:
        if c.case_id() == case or c.user_id == case:
            return c
    return None


def _tokens(x: str) -> set[str]:
    return {t for t in x.split(";") if t and t != "none"}


_FREE_TEXT = {"evidence_standard_met_reason", "claim_status_justification"}  # not graded


def _eq(col: str, got: str, exp: str) -> bool:
    if col in _FREE_TEXT:
        return True  # free-text: read manually, not diffed
    if col in ("risk_flags", "supporting_image_ids"):
        return _tokens(got) == _tokens(exp)
    return got == exp


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    ap = argparse.ArgumentParser(description="Run one case with full trace.")
    ap.add_argument("--case", required=True, help="case id (e.g. case_008) or user id")
    ap.add_argument("--split", choices=["sample", "test"], default="sample")
    ap.add_argument("--from-cache", action="store_true", help="decision-only on cached facts (no API)")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--model", default=None)
    a = ap.parse_args(argv)

    cfg = load_config(model=a.model)
    claims = read_claims(cfg.sample_csv if a.split == "sample" else cfg.test_csv)
    claim = _find(claims, a.case)
    if claim is None:
        print(f"case '{a.case}' not found in {a.split} split")
        return 1
    history = read_history(cfg.history_csv)
    rules = read_evidence_rules(cfg.evidence_csv)

    if a.from_cache:
        fp = cfg.facts_dir(a.split) / f"{claim.uid()}.json"
        if not fp.exists():
            print(f"no cached facts at {fp}; run the pipeline first (or drop --from-cache).")
            return 1
        facts = PerceptionFacts.model_validate_json(fp.read_text(encoding="utf-8"))
        agent_trace: dict = {"from_cache": True}
    else:
        from src.anthropic_client import build_client, has_api_key
        if not has_api_key():
            print("ANTHROPIC_API_KEY not set. Use --from-cache for decision-only on cached facts.")
            return 2
        from src.agent import run_perception
        from src.perception.ingest import load_images
        system_prompt = build_system_prompt(rules, cfg.prompt_version)
        loaded = load_images(claim, cfg)
        facts, agent_trace = run_perception(claim, loaded, cfg, build_client(cfg), system_prompt)

    decision = build_decision(claim, facts, history.get(claim.user_id), cfg.thresholds)
    row = decision.row
    print(f"=== {claim.case_id()}  ({claim.claim_object}, user={claim.user_id}) ===")
    print(json.dumps(row.to_csv_dict(), indent=2, ensure_ascii=False))

    if a.verbose:
        print("\n--- decision audit ---")
        print(json.dumps(decision.audit, indent=2, default=str))
        print("\n--- perception facts ---")
        print(facts.model_dump_json(indent=2))
        print("\n--- agent trace ---")
        print(json.dumps(agent_trace, indent=2, default=str))

    if a.split == "sample":
        labels = read_sample_labels(cfg.sample_csv).get(claim.case_id())
        if labels:
            got = row.to_csv_dict()
            mism = [(k, got.get(k, ""), v) for k, v in labels.items() if not _eq(k, got.get(k, ""), v)]
            print(f"\n--- vs label: {'ALL MATCH' if not mism else str(len(mism)) + ' mismatch(es)'} ---")
            for k, g, v in mism:
                print(f"  {k}: got={g!r}  exp={v!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
