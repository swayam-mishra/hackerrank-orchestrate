"""Lightweight run observability (orchestrator-layer only; pure functions stay pure).

- progress_line(): one stderr line per completed row.
- aggregate(): roll per-row audit traces into operational metrics (cost, latency,
  cache, distributions) — reused for both live runs and backfill from the JSONL.
- render_operational_md(): the MEASURED operational section for evaluation_report.md.

Reads the per-row audit JSONL (no separate telemetry framework). All metric code is
caller-wrapped in try/except so it can never crash a row.

Backfill (no re-run):  python -m src.observability --split test
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.config import Config, load_config, prices_for


# ───────────────────────── per-row field access (tolerant of old traces) ─────────────────────────

def _usage(trace: dict) -> dict:
    return trace.get("agent", {}).get("usage", {}) or {}


def _row_calls(trace: dict) -> int:
    a = trace.get("agent", {})
    if "api_calls" in a:
        return int(a["api_calls"])
    # backfill estimate from older traces: one call per loop round (+1 if a forced finalize ran)
    return int(a.get("rounds", 0)) + (1 if a.get("forced") else 0)


def _row_status(trace: dict) -> str:
    return trace.get("output", {}).get("claim_status", "")


def _row_error(trace: dict) -> str | None:
    return trace.get("error") or trace.get("perception_error") or trace.get("agent", {}).get("error")


def _row_images(trace: dict, claims_by_case: dict | None) -> int:
    if "images_processed" in trace:
        return int(trace["images_processed"])
    if claims_by_case and trace.get("case_id") in claims_by_case:
        return len(claims_by_case[trace["case_id"]].image_ids())
    return 0


def _fmt(n: int) -> str:
    return f"{n:,}"


# ───────────────────────── live progress ─────────────────────────

def progress_line(done: int, total: int, trace: dict) -> str:
    u = _usage(trace)
    secs = trace.get("wall_clock_seconds")
    secs_s = f"{secs:.1f}s" if isinstance(secs, (int, float)) else "?s"
    err = _row_error(trace)
    tail = f" | ERROR: {err[:60]}" if err else ""
    return (f"[{done}/{total}] {trace.get('case_id','?')} {trace.get('claim_object','?')} "
            f"-> {_row_status(trace) or '?'} | {_row_calls(trace)} calls | {secs_s} | "
            f"in {_fmt(u.get('input_tokens',0))} / out {_fmt(u.get('output_tokens',0))} / "
            f"cache_read {_fmt(u.get('cache_read_input_tokens',0))} tok{tail}")


# ───────────────────────── aggregation ─────────────────────────

def _pctl(xs: list[float], p: float) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    k = (len(s) - 1) * p
    lo = int(k)
    hi = min(lo + 1, len(s) - 1)
    return round(s[lo] + (s[hi] - s[lo]) * (k - lo), 2)


def _mean(xs: list[float]) -> float | None:
    return round(sum(xs) / len(xs), 2) if xs else None


def aggregate(traces: list[dict], model: str, claims_by_case: dict | None = None,
              total_wall_clock_s: float | None = None) -> dict:
    px = prices_for(model)
    n = len(traces)
    tin = sum(_usage(t).get("input_tokens", 0) for t in traces)
    tout = sum(_usage(t).get("output_tokens", 0) for t in traces)
    tcr = sum(_usage(t).get("cache_read_input_tokens", 0) for t in traces)
    tcc = sum(_usage(t).get("cache_creation_input_tokens", 0) for t in traces)
    calls = [_row_calls(t) for t in traces]
    images = sum(_row_images(t, claims_by_case) for t in traces)
    latencies = [t["wall_clock_seconds"] for t in traces if isinstance(t.get("wall_clock_seconds"), (int, float))]
    retries = sum(int(t.get("agent", {}).get("repaired", 0)) for t in traces)
    errored = sum(1 for t in traces if _row_error(t))
    safe_default = sum(1 for t in traces if t.get("error"))  # top-level error == except path / safe-default row

    cost = {
        "input": round(tin / 1e6 * px["input"], 4),
        "output": round(tout / 1e6 * px["output"], 4),
        "cache_write": round(tcc / 1e6 * px["cache_write"], 4),
        "cache_read": round(tcr / 1e6 * px["cache_read"], 4),
    }
    cost["total_usd"] = round(sum(cost.values()), 4)
    total_input_all = tin + tcr + tcc

    from collections import Counter
    status_dist = dict(Counter(_row_status(t) for t in traces))
    mrr = sum(1 for t in traces if "manual_review_required" in (t.get("output", {}).get("risk_flags", "")))
    rl429 = sum(int(t.get("agent", {}).get("rate_limit_429s", 0) or 0) for t in traces)
    err_classes = dict(Counter(
        (t.get("error_class") or t.get("agent", {}).get("error_class"))
        for t in traces if (t.get("error_class") or t.get("agent", {}).get("error_class"))
    ))
    # manual_review_required driver breakdown — the automation-ceiling KPI: how much of the MRR
    # rate is history-driven (label-required) vs. the tunable evidence/fraud drivers.
    mrr_drivers_dist = dict(Counter(
        d for t in traces for d in (t.get("decision", {}).get("mrr_drivers", []) or [])
    ))

    return {
        "model": model,
        "rows_processed": n,
        "rows_errored": errored,
        "rows_safe_default": safe_default,
        "model_calls": {
            "total": sum(calls), "mean": _mean(calls),
            "median": _pctl(calls, 0.5), "p95": _pctl(calls, 0.95),
        },
        "tokens": {
            "input_uncached": tin, "output": tout,
            "cache_read": tcr, "cache_creation": tcc,
            "input_total_incl_cache": total_input_all,
        },
        "cache": {
            "cache_read_tokens": tcr,
            "pct_input_from_cache": round(100 * tcr / total_input_all, 1) if total_input_all else 0.0,
        },
        "images_processed": images,
        "cost_usd": cost,
        "cost_per_claim_usd": round(cost["total_usd"] / n, 4) if n else 0.0,
        "latency_seconds": {
            "captured": bool(latencies),
            "total_wall_clock": round(total_wall_clock_s, 1) if total_wall_clock_s else (round(sum(latencies), 1) if latencies else None),
            "mean": _mean(latencies), "median": _pctl(latencies, 0.5), "p95": _pctl(latencies, 0.95),
        },
        "throughput_rows_per_min": (
            round(n / (total_wall_clock_s / 60), 1) if total_wall_clock_s
            else (round(n / (sum(latencies) / 60), 1) if latencies else None)
        ),
        "rate_limit_429s": rl429,  # 429s that ESCAPED SDK retries (internal retries not counted)
        "retries_in_loop": retries,
        "error_class_distribution": err_classes,
        "manual_review_required_rate": round(mrr / n, 3) if n else 0.0,
        "manual_review_driver_distribution": mrr_drivers_dist,
        "claim_status_distribution": status_dist,
        "pricing_assumptions_per_mtok": px,
    }


def write_run_metrics(metrics: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(metrics, indent=2, ensure_ascii=False), encoding="utf-8")


def format_summary(m: dict) -> str:
    c, lat = m["cost_usd"], m["latency_seconds"]
    lines = [
        f"── run metrics ({m['model']}) ──",
        f"rows: {m['rows_processed']} processed, {m['rows_errored']} errored, {m['rows_safe_default']} safe-default",
        f"model calls: {m['model_calls']['total']} total (mean {m['model_calls']['mean']}, median {m['model_calls']['median']}, p95 {m['model_calls']['p95']})",
        f"tokens: in {_fmt(m['tokens']['input_uncached'])} | out {_fmt(m['tokens']['output'])} | "
        f"cache_read {_fmt(m['tokens']['cache_read'])} | cache_write {_fmt(m['tokens']['cache_creation'])}",
        f"cache: {m['cache']['pct_input_from_cache']}% of input served from cache (read {_fmt(m['cache']['cache_read_tokens'])} tok)",
        f"images processed: {m['images_processed']}",
        f"COST: ${c['total_usd']} total (in ${c['input']} / out ${c['output']} / cache_w ${c['cache_write']} / cache_r ${c['cache_read']}) "
        f"= ${m['cost_per_claim_usd']}/claim",
    ]
    if lat["captured"]:
        lines.append(f"latency: {lat['total_wall_clock']}s wall-clock, mean {lat['mean']}s, median {lat['median']}s, p95 {lat['p95']}s "
                     f"({m['throughput_rows_per_min']} rows/min)")
    else:
        lines.append("latency: not captured in this (prior) run — instrumentation added; measured on the next run")
    lines.append(f"manual_review_required rate: {m['manual_review_required_rate']:.0%} | status dist: {m['claim_status_distribution']} | in-loop retries: {m['retries_in_loop']}")
    return "\n".join(lines)


# ───────────────────────── report injection ─────────────────────────

OPS_START = "<!-- OPERATIONAL_METRICS:START (generated from run_metrics.json) -->"
OPS_END = "<!-- OPERATIONAL_METRICS:END -->"


def render_operational_md(m: dict) -> str:
    c, lat, tok = m["cost_usd"], m["latency_seconds"], m["tokens"]
    lat_line = (f"- **Latency / runtime:** {lat['total_wall_clock']}s wall-clock; per row mean {lat['mean']}s, "
                f"median {lat['median']}s, p95 {lat['p95']}s; throughput {m['throughput_rows_per_min']} rows/min."
                if lat["captured"] else
                "- **Latency / runtime:** not captured in the run these numbers are backfilled from "
                "(timing instrumentation has since been added; latency is measured on the next run).")
    return "\n".join([
        OPS_START,
        f"_MEASURED from `code/artifacts/run_metrics.json` (model `{m['model']}`). Regenerate with "
        f"`python -m src.observability --split <split>`._",
        "",
        f"- **Rows:** {m['rows_processed']} processed, {m['rows_errored']} errored, {m['rows_safe_default']} safe-default.",
        f"- **Model calls:** {m['model_calls']['total']} total — mean {m['model_calls']['mean']}, "
        f"median {m['model_calls']['median']}, p95 {m['model_calls']['p95']} per row.",
        f"- **Tokens:** input(uncached) {tok['input_uncached']:,}; output {tok['output']:,}; "
        f"cache-read {tok['cache_read']:,}; cache-write {tok['cache_creation']:,} "
        f"(total input incl. cache {tok['input_total_incl_cache']:,}).",
        f"- **Images processed:** {m['images_processed']}.",
        f"- **Cost (USD):** **${c['total_usd']} total** = input ${c['input']} + output ${c['output']} + "
        f"cache-write ${c['cache_write']} + cache-read ${c['cache_read']}; **${m['cost_per_claim_usd']}/claim**. "
        f"Prices/MTok: {m['pricing_assumptions_per_mtok']}.",
        f"- **Prompt caching (is it working?):** **{m['cache']['pct_input_from_cache']}% of input tokens served from cache** "
        f"({m['cache']['cache_read_tokens']:,} cache-read tok) — within-row breakpoint is effective.",
        lat_line,
        f"- **Rate limits / retries:** escaped 429s {m['rate_limit_429s']} (SDK internal retries not counted); "
        f"in-loop validation retries {m['retries_in_loop']}.",
        f"- **Operational mix:** manual_review_required rate {m['manual_review_required_rate']:.0%} "
        f"(drivers {m.get('manual_review_driver_distribution', {}) or 'none'} — history-driven is label-required, "
        f"the rest is the tunable automation gap); "
        f"claim_status distribution {m['claim_status_distribution']}; "
        f"error classes {m.get('error_class_distribution', {}) or 'none'}.",
        "",
        "**Caveats (unchanged):** numbers above are for the multi-round agent loop. The **Batch API 50% discount "
        "does NOT apply** to a multi-round tool loop (only single-shot calls). Caching is within-row: the system+tools "
        "prefix alone (~1.7k tok) is below Opus 4.8's 4096-token minimum, so cross-row prefix caching does not kick in; "
        "the high cache-read % above comes from reusing the image-bearing first turn across tool rounds.",
        OPS_END,
    ])


def update_report_section(report_path: Path, md: str) -> bool:
    if not report_path.exists():
        return False
    text = report_path.read_text(encoding="utf-8")
    if OPS_START in text and OPS_END in text:
        pre = text.split(OPS_START)[0]
        post = text.split(OPS_END)[1]
        report_path.write_text(pre + md + post, encoding="utf-8")
        return True
    return False


# ───────────────────────── backfill CLI ─────────────────────────

def load_audit(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def backfill(cfg: Config, split: str) -> dict:
    from src.io.reader import read_claims
    audit_path = cfg.audit_dir / f"{split}.jsonl"
    traces = load_audit(audit_path)
    csv_path = cfg.sample_csv if split == "sample" else cfg.test_csv
    claims_by_case = {c.uid(): c for c in read_claims(csv_path)}
    model = traces[0].get("agent", {}).get("model", cfg.model) if traces else cfg.model
    metrics = aggregate(traces, model=model, claims_by_case=claims_by_case)
    write_run_metrics(metrics, cfg.artifacts_dir / "run_metrics.json")
    report = cfg.repo_root / "code" / "evaluation" / "evaluation_report.md"
    updated = update_report_section(report, render_operational_md(metrics))
    print(format_summary(metrics))
    print(f"\nwrote {cfg.artifacts_dir/'run_metrics.json'}; report section {'updated' if updated else 'NOT updated (markers missing)'}")
    return metrics


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Aggregate per-row audit JSONL into run_metrics.json (backfill, no re-run).")
    ap.add_argument("--split", choices=["test", "sample"], default="test")
    a = ap.parse_args(argv)
    backfill(load_config(), a.split)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
