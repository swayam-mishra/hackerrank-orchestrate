"""Evidence rulebook helpers (used at prompt-build time to ground sufficiency, and by
the eval harness). The full rulebook is injected into the cached system prompt, so
this is not a live tool round-trip. Pure formatting/selection."""
from __future__ import annotations

from src.io.reader import EvidenceRule

# issue_family -> applies_to keywords, for optional focused selection / audit.
_FAMILY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "dent_scratch": ("dent", "scratch", "panel", "body"),
    "crack_glass": ("crack", "glass", "light", "mirror", "broken", "screen"),
    "broken_missing": ("broken", "missing", "hinge", "lid", "port", "component"),
    "packaging": ("crush", "torn", "seal", "exterior"),
    "water_stain": ("water", "stain", "label"),
    "unknown": (),
}


def format_rulebook(rules: list[EvidenceRule]) -> str:
    return "\n".join(
        f"- [{r.requirement_id}] ({r.claim_object} / {r.applies_to}): {r.minimum_image_evidence}"
        for r in rules
    )


def select_rules(rules: list[EvidenceRule], claim_object: str, issue_family: str) -> list[EvidenceRule]:
    """Rules that apply to this object (object-specific + 'all'), family-relevant first."""
    applicable = [r for r in rules if r.claim_object in (claim_object, "all")]
    kw = _FAMILY_KEYWORDS.get(issue_family, ())
    relevant = [r for r in applicable if any(k in r.applies_to.lower() for k in kw)]
    rest = [r for r in applicable if r not in relevant]
    return relevant + rest
