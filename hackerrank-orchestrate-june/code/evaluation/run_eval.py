"""Evaluation metrics for the sample set (the only labeled data, n=20).

Per-column: exact-match for fixed enums/bools; set-overlap (Jaccard + P/R/F1) for the
multi-label risk_flags and supporting_image_ids; a claim_status confusion matrix.
Free-text columns are NOT auto-graded (read manually). All numbers are DIRECTIONAL at
n=20 — no CIs/significance. LLM-as-judge is intentionally NOT used."""
from __future__ import annotations

import csv
import re
from collections import Counter
from pathlib import Path

_CASE_RE = re.compile(r"(case_\d+)")

EXACT_COLS = ("evidence_standard_met", "valid_image", "issue_type", "object_part", "claim_status", "severity")
SET_COLS = ("risk_flags", "supporting_image_ids")
FREE_COLS = ("evidence_standard_met_reason", "claim_status_justification")


def read_predictions(path: Path) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            m = _CASE_RE.search(row["image_paths"])
            out[m.group(1) if m else row["user_id"]] = row
    return out


def _tokens(x: str) -> set[str]:
    return {t for t in (x or "").split(";") if t and t != "none"}


def set_prf(pred: str, exp: str) -> tuple[float, float, float]:
    P, E = _tokens(pred), _tokens(exp)
    if not P and not E:
        return 1.0, 1.0, 1.0
    tp, fp, fn = len(P & E), len(P - E), len(E - P)
    prec = tp / (tp + fp) if (tp + fp) else 0.0
    rec = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
    return prec, rec, f1


CLAIM_STATUS_CLASSES = ("supported", "contradicted", "not_enough_information")


def claim_status_per_class(confusion: Counter, classes: tuple[str, ...] = CLAIM_STATUS_CLASSES) -> dict:
    """Per-class precision / recall / F1 / support for claim_status, from the confusion
    counts keyed (gold, pred). Aggregate accuracy hides the rare classes (contradicted n=5,
    NEI n=2); a supported-default scores ~65% while missing every contradiction — recall
    on contradicted/NEI is the number that actually matters."""
    out: dict[str, dict] = {}
    for c in classes:
        tp = confusion.get((c, c), 0)
        fn = sum(v for (g, p), v in confusion.items() if g == c and p != c)
        fp = sum(v for (g, p), v in confusion.items() if g != c and p == c)
        prec = tp / (tp + fp) if (tp + fp) else 0.0
        rec = tp / (tp + fn) if (tp + fn) else 0.0
        f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
        out[c] = {"precision": round(prec, 3), "recall": round(rec, 3),
                  "f1": round(f1, 3), "support": tp + fn}
    return out


def evaluate(pred_by_case: dict[str, dict], labels: dict[str, dict]) -> dict:
    n = 0
    exact = {c: 0 for c in EXACT_COLS}
    set_f1: dict[str, list[float]] = {c: [] for c in SET_COLS}
    set_exact = {c: 0 for c in SET_COLS}
    confusion: Counter = Counter()
    mismatches: list[dict] = []

    for case, lab in labels.items():
        pred = pred_by_case.get(case)
        if pred is None:
            mismatches.append({"case": case, "column": "*", "got": "MISSING", "expected": "*"})
            continue
        n += 1
        for c in EXACT_COLS:
            g = pred.get(c, "")
            if g == lab[c]:
                exact[c] += 1
            else:
                mismatches.append({"case": case, "column": c, "got": g, "expected": lab[c]})
        for c in SET_COLS:
            g = pred.get(c, "")
            _, _, f1 = set_prf(g, lab[c])
            set_f1[c].append(f1)
            if _tokens(g) == _tokens(lab[c]):
                set_exact[c] += 1
            else:
                mismatches.append({"case": case, "column": c, "got": g, "expected": lab[c]})
        confusion[(lab["claim_status"], pred.get("claim_status", ""))] += 1

    return {
        "n": n,
        "exact_accuracy": {c: round(exact[c] / n, 3) if n else 0.0 for c in EXACT_COLS},
        "set_mean_f1": {c: round(sum(v) / len(v), 3) if v else 0.0 for c, v in set_f1.items()},
        "set_exact_rate": {c: round(set_exact[c] / n, 3) if n else 0.0 for c in SET_COLS},
        "claim_status_confusion": {f"{k[0]} -> {k[1]}": v for k, v in sorted(confusion.items())},
        "claim_status_per_class": claim_status_per_class(confusion),
        "mismatches": mismatches,
        "note": "DIRECTIONAL only (n=20); no confidence intervals. Free-text columns not auto-graded.",
    }


def regression_diff(prev: dict[str, dict], curr: dict[str, dict]) -> list[dict]:
    """Per-cell diff across all rows (sample + test) to catch unintended changes."""
    diffs: list[dict] = []
    for case in sorted(set(prev) | set(curr)):
        a, b = prev.get(case), curr.get(case)
        if a is None or b is None:
            diffs.append({"case": case, "column": "*", "prev": a is not None, "curr": b is not None})
            continue
        for col in a:
            if a.get(col) != b.get(col):
                diffs.append({"case": case, "column": col, "prev": a.get(col), "curr": b.get(col)})
    return diffs


def repeat_variance(prediction_sets: list[dict[str, dict]]) -> dict:
    """Given N independent prediction runs ({case: row}), report per-column STABILITY (the
    fraction of cases that produced an identical value across all runs) and the unstable cases.
    Surfaces perception non-determinism (turns 'directional, n=20' into 'directional ± variance')."""
    sets = [p for p in prediction_sets if p]
    if len(sets) < 2:
        return {"runs": len(sets), "note": "need >=2 runs to measure variance"}
    cases = sorted(set().union(*[set(p) for p in sets]))
    cols = EXACT_COLS + SET_COLS
    unstable: dict[str, list[str]] = {c: [] for c in cols}
    for case in cases:
        rows = [p.get(case) for p in sets]
        for c in cols:
            seen = {
                (frozenset(_tokens(r.get(c, ""))) if c in SET_COLS else r.get(c, ""))
                for r in rows if r is not None
            }
            if len(seen) > 1:
                unstable[c].append(case)
    n = len(cases)
    return {
        "runs": len(sets),
        "cases": n,
        "stable_rate": {c: round(1 - len(unstable[c]) / n, 3) if n else 1.0 for c in cols},
        "unstable_cases": {c: v for c, v in unstable.items() if v},
    }


def format_metrics(m: dict) -> str:
    lines = [f"Evaluated {m['n']} labeled rows.  ({m['note']})", "", "Exact-match accuracy:"]
    lines += [f"  {c:24s} {v:.0%}" for c, v in m["exact_accuracy"].items()]
    lines += ["", "Multi-label (set) metrics:"]
    for c in SET_COLS:
        lines.append(f"  {c:24s} mean_F1={m['set_mean_f1'][c]:.2f}  exact_set={m['set_exact_rate'][c]:.0%}")
    lines += ["", "claim_status per-class (precision / recall / F1, support):"]
    for c, s in m.get("claim_status_per_class", {}).items():
        lines.append(f"  {c:24s} P={s['precision']:.0%}  R={s['recall']:.0%}  F1={s['f1']:.2f}  (n={s['support']})")
    lines += ["", "claim_status confusion (expected -> got):"]
    lines += [f"  {k:40s} {v}" for k, v in m["claim_status_confusion"].items()]
    if m["mismatches"]:
        lines += ["", f"Mismatches ({len(m['mismatches'])}) — read every one:"]
        lines += [f"  {d['case']:10s} {d['column']:24s} got={d['got']!r} exp={d['expected']!r}" for d in m["mismatches"]]
    return "\n".join(lines)
