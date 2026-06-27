"""PURE — severity invariants over the VLM's soft estimate. NEI => unknown;
issue none => none; an object-aware (claim_object, issue_type) ceiling caps the VLM's
soft estimate (e.g. a car scratch is cosmetic -> 'medium' max, but a laptop screen
scratch impairs the display -> 'high' allowed); falls back to a flat per-issue-type
ceiling, else passes the VLM estimate through. No geometry/area math."""
from __future__ import annotations

from src.schema import SEVERITY_VALUES, Severity

# Object-aware ceiling: the SAME cosmetic issue means different things on different
# objects. A scratch is trivial on a package, cosmetic on a car body, but on a laptop
# it usually means the screen/display is impaired -> 'high' allowed. Keyed
# (claim_object, issue_type) -> max severity; checked BEFORE the flat ceiling below.
OBJECT_SEVERITY_CEILING: dict[tuple[str, str], str] = {
    ("car", "scratch"): "medium",
    ("car", "dent"): "medium",
    ("car", "stain"): "medium",
    ("laptop", "scratch"): "high",    # screen scratch = display impaired
    ("laptop", "dent"): "medium",
    ("laptop", "stain"): "medium",
    ("package", "scratch"): "low",
    ("package", "stain"): "medium",
    ("package", "dent"): "medium",
}

# Fallback when the (object, issue) combo is not in the object-aware matrix.
# Minor / cosmetic / surface damage types cannot be 'high' regardless of object.
SEVERITY_CEILING: dict[str, str] = {
    "scratch": "medium",
    "stain": "medium",
    "dent": "medium",
    "torn_packaging": "medium",
    "water_damage": "medium",
}

# Ordering used to cap a severity at a ceiling. 'unknown' is intentionally absent:
# it is never ranked, so it is passed through untouched (never capped, never raised).
_SEVERITY_RANK: dict[str, int] = {"none": 0, "low": 1, "medium": 2, "high": 3}


def _cap(severity: str, ceiling: str) -> Severity:
    """Return `severity` unless it outranks `ceiling`, in which case return `ceiling`."""
    if _SEVERITY_RANK[severity] > _SEVERITY_RANK[ceiling]:
        return ceiling  # type: ignore[return-value]
    return severity  # type: ignore[return-value]


def finalize_severity(
    claim_status: str, issue_type: str, vlm_severity: str, claim_object: str
) -> Severity:
    if claim_status == "not_enough_information":
        return "unknown"
    if issue_type == "none":
        return "none"
    # Object-aware ceiling wins; fall back to the flat per-issue-type ceiling.
    ceiling = OBJECT_SEVERITY_CEILING.get((claim_object, issue_type))
    if ceiling is None:
        ceiling = SEVERITY_CEILING.get(issue_type)
    if ceiling is not None and vlm_severity in _SEVERITY_RANK:
        return _cap(vlm_severity, ceiling)
    return vlm_severity if vlm_severity in SEVERITY_VALUES else "unknown"  # type: ignore[return-value]
