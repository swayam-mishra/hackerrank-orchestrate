from pathlib import Path

from src.decision import taxonomy


def degrade(reason: str, chunks: list, issue: str, company, latency_ms: int) -> dict:
    """
    Build a safe minimal response from the top retrieved chunk. Used when LLM
    validation + repair fail, or the LLM produces unparseable output across all
    retries. Status stays 'replied' (the user gets *something*) but
    request_type='invalid' and justification flags the degradation.
    """
    if not chunks:
        # No retrieval to lean on — fall through to escalation handoff
        return {
            "status": "escalated",
            "product_area": "",
            "response": (
                "ESCALATED TO HUMAN AGENT\n\n"
                f"Reason: {reason}; no retrieved documentation available for safe fallback.\n\n"
                f"Original issue (preview): {issue[:200]}"
            ),
            "justification": f"Degraded path with no chunks: {reason}",
            "request_type": "invalid",
            "inferred_company": company or "",
            "latency_ms": latency_ms,
            "_degraded": True,
        }

    top = chunks[0]
    snippet = top.get("text", "").strip()
    if len(snippet) > 320:
        snippet = snippet[:320].rstrip() + "..."
    src = Path(top.get("source_file", "")).name or "documentation"
    response = (
        f"Based on documentation in {src}: {snippet} "
        f"For specifics tied to your account, please contact support directly."
    )
    pa = taxonomy.derive_from_chunks(chunks) or ""
    return {
        "status": "replied",
        "product_area": pa,
        "response": response,
        "justification": (
            f"Degraded response: {reason}. Using top retrieved chunk verbatim "
            "to avoid hallucination."
        ),
        "request_type": "invalid",
        "inferred_company": company or "",
        "latency_ms": latency_ms,
        "_degraded": True,
    }
