"""Schema is the single source of truth: assert literals == spec lists, invariants
fire, serialization is correct, and the tool schema is strict."""
import pytest
from pydantic import ValidationError

from src.schema import (
    CLAIM_STATUS_VALUES, ISSUE_TYPE_VALUES, OBJECT_PART_VALUES, OUTPUT_COLUMNS,
    PARTS_BY_OBJECT, RISK_FLAG_VALUES, SEVERITY_VALUES, OutputRow,
    normalize_risk_flags, submit_decision_tool_schema,
)

# Spec lists copied verbatim from problem_statement.md (the guard against accidental edits).
SPEC_CLAIM_STATUS = {"supported", "contradicted", "not_enough_information"}
SPEC_ISSUE = {"dent", "scratch", "crack", "glass_shatter", "broken_part", "missing_part",
              "torn_packaging", "crushed_packaging", "water_damage", "stain", "none", "unknown"}
SPEC_SEVERITY = {"none", "low", "medium", "high", "unknown"}
SPEC_RISK = {"none", "blurry_image", "cropped_or_obstructed", "low_light_or_glare", "wrong_angle",
             "wrong_object", "wrong_object_part", "damage_not_visible", "claim_mismatch",
             "possible_manipulation", "non_original_image", "text_instruction_present",
             "user_history_risk", "manual_review_required"}
SPEC_CAR = {"front_bumper", "rear_bumper", "door", "hood", "windshield", "side_mirror",
            "headlight", "taillight", "fender", "quarter_panel", "body", "unknown"}
SPEC_LAPTOP = {"screen", "keyboard", "trackpad", "hinge", "lid", "corner", "port", "base", "body", "unknown"}
SPEC_PACKAGE = {"box", "package_corner", "package_side", "seal", "label", "contents", "item", "unknown"}


def test_literals_match_spec():
    assert set(CLAIM_STATUS_VALUES) == SPEC_CLAIM_STATUS
    assert set(ISSUE_TYPE_VALUES) == SPEC_ISSUE
    assert set(SEVERITY_VALUES) == SPEC_SEVERITY
    assert set(RISK_FLAG_VALUES) == SPEC_RISK
    assert PARTS_BY_OBJECT["car"] == SPEC_CAR
    assert PARTS_BY_OBJECT["laptop"] == SPEC_LAPTOP
    assert PARTS_BY_OBJECT["package"] == SPEC_PACKAGE
    assert set(OBJECT_PART_VALUES) == SPEC_CAR | SPEC_LAPTOP | SPEC_PACKAGE


def test_output_columns_order():
    assert OUTPUT_COLUMNS == (
        "user_id", "image_paths", "user_claim", "claim_object",
        "evidence_standard_met", "evidence_standard_met_reason", "risk_flags",
        "issue_type", "object_part", "claim_status", "claim_status_justification",
        "supporting_image_ids", "valid_image", "severity",
    )


def _row(**kw):
    base = dict(user_id="u", image_paths="images/test/case_x/img_1.jpg", user_claim="c", claim_object="car",
               evidence_standard_met=True, evidence_standard_met_reason="r", risk_flags=["none"],
               issue_type="dent", object_part="rear_bumper", claim_status="supported",
               claim_status_justification="j", supporting_image_ids=["img_1"], valid_image=True, severity="medium")
    base.update(kw)
    return OutputRow(**base)


def test_valid_supported_and_csv_order():
    r = _row()
    assert tuple(r.to_csv_dict().keys()) == OUTPUT_COLUMNS
    assert r.to_csv_dict()["evidence_standard_met"] == "true"
    assert r.to_csv_dict()["risk_flags"] == "none"


def test_case008_valid_image_false_contradicted_allowed():
    r = _row(issue_type="broken_part", object_part="front_bumper", claim_status="contradicted",
             risk_flags=["claim_mismatch", "non_original_image"], valid_image=False, severity="high")
    assert r.claim_status == "contradicted" and r.valid_image is False


@pytest.mark.parametrize("kw", [
    dict(claim_status="supported", issue_type="none"),                      # supported needs concrete issue
    dict(claim_status="supported", issue_type="unknown"),
    dict(claim_status="not_enough_information", supporting_image_ids=["img_1"]),  # NEI must have no support
    dict(claim_status="not_enough_information", evidence_standard_met=False, supporting_image_ids=[], severity="low"),  # NEI->unknown
    dict(claim_status="contradicted", supporting_image_ids=[]),             # non-NEI must have support
    dict(claim_object="laptop", object_part="rear_bumper"),                 # wrong part for object
    dict(evidence_standard_met=False, claim_status="supported"),            # not-met must be NEI
])
def test_invariants_raise(kw):
    with pytest.raises(ValidationError):
        _row(**kw)


def test_risk_normalization():
    assert normalize_risk_flags(["none", "claim_mismatch", "none", "blurry_image", "claim_mismatch"]) == ["blurry_image", "claim_mismatch"]
    assert normalize_risk_flags([]) == ["none"]
    assert normalize_risk_flags(["none"]) == ["none"]


def test_tool_schema_is_strict():
    sch = submit_decision_tool_schema()
    assert sch["additionalProperties"] is False
    assert set(sch["required"]) == set(sch["properties"].keys())
    # nested $defs objects are also strictified
    for d in sch.get("$defs", {}).values():
        if d.get("type") == "object":
            assert d["additionalProperties"] is False
