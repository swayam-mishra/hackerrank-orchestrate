"""PURE — evidence sufficiency (the hard NEI gate). Grounded in: is there a usable
image, and is the claimed part assessable OR a contradiction determinable?

Insufficient (NEI) only when the claimed region isn't shown AND no contradiction can
be determined (sample case_006, case_018). When a wrong-object / mismatch / no-damage
contradiction IS determinable, evidence is sufficient and the claim is contradicted
(case_019, case_008, case_014). No I/O, no globals."""
from __future__ import annotations

from dataclasses import dataclass

from src.schema import ContradictionSignal, PerceptionFacts

# wrong_object_part is intentionally NOT a "determinable contradiction" here: a different
# part being shown means the claimed part isn't assessable -> NEI via the gate (sample
# case_006). When the claimed part IS assessable, the tree still contradicts on
# wrong_object_part (sample case_014).
_CONTRA = {"wrong_object", "claim_mismatch"}


@dataclass(frozen=True)
class EvidenceResult:
    evidence_standard_met: bool
    reason: str


def evaluate_evidence(facts: PerceptionFacts, signals: list[ContradictionSignal]) -> EvidenceResult:
    usable = [i for i in facts.images if i.usable]
    sig = set(signals)
    contra_signal = bool(_CONTRA & sig)
    if "wrong_object_part" in sig and not facts.part_assessable:
        contra_signal = False
    determinable_contradiction = contra_signal or (
        facts.part_assessable and facts.visible_issue_type == "none"
    )
    met = bool(usable) and (facts.part_assessable or determinable_contradiction)

    if not usable:
        reason = "No usable image was available to review the claim."
    elif met and facts.part_assessable:
        reason = "The claimed part is visible and assessable in the submitted image(s)."
    elif met:
        reason = "The image(s) are clear enough to evaluate the claim against the visible evidence."
    elif not facts.part_assessable:
        reason = "The submitted image(s) do not clearly show the claimed part, so the claim cannot be verified."
    else:
        reason = "The image evidence is insufficient to evaluate the claim."
    return EvidenceResult(evidence_standard_met=met, reason=reason)
