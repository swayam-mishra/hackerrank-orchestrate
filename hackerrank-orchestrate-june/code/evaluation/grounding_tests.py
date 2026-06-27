"""Mandatory visual-grounding tests (EVALUATION_STRATEGY §5). Prove the system uses
pixels, not text priors:

  * blank-drop  — re-run with images replaced by a blank gray image. Outputs SHOULD
                  collapse toward not_enough_information / valid_image=false. If they
                  barely change, the system is leaning on text priors (a BUG).
  * image-swap  — re-run each claim with another claim's images. Outputs SHOULD change
                  (wrong_object / claim_mismatch / different issue). If stable, the
                  perception isn't driving the decision (a BUG).

Requires ANTHROPIC_API_KEY (it re-runs perception). Run:
  python -m evaluation.grounding_tests --n 6
"""
from __future__ import annotations

import argparse
import base64
import io as _io

from PIL import Image

from src.agent import run_perception
from src.anthropic_client import build_client, has_api_key
from src.config import Config, load_config
from src.decision.assemble import build_decision
from src.io.reader import ClaimInput, read_claims, read_evidence_rules, read_history
from src.perception.ingest import LoadedImage, load_images
from src.prompts import build_system_prompt


def _blank_image_b64() -> str:
    buf = _io.BytesIO()
    Image.new("RGB", (640, 480), (127, 127, 127)).save(buf, format="JPEG")
    return base64.standard_b64encode(buf.getvalue()).decode("ascii")


def _decide(claim, loaded, cfg, client, hist, sp) -> dict:
    facts, _ = run_perception(claim, loaded, cfg, client, sp)
    return build_decision(claim, facts, hist.get(claim.user_id), cfg.thresholds).row.to_csv_dict()


def run(cfg: Config, n: int) -> None:
    if not has_api_key():
        print("ANTHROPIC_API_KEY not set — grounding tests need the API. Skipping.")
        return
    client = build_client(cfg)
    claims = read_claims(cfg.sample_csv)[:n]
    hist = read_history(cfg.history_csv)
    sp = build_system_prompt(read_evidence_rules(cfg.evidence_csv), cfg.prompt_version)
    blank = _blank_image_b64()

    base, blank_changed, swap_changed = {}, 0, 0
    for c in claims:
        base[c.case_id()] = _decide(c, load_images(c, cfg), cfg, client, hist, sp)

    print("=== blank-drop (expect collapse to NEI / valid_image=false) ===")
    for c in claims:
        loaded = [LoadedImage(image_id=i, ok=True, abs_path="", b64=blank) for i in c.image_ids()]
        out = _decide(c, loaded, cfg, client, hist, sp)
        chg = out["claim_status"] != base[c.case_id()]["claim_status"] or out["valid_image"] != base[c.case_id()]["valid_image"]
        blank_changed += chg
        print(f"  {c.case_id()}: base={base[c.case_id()]['claim_status']} -> blank={out['claim_status']} "
              f"valid={out['valid_image']} {'CHANGED' if chg else 'UNCHANGED(!)'}")

    print("\n=== image-swap (expect wrong_object/claim_mismatch/changed issue) ===")
    for idx, c in enumerate(claims):
        donor = claims[(idx + 1) % len(claims)]
        loaded = load_images(donor, cfg)
        out = _decide(c, loaded, cfg, client, hist, sp)
        b = base[c.case_id()]
        chg = (out["claim_status"] != b["claim_status"] or out["issue_type"] != b["issue_type"]
               or "wrong_object" in out["risk_flags"] or "claim_mismatch" in out["risk_flags"])
        swap_changed += chg
        print(f"  {c.case_id()}(+{donor.case_id()} imgs): status={out['claim_status']} issue={out['issue_type']} "
              f"flags={out['risk_flags']} {'CHANGED' if chg else 'UNCHANGED(!)'}")

    tot = len(claims)
    print(f"\nblank-drop changed {blank_changed}/{tot}; image-swap changed {swap_changed}/{tot}.")
    if blank_changed < tot or swap_changed < tot:
        print("WARNING: some cases were stable under perturbation — investigate text-prior leakage.")


def main(argv: list[str] | None = None) -> int:
    from dotenv import load_dotenv
    load_dotenv()
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=6, help="number of sample cases to perturb")
    ap.add_argument("--model", default=None)
    a = ap.parse_args(argv)
    run(load_config(model=a.model), a.n)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
