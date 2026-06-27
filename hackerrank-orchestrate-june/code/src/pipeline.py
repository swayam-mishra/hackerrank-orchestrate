"""Per-row orchestration: pre-checks -> perception loop -> deterministic decision ->
validated OutputRow. Owns side effects (image I/O, API calls, audit/checkpoint writes)
so the decision layer stays pure. Guarantees one valid row per input row (safe-default
on any unrecoverable error) and is resumable via a per-case checkpoint."""
from __future__ import annotations

import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from src.agent import run_perception_consistent
from src.config import Config, ensure_dirs
from src.decision.assemble import build_decision
from src.errors import classify_error
from src.io.reader import ClaimInput, HistoryRow
from src.observability import (
    aggregate, format_summary, load_audit, progress_line, write_run_metrics,
)
from src.perception.ingest import load_images
from src.schema import OutputRow, PerceptionFacts

_LOCK = threading.Lock()


def safe_default_row(claim: ClaimInput, cause: str) -> OutputRow:
    """Conservative valid row used when a layer fails unrecoverably (FAILURE_MODES §cross-cutting)."""
    return OutputRow(
        user_id=claim.user_id, image_paths=claim.image_paths, user_claim=claim.user_claim,
        claim_object=claim.claim_object,
        evidence_standard_met=False,
        evidence_standard_met_reason=f"Automated review could not assess this claim ({cause[:160]}).",
        risk_flags=["manual_review_required"],
        issue_type="unknown", object_part="unknown",
        claim_status="not_enough_information",
        claim_status_justification="Routed to manual review; the claim could not be evaluated automatically.",
        supporting_image_ids=[], valid_image=False, severity="unknown",
    )


def decide_from_cache(
    cfg: Config, claims: list[ClaimInput], history_by_id: dict[str, HistoryRow], split: str,
) -> list[OutputRow]:
    """Rebuild output rows from cached PerceptionFacts — deterministic, NO API calls.
    Used to regenerate output.csv after decision-logic changes. Missing/invalid facts -> safe default."""
    fdir = cfg.facts_dir(split)
    rows: list[OutputRow] = []
    for claim in claims:
        fp = fdir / f"{claim.uid()}.json"
        try:
            facts = PerceptionFacts.model_validate_json(fp.read_text(encoding="utf-8"))
            rows.append(build_decision(claim, facts, history_by_id.get(claim.user_id), cfg.thresholds).row)
        except Exception as e:
            rows.append(safe_default_row(claim, f"no cached facts ({type(e).__name__})"))
    return rows


def process_claim(
    claim: ClaimInput, cfg: Config, client: Any,
    history_by_id: dict[str, HistoryRow], system_prompt: str,
) -> tuple[OutputRow, dict, PerceptionFacts | None]:
    case_id = claim.uid()
    t0 = time.monotonic()
    try:
        loaded = load_images(claim, cfg)
        facts, agent_trace = run_perception_consistent(claim, loaded, cfg, client, system_prompt)
        decision = build_decision(claim, facts, history_by_id.get(claim.user_id), cfg.thresholds)
        trace = {
            "case_id": case_id, "user_id": claim.user_id, "claim_object": claim.claim_object,
            "n_model_calls": agent_trace.get("api_calls", 0),
            "images_processed": sum(1 for i in loaded if i.ok),
            "wall_clock_seconds": round(time.monotonic() - t0, 2),
            "agent": agent_trace, "decision": decision.audit,
            "perception_error": facts.perception_error,
            "output": decision.row.to_csv_dict(),
        }
        return decision.row, trace, facts
    except Exception as e:  # never let one row crash the batch
        row = safe_default_row(claim, f"{type(e).__name__}: {e}")
        trace = {"case_id": case_id, "user_id": claim.user_id, "claim_object": claim.claim_object,
                 "error": f"{type(e).__name__}: {e}", "error_class": classify_error(e),
                 "n_model_calls": 0, "images_processed": 0,
                 "wall_clock_seconds": round(time.monotonic() - t0, 2), "output": row.to_csv_dict()}
        return row, trace, None


def _append_jsonl(path: Path, obj: dict) -> None:
    with _LOCK:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(obj, ensure_ascii=False, default=str) + "\n")


def _load_checkpoint(path: Path) -> dict[str, dict]:
    done: dict[str, dict] = {}
    if path.exists():
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    rec = json.loads(line)
                    done[rec["case_id"]] = rec["row"]
        return done
    return done


def run_split(
    cfg: Config, claims: list[ClaimInput], history_by_id: dict[str, HistoryRow],
    system_prompt: str, client: Any, split: str, resume: bool = True,
) -> list[OutputRow]:
    """Process all claims (concurrently, bounded), writing audit + facts-cache +
    checkpoint. Returns rows in INPUT order. Resumable: completed cases are skipped."""
    ensure_dirs(cfg)
    cfg.facts_dir(split).mkdir(parents=True, exist_ok=True)
    audit_path = cfg.audit_dir / f"{split}.jsonl"
    ckpt_path = cfg.artifacts_dir / f"{split}_checkpoint.jsonl"
    done = _load_checkpoint(ckpt_path) if resume else {}
    if not resume:
        audit_path.unlink(missing_ok=True)
        ckpt_path.unlink(missing_ok=True)

    todo = [c for c in claims if c.uid() not in done]
    total = len(claims)
    t_start = time.monotonic()
    completed = len(done)
    if completed:
        print(f"[resume] {completed}/{total} already done; processing {len(todo)} remaining", file=sys.stderr)

    def work(claim: ClaimInput) -> tuple[str, dict, dict]:
        row, trace, facts = process_claim(claim, cfg, client, history_by_id, system_prompt)
        if facts is not None:
            (cfg.facts_dir(split) / f"{claim.uid()}.json").write_text(facts.model_dump_json(), encoding="utf-8")
        _append_jsonl(audit_path, trace)
        rec = {"case_id": claim.uid(), "row": row.to_csv_dict()}
        _append_jsonl(ckpt_path, rec)
        return claim.uid(), rec["row"], trace

    if todo:
        with ThreadPoolExecutor(max_workers=max(1, cfg.concurrency)) as ex:
            for fut in as_completed([ex.submit(work, c) for c in todo]):
                cid, rowdict, trace = fut.result()
                done[cid] = rowdict
                completed += 1
                try:  # live progress must never crash a run
                    print(progress_line(completed, total, trace), file=sys.stderr, flush=True)
                except Exception:
                    pass

    try:
        print(f"done {sum(1 for c in claims if c.uid() in done)}/{total} in {_fmt_dur(time.monotonic() - t_start)}",
              file=sys.stderr)
    except Exception:
        pass

    rows = [OutputRow(**_csvdict_to_kwargs(done[c.uid()])) for c in claims]

    # persisted operational metrics (graded; never crash the run)
    try:
        _write_run_metrics(cfg, split, claims, total_wall_clock_s=time.monotonic() - t_start)
    except Exception as e:
        print(f"[metrics] aggregation skipped: {type(e).__name__}: {e}", file=sys.stderr)
    return rows


def _fmt_dur(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    return f"{m}m{s:02d}s" if m else f"{s}s"


def _write_run_metrics(cfg: Config, split: str, claims: list[ClaimInput], total_wall_clock_s: float) -> None:
    traces = load_audit(cfg.audit_dir / f"{split}.jsonl")
    claims_by_case = {c.uid(): c for c in claims}
    model = traces[0].get("agent", {}).get("model", cfg.model) if traces else cfg.model
    metrics = aggregate(traces, model=model, claims_by_case=claims_by_case, total_wall_clock_s=total_wall_clock_s)
    write_run_metrics(metrics, cfg.artifacts_dir / "run_metrics.json")
    # NOTE: the run does NOT rewrite the tracked evaluation_report.md (avoids spurious diffs /
    # races on a source-controlled artifact). Update that doc on demand via the backfill CLI:
    #   python -m src.observability --split <split>
    print("\n" + format_summary(metrics), file=sys.stderr)


def _csvdict_to_kwargs(d: dict[str, str]) -> dict:
    """Rebuild an OutputRow from its serialized csv dict (round-trips bool/set fields)."""
    return {
        "user_id": d["user_id"], "image_paths": d["image_paths"], "user_claim": d["user_claim"],
        "claim_object": d["claim_object"],
        "evidence_standard_met": d["evidence_standard_met"] == "true",
        "evidence_standard_met_reason": d["evidence_standard_met_reason"],
        "risk_flags": [] if d["risk_flags"] == "none" else d["risk_flags"].split(";"),
        "issue_type": d["issue_type"], "object_part": d["object_part"],
        "claim_status": d["claim_status"], "claim_status_justification": d["claim_status_justification"],
        "supporting_image_ids": [] if d["supporting_image_ids"] == "none" else d["supporting_image_ids"].split(";"),
        "valid_image": d["valid_image"] == "true", "severity": d["severity"],
    }
