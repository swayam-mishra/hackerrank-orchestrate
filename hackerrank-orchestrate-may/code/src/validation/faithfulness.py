"""
Faithfulness scoring — verify high-risk claims in the response are grounded in
the retrieved chunks. Heuristic, not LLM-based: extracts numbers, quoted strings,
and capitalised multi-word phrases (likely product/feature names) and checks
that each appears verbatim or near-verbatim in any retrieved chunk.

Returns a ratio in [0, 1] (1 = all extracted claims found in chunks).
The agent records this in `_faithfulness_ratio` for observability + in the
decision trace, and surfaces low-ratio responses in the run summary.
"""
import re

# Numbers (currency, durations, counts, percentages)
_NUMBER = re.compile(r"\$?\d+(?:[.,]\d+)*(?:\s*(?:%|days?|hours?|months?|years?|business days?))?")
# Quoted strings (single or double quotes)
_QUOTED = re.compile(r'"([^"]{3,})"|\'([^\']{3,})\'')
# Capitalised multi-word phrases — usually product/feature names ("Resume Builder", "Bug Bounty Program")
_CAPITALISED_PHRASE = re.compile(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,4})\b")


def _extract_claims(response: str) -> dict:
    nums = set(_NUMBER.findall(response or ""))
    quoted = set()
    for m in _QUOTED.findall(response or ""):
        for g in m:
            if g:
                quoted.add(g.strip())
    phrases = set(_CAPITALISED_PHRASE.findall(response or ""))
    # Drop very generic phrases that aren't product-specific
    generic = {"Customer Service", "Bug Bounty", "User Account", "Support Team",
               "Bug Bounty Program", "Support", "Documentation"}
    phrases -= generic
    return {"numbers": nums, "quoted": quoted, "phrases": phrases}


def _claim_in_chunks(claim: str, chunk_blob_lower: str) -> bool:
    """Case-insensitive substring match. Strip surrounding punctuation."""
    needle = claim.strip().strip(".,;:!?\"'").lower()
    if len(needle) < 3:
        return True  # too short to meaningfully verify
    return needle in chunk_blob_lower


def score(response: str, chunks: list) -> dict:
    """
    Returns {ratio: float in [0,1], total_claims: int, unsupported: list[str]}.
    A ratio of 1.0 means every extracted claim was found in chunks. 0.0 means none.
    Empty response or empty chunks → ratio = 1.0 (vacuously true).
    """
    if not response or not chunks:
        return {"ratio": 1.0, "total_claims": 0, "unsupported": []}
    claims = _extract_claims(response)
    flat = (claims["numbers"]
            | claims["quoted"]
            | claims["phrases"])
    if not flat:
        return {"ratio": 1.0, "total_claims": 0, "unsupported": []}
    chunk_blob = " ".join(c.get("text", "") for c in chunks).lower()
    supported = []
    unsupported = []
    for claim in flat:
        if _claim_in_chunks(claim, chunk_blob):
            supported.append(claim)
        else:
            unsupported.append(claim)
    total = len(flat)
    ratio = len(supported) / total if total else 1.0
    return {"ratio": round(ratio, 3),
            "total_claims": total,
            "unsupported": sorted(unsupported)[:8]}  # cap to 8 for log brevity
