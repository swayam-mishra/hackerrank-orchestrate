import re
from pathlib import Path

from src.decision import taxonomy

_REQUIRED = {"status", "product_area", "response", "justification",
             "request_type", "inferred_company"}
_VALID_STATUS = {"replied", "escalated"}
_VALID_REQUEST_TYPE = {"product_issue", "feature_request", "bug", "invalid"}
# Only match actual filenames with extensions — avoids "according to the documentation"
# false positives.
_CITATION_RE = re.compile(
    r"according to ([\w./-]+?\.(?:md|txt|csv|html|json|yaml|yml))",
    re.IGNORECASE,
)


def _build_repair_hint(errors):
    if not errors:
        return ""
    return (
        "Your previous JSON had these problems: "
        + "; ".join(errors)
        + ". Fix them. Respond with valid JSON only — no markdown, no commentary."
    )


def validate(result, chunks, company, issue: str = ""):
    """
    Returns {valid: bool, errors: list[str], hint: str, repaired: bool}.
    May mutate `result` in place to apply non-LLM repairs (path-derived
    product_area, schema defaults). Errors are categorised:
      - blocking (require LLM repair): missing_field, invalid_status,
        invalid_request_type, escalated_with_product_area, replied_with_empty_response
      - soft (informational only): phantom_citation, product_area_repaired,
        product_area_off_taxonomy
    """
    errors = []
    repaired = False

    # 3a. Schema check — fill missing fields with empty defaults
    for f in _REQUIRED - set(result.keys()):
        errors.append(f"missing_field:{f}")
        result[f] = ""

    # 3b. Enum check
    if result.get("status") not in _VALID_STATUS:
        errors.append(f"invalid_status:{result.get('status')!r}")
    if result.get("request_type") not in _VALID_REQUEST_TYPE:
        errors.append(f"invalid_request_type:{result.get('request_type')!r}")

    # 3c. Consistency
    if result.get("status") == "escalated" and result.get("product_area"):
        errors.append("escalated_with_product_area")
    if result.get("status") == "replied" and not str(result.get("response", "")).strip():
        errors.append("replied_with_empty_response")

    # 3d. Taxonomy — try path-derived repair first
    pa = str(result.get("product_area", "")).strip().lower()
    if pa:
        allowed = taxonomy.allowed_for(company)
        if pa not in allowed:
            derived = taxonomy.derive_from_chunks(chunks)
            if derived and derived in taxonomy.ALL:
                result["product_area"] = derived
                errors.append(f"product_area_repaired:{pa}->{derived}")
                repaired = True
            else:
                errors.append(f"product_area_off_taxonomy:{pa}")

    # 3d2. Deterministic post-mapping corrections (keyword rules)
    pa_after_taxonomy = str(result.get("product_area", "")).strip().lower()
    if pa_after_taxonomy and result.get("status") == "replied" and issue:
        corrected = taxonomy.apply_corrections(pa_after_taxonomy, issue, company)
        if corrected != pa_after_taxonomy:
            result["product_area"] = corrected
            errors.append(f"product_area_keyword_corrected:{pa_after_taxonomy}->{corrected}")
            repaired = True

    # 3e. Phantom citation
    chunk_names = {Path(c.get("source_file", "")).name.lower() for c in chunks}
    response_text = str(result.get("response", "")).lower()
    for raw_cite in _CITATION_RE.findall(response_text):
        base = raw_cite.split("/")[-1].rstrip(".,;:)")
        if base and base not in chunk_names:
            errors.append(f"phantom_citation:{base}")

    blocking = [e for e in errors if e.split(":", 1)[0] in {
        "missing_field", "invalid_status", "invalid_request_type",
        "escalated_with_product_area", "replied_with_empty_response",
    }]

    return {
        "valid": len(blocking) == 0,
        "errors": errors,
        "blocking": blocking,
        "hint": _build_repair_hint(blocking),
        "repaired": repaired,
    }
