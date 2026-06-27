"""PURE — the deterministic decision tree (DECISION_ENGINE §3). First match wins.
Returns the status, the branch that fired (for the audit trail), and whether a
claim_mismatch flag should be added. No I/O, no globals, no history input."""
from __future__ import annotations

from dataclasses import dataclass

from src.schema import (
    FAMILY_OF_ISSUE,
    ClaimStatus,
    ContradictionSignal,
    PerceptionFacts,
)

from src.decision.evidence import EvidenceResult

SUPPORTED: ClaimStatus = "supported"
CONTRADICTED: ClaimStatus = "contradicted"
NEI: ClaimStatus = "not_enough_information"


@dataclass(frozen=True)
class StatusResult:
    claim_status: ClaimStatus
    branch: str
    add_claim_mismatch: bool = False


def _issue_matches_claim(visible_issue: str, claimed_family: str) -> bool:
    if claimed_family == "unknown":
        return True  # claim family unclear -> accept any concrete visible issue (lower confidence)
    return FAMILY_OF_ISSUE.get(visible_issue, "unknown") == claimed_family


def decide_status(
    facts: PerceptionFacts,
    evidence: EvidenceResult,
    signals: list[ContradictionSignal],
) -> StatusResult:
    sig = set(signals)
    issue = facts.visible_issue_type

    # 0. Hard NEI gate (the safe invariant; NOT valid_image).
    if not evidence.evidence_standard_met:
        return StatusResult(NEI, "gate:evidence_insufficient")

    # 1. Contradiction by object/part/claim mismatch. `wrong_object` is categorical (a
    #    different object) and always dominates; between `wrong_object_part` and
    #    `claim_mismatch` we HONOR the VLM's confidence ordering (the ordered `signals`
    #    list, most-certain-first from consistency.py) so the firing branch — and thus the
    #    justification — matches the signal the model was most sure of.
    if "wrong_object" in sig:
        return StatusResult(CONTRADICTED, "contradict:wrong_object")
    for s in signals:
        if s == "wrong_object_part":
            return StatusResult(CONTRADICTED, "contradict:wrong_object_part")
        if s == "claim_mismatch":
            return StatusResult(CONTRADICTED, "contradict:claim_mismatch")

    # 2. Claimed part visible & undamaged.
    if facts.part_assessable and issue == "none":
        return StatusResult(CONTRADICTED, "contradict:no_damage")

    # 3. VLM abstains on the issue.
    if issue == "unknown":
        return StatusResult(NEI, "nei:vlm_abstain")

    # 4. Concrete issue that matches the claim -> supported.
    if issue not in ("none", "unknown") and _issue_matches_claim(issue, facts.claimed_issue_family) \
            and facts.object_matches_claim != "false":
        return StatusResult(SUPPORTED, "support:issue_match")

    # 5. Concrete issue that does NOT match the claim -> contradicted (mismatch).
    if issue not in ("none", "unknown"):
        return StatusResult(CONTRADICTED, "contradict:issue_mismatch", add_claim_mismatch=True)

    # 6. Default safe posture.
    return StatusResult(NEI, "nei:default")
