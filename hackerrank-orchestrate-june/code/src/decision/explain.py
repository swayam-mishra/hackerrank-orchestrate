"""PURE — image-grounded free-text generation, from logged facts + the decision
branch (never invented). Returns (evidence_standard_met_reason, claim_status_justification).
No I/O, no globals."""
from __future__ import annotations

from src.schema import PerceptionFacts


def _cue(facts: PerceptionFacts, supporting: list[str]) -> str:
    by_id = {i.image_id: i for i in facts.images}
    for iid in supporting:
        c = by_id.get(iid)
        if c and c.visual_cue.strip():
            return c.visual_cue.strip()
    for i in facts.images:
        if i.visual_cue.strip():
            return i.visual_cue.strip()
    return ""


def _ids(supporting: list[str]) -> str:
    return f" (see {';'.join(supporting)})" if supporting else ""


def build_explanations(
    facts: PerceptionFacts,
    claim_object: str,
    claim_status: str,
    branch: str,
    issue_type: str,
    object_part: str,
    supporting: list[str],
) -> tuple[str, str]:
    cue = _cue(facts, supporting)
    cue_txt = f" {cue}." if cue else ""
    ids = _ids(supporting)

    if claim_status == "supported":
        ev = "The claimed part is visible and the claimed condition can be assessed from the submitted image(s)."
        just = f"The image(s) show {issue_type.replace('_', ' ')} on the {object_part.replace('_', ' ')}, consistent with the claim.{cue_txt}{ids}"
    elif claim_status == "contradicted":
        ev = "The image(s) are clear enough to evaluate the claim against the visible evidence."
        if branch == "contradict:wrong_object":
            just = f"The submitted image shows a different object than the claimed {claim_object}, so it does not support the claim.{ids}"
        elif branch == "contradict:wrong_object_part":
            just = f"The image shows a different part than the claimed {object_part.replace('_', ' ')}, so the claim is not supported.{ids}"
        elif branch == "contradict:no_damage":
            just = f"The {object_part.replace('_', ' ')} is visible but shows no damage, which contradicts the claim.{cue_txt}{ids}"
        else:  # claim_mismatch / issue_mismatch
            seen = issue_type.replace('_', ' ') if issue_type not in ("none", "unknown") else "different damage"
            just = f"The visible {seen} does not match the user's claim, so the claim is contradicted.{cue_txt}{ids}"
    else:  # not_enough_information
        ev = "The submitted image(s) do not provide sufficient evidence to evaluate the claim."
        just = f"The claimed {object_part.replace('_', ' ')} could not be verified from the submitted image(s), so there is not enough information to decide."

    # Cross-claim image reuse on a supporting image is a fraud signal — surface it.
    by_id = {i.image_id: i for i in facts.images}
    for iid in supporting:
        img = by_id.get(iid)
        if img is not None and img.reused_in_cases:
            just += f" Note: {iid} was also submitted in claim(s) {', '.join(img.reused_in_cases)}."
    return ev, just
