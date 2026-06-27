"""PURE — object/part consistency. Derives contradiction signals from the VLM facts.
No I/O, no globals."""
from __future__ import annotations

from dataclasses import dataclass

from src.schema import ContradictionSignal, PerceptionFacts


@dataclass(frozen=True)
class ConsistencyResult:
    signals: list[ContradictionSignal]


_SEV_BAND = {"low": 1, "medium": 2, "high": 3}


def check_consistency(facts: PerceptionFacts) -> ConsistencyResult:
    # Signals DERIVED deterministically from structured facts (not the VLM's soft list):
    derived: set[ContradictionSignal] = set()
    if facts.object_matches_claim == "false":
        derived.add("wrong_object")  # a 'false' object match is, by definition, a wrong_object contradiction
    # A large claimed-vs-observed severity gap on the assessable part is a claim_mismatch,
    # corroborated from STRUCTURED data ('claimed severe / observed minor'), not just the VLM boolean.
    if (facts.part_assessable
            and facts.claimed_severity in _SEV_BAND and facts.severity_estimate in _SEV_BAND
            and abs(_SEV_BAND[facts.claimed_severity] - _SEV_BAND[facts.severity_estimate]) >= 2):
        derived.add("claim_mismatch")

    reported = list(facts.contradiction_signals)
    if reported:
        # Honor the VLM's confidence ordering; prepend categorical wrong_object, and append any
        # OTHER derived signal not already reported (lowest priority — it only corroborates).
        ordered = list(reported)
        if "wrong_object" in derived and "wrong_object" not in ordered:
            ordered.insert(0, "wrong_object")
        if "claim_mismatch" in derived and "claim_mismatch" not in ordered:
            ordered.append("claim_mismatch")
        return ConsistencyResult(signals=ordered)
    # No VLM signals reported -> emit any derived signals in the fixed priority order.
    order = ("wrong_object", "wrong_object_part", "claim_mismatch")
    return ConsistencyResult(signals=[s for s in order if s in derived])
