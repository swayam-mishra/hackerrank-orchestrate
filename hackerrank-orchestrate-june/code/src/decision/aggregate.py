"""PURE — multi-image aggregation: valid_image, quality/authenticity flag union, and
the supporting-image candidate set (validating the VLM's nomination). No I/O, no globals.

valid_image (usable/authentic for automated review) is INDEPENDENT of evidence
sufficiency: a non-original image can still be evidence_standard_met (sample case_008).
Mild blur does not invalidate a set if a clear image exists (case_007)."""
from __future__ import annotations

from dataclasses import dataclass

from src.schema import ImageFact, PerceptionFacts


@dataclass(frozen=True)
class AggregateResult:
    valid_image: bool
    quality_flags: list[str]        # blurry_image, cropped_or_obstructed, low_light_or_glare, wrong_angle
    authenticity_flags: list[str]   # non_original_image, possible_manipulation
    supporting_candidates: list[str]


def _review_ok(i: ImageFact) -> bool:
    return i.usable and i.authenticity == "original"


def aggregate_images(facts: PerceptionFacts) -> AggregateResult:
    images = facts.images
    usable_ids = {i.image_id for i in images if i.usable}

    valid_image = any(_review_ok(i) for i in images)

    qflags: set[str] = set()
    for i in images:
        qflags.update(i.quality_flags)

    auth: set[str] = set()
    if any(i.authenticity == "non_original" for i in images):
        auth.add("non_original_image")
    if any(i.authenticity == "possible_manipulation" for i in images):
        auth.add("possible_manipulation")

    # Supporting candidates: trust the VLM's nomination, but only keep usable + relevant ids.
    relevant_ids = {i.image_id for i in images if i.usable and i.relevant_to_claim}
    nominated = [x for x in facts.vlm_supporting_image_ids if x in usable_ids and x in relevant_ids]
    if not nominated:
        # fallback: relevant+usable images with a visual cue, else any relevant usable. Collapse
        # near-duplicates (THREAT_MODEL B4): a padded copy of an earlier image is not fresh
        # evidence, so don't nominate it when its original is present.
        fresh = [i for i in images if i.usable and i.relevant_to_claim and not i.duplicate_of]
        cued = [i.image_id for i in fresh if i.visual_cue.strip()]
        rel = [i.image_id for i in fresh]
        nominated = cued or rel
    return AggregateResult(
        valid_image=valid_image,
        quality_flags=sorted(qflags),
        authenticity_flags=sorted(auth),
        supporting_candidates=_dedupe(nominated),
    )


def _dedupe(xs: list[str]) -> list[str]:
    out: list[str] = []
    for x in xs:
        if x not in out:
            out.append(x)
    return out
