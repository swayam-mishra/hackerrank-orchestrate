"""PURE — assemble the final OutputRow from PerceptionFacts + history, by composing the
other pure layers. The deterministic core: facts in -> validated OutputRow + audit out.
No I/O, no API, no globals. Re-runnable on cached facts (cli --from-cache)."""
from __future__ import annotations

from dataclasses import dataclass

from src.config import Thresholds
from src.io.reader import ClaimInput, HistoryRow
from src.schema import (
    PARTS_BY_OBJECT,
    ContradictionSignal,
    ObjectPart,
    OutputRow,
    PerceptionFacts,
    normalize_risk_flags,
)
from src.decision.aggregate import aggregate_images
from src.decision.consistency import check_consistency
from src.decision.evidence import evaluate_evidence
from src.decision.explain import build_explanations
from src.decision.severity import finalize_severity
from src.decision.tree import CONTRADICTED, NEI, SUPPORTED, decide_status
from src.risk.history import history_overlay
from src.risk.injection import detect_injection


@dataclass(frozen=True)
class AssembledDecision:
    row: OutputRow
    audit: dict


def _choose_object_part(facts: PerceptionFacts, status: str, signals: list[str], claim_object: str) -> ObjectPart:
    if "wrong_object" in signals:
        cand: str = "unknown"
    elif "wrong_object_part" in signals:
        cand = facts.claimed_part   # report claimed part, not visible
    elif status == NEI:
        cand = facts.claimed_part
    elif facts.visible_object_part != "unknown":
        cand = facts.visible_object_part
    else:
        cand = facts.claimed_part
    if cand not in PARTS_BY_OBJECT[claim_object]:
        cand = "unknown"
    return cand  # type: ignore[return-value]


def build_decision(
    claim: ClaimInput,
    facts: PerceptionFacts,
    history: HistoryRow | None,
    th: Thresholds,
) -> AssembledDecision:
    consistency = check_consistency(facts)
    signals: list[ContradictionSignal] = list(consistency.signals)
    evidence = evaluate_evidence(facts, signals)
    agg = aggregate_images(facts)
    status_res = decide_status(facts, evidence, signals)
    status = status_res.claim_status
    if status_res.add_claim_mismatch and "claim_mismatch" not in signals:
        signals.append("claim_mismatch")

    # supporting images: none for NEI; otherwise the validated candidates (fallback to first usable).
    if status == NEI:
        supporting: list[str] = []
    else:
        def _clean(ids: list[str]) -> list[str]:
            out: list[str] = []
            for x in ids:
                x = x.strip()
                if x and x.lower() != "none" and x not in out:
                    out.append(x)
            return out

        supporting = _clean(list(agg.supporting_candidates))
        if not supporting:
            usable_ids = _clean([i.image_id for i in facts.images if i.usable])
            supporting = usable_ids[:1]
            if not supporting:
                # no usable image but a verdict was reached -> degrade safely to NEI
                status = NEI
                status_res = type(status_res)(NEI, "nei:no_usable_image")

    # 'supported' must be GROUNDED: at least one supporting image has to carry a specific,
    # locatable visual_cue. A 'supported' read with no cue on any supporting image is an
    # ungrounded/hallucinated positive -> degrade to NEI (enforces the prompt's cue rule in code).
    if status == SUPPORTED and not any(
        im.visual_cue.strip() for im in facts.images if im.image_id in set(supporting)
    ):
        status = NEI
        status_res = type(status_res)(NEI, "nei:ungrounded_support")
        supporting = []

    # low-confidence abstention (additive): a supported/contradicted verdict whose VLM
    # confidence is below the floor AND has no grounding cue is too shaky to assert -> route
    # to a human as NEI + manual_review_required. Can only abstain, never flip to a verdict.
    low_confidence_abstain = (
        status in (SUPPORTED, CONTRADICTED)
        and facts.vlm_confidence < th.vlm_confidence_min
        and not any(im.visual_cue.strip() for im in facts.images if im.image_id in set(supporting))
    )
    if low_confidence_abstain:
        status = NEI
        status_res = type(status_res)(NEI, "nei:low_confidence")
        supporting = []

    # wrong_object: the claimed object's issue can't be determined from a different object -> unknown
    if status == NEI or "wrong_object" in signals:
        issue_type = "unknown"
    else:
        issue_type = facts.visible_issue_type
    object_part = _choose_object_part(facts, status, signals, claim.claim_object)
    severity = finalize_severity(status, issue_type, facts.severity_estimate, claim.claim_object)
    evidence_met_output = status != NEI  # matches the two sample NEI rows; satisfies invariant 1

    # authenticity flags reflect the SUPPORTING evidence (not an incidental context image).
    support_set = set(supporting)
    auth_flags: set[str] = set()
    for im in facts.images:
        if im.image_id in support_set:
            if im.authenticity == "non_original":
                auth_flags.add("non_original_image")
            elif im.authenticity == "possible_manipulation":
                auth_flags.add("possible_manipulation")

    # cross-claim image reuse on a SUPPORTING image is a fraud signal: the same exact
    # image was submitted under another claim. Treat as possible manipulation + force review.
    reuse_fraud = any(
        i.reused_in_cases for i in facts.images if i.image_id in support_set
    )
    # deterministic authenticity prior (EXIF/double-compression) on a SUPPORTING image —
    # corroborates possible_manipulation; off unless cfg.authenticity_prior, so no-op by default.
    manip_prior = any(
        i.manipulation_prior for i in facts.images if i.image_id in support_set
    )

    # risk flags: visual/quality/authenticity/contradiction/injection + history overlay (additive).
    # wrong_object_part did NOT drive a contradiction on an NEI row (the claimed part is not
    # assessable -> the gate downgraded it), so it must not surface as a contradiction risk flag.
    risk_signals = [s for s in signals if not (status == NEI and s == "wrong_object_part")]
    risk: set[str] = set(agg.quality_flags) | auth_flags | set(risk_signals)

    # cross-image disagreement among RELEVANT images (one shows damage, another shows none, or
    # concrete severities span >=2 bands) -> a human should look.
    _rel = [i for i in facts.images if i.relevant_to_claim and i.usable]
    _concrete = [i for i in _rel if i.visible_issue_type not in ("none", "unknown")]
    _band = {"none": 0, "low": 1, "medium": 2, "high": 3}
    _sevs = [_band[i.visible_severity] for i in _concrete if i.visible_severity in _band]
    cross_image_conflict = (bool(_concrete) and any(i.visible_issue_type == "none" for i in _rel)) or (
        len(_sevs) >= 2 and max(_sevs) - min(_sevs) >= 2)
    # a 'supported' verdict resting on a merely-UNKNOWN object match (tree only blocks on "false").
    unknown_match = status == SUPPORTED and facts.object_matches_claim == "unknown"
    # injection screen: VLM self-report OR a deterministic phrase screen (backstop, THREAT A1/A2).
    injection = facts.claim_text_instruction_present or detect_injection(
        claim.user_claim, *[im.image_text for im in facts.images])
    # history overlay computed with has_authenticity_flag=False so the manual_review_required
    # driver can be ATTRIBUTED (auth is attributed separately); the risk set is identical to
    # the prior single-call form.
    history_flags = set(history_overlay(history, has_authenticity_flag=False, th=th))

    if reuse_fraud or manip_prior:
        risk.add("possible_manipulation")
    if injection:
        risk.add("text_instruction_present")
    # damage_not_visible is contradictory with claim_mismatch: if claim_mismatch fired, the
    # damage IS visible but mismatches the claimed severity/nature, so don't also say it's not visible.
    if (facts.visible_issue_type in ("none", "unknown")
            and "wrong_object" not in signals
            and "claim_mismatch" not in signals):
        risk.add("damage_not_visible")
    if "wrong_object" in risk:
        risk.add("claim_mismatch")  # a wrong object necessarily mismatches the claim
    risk.update(history_flags)

    # manual_review_required, ATTRIBUTED per driver (§2 automation-ceiling KPI). Most MRR is
    # history-driven and LABEL-REQUIRED (sample case_017: a clean supported claim from a risky
    # user is still routed to review), so it is NOT decoupled here; the non-history drivers are
    # the tunable automation gap. Behaviour is identical to the prior combined overlay.
    mrr_drivers: list[str] = []
    if reuse_fraud:
        mrr_drivers.append("reuse")
    if manip_prior:
        mrr_drivers.append("authenticity_prior")
    if auth_flags:
        mrr_drivers.append("authenticity")
    if low_confidence_abstain:
        mrr_drivers.append("low_confidence")
    if facts.perception_disagreement:
        mrr_drivers.append("perception_disagreement")
    if cross_image_conflict:
        mrr_drivers.append("cross_image")
    if unknown_match:
        mrr_drivers.append("unknown_object_match")
    if injection:
        mrr_drivers.append("injection")
    if "manual_review_required" in history_flags:
        mrr_drivers.append("history")
    if mrr_drivers:
        risk.add("manual_review_required")
    risk_flags = normalize_risk_flags(list(risk))

    ev_reason, justification = build_explanations(
        facts, claim.claim_object, status, status_res.branch, issue_type, object_part, supporting
    )

    row = OutputRow(
        user_id=claim.user_id,
        image_paths=claim.image_paths,
        user_claim=claim.user_claim,
        claim_object=claim.claim_object,
        evidence_standard_met=evidence_met_output,
        evidence_standard_met_reason=ev_reason,
        risk_flags=risk_flags,
        issue_type=issue_type,
        object_part=object_part,
        claim_status=status,
        claim_status_justification=justification,
        supporting_image_ids=supporting,
        valid_image=agg.valid_image,
        severity=severity,
    )

    audit = {
        "branch": status_res.branch,
        "signals": signals,
        "evidence_gate_met": evidence.evidence_standard_met,
        "evidence_reason": evidence.reason,
        "part_assessable": facts.part_assessable,
        "object_matches_claim": facts.object_matches_claim,
        "visible_issue_type": facts.visible_issue_type,
        "visible_object_part": facts.visible_object_part,
        "vlm_confidence": facts.vlm_confidence,
        "valid_image": agg.valid_image,
        "authenticity_flags": sorted(auth_flags),
        "supporting_candidates": agg.supporting_candidates,
        "history_present": history is not None,
        "mrr_drivers": sorted(set(mrr_drivers)),  # why this row routed to manual review (automation-ceiling KPI)
    }
    return AssembledDecision(row=row, audit=audit)
