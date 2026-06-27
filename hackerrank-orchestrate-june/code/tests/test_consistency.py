"""check_consistency ordering: the VLM's reported contradiction_signals order
(most-certain-first) is preserved; the hardcoded priority order is only a fallback
when the VLM reported no signals. wrong_object derived from object_matches is prepended."""
from src.schema import PerceptionFacts
from src.decision.consistency import check_consistency


def facts(signals, object_matches="true"):
    return PerceptionFacts(
        user_id="u", claim_object="car",
        contradiction_signals=signals, object_matches_claim=object_matches,
    )


def test_vlm_order_preserved_not_resorted():
    # VLM is more certain of claim_mismatch -> it must stay first, NOT be re-sorted
    # back into the hardcoded ("...", "wrong_object_part", "claim_mismatch") order.
    r = check_consistency(facts(["claim_mismatch", "wrong_object_part"]))
    assert r.signals == ["claim_mismatch", "wrong_object_part"]


def test_vlm_order_preserved_reverse():
    r = check_consistency(facts(["wrong_object_part", "claim_mismatch"]))
    assert r.signals == ["wrong_object_part", "claim_mismatch"]


def test_wrong_object_prepended_from_object_match():
    # wrong_object derived from object_matches="false" leads the VLM's reported list.
    r = check_consistency(facts(["claim_mismatch"], object_matches="false"))
    assert r.signals == ["wrong_object", "claim_mismatch"]


def test_wrong_object_not_duplicated_when_already_reported():
    # Already reported by the VLM -> not prepended again; reported order is kept.
    r = check_consistency(facts(["claim_mismatch", "wrong_object"], object_matches="false"))
    assert r.signals == ["claim_mismatch", "wrong_object"]


def test_fallback_hardcoded_order_when_no_vlm_signals():
    # No VLM signals reported, but object_matches="false" -> fallback path yields wrong_object.
    r = check_consistency(facts([], object_matches="false"))
    assert r.signals == ["wrong_object"]


def test_empty_when_no_signals_and_object_matches():
    r = check_consistency(facts([], object_matches="true"))
    assert r.signals == []
