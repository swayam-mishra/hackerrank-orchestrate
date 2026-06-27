def check(issue_text: str, prefilter_result: dict, top_bm25_score: float, company: str = None) -> dict:
    # Trigger 1: injection attempt detected by prefilter
    if prefilter_result.get("reason") == "injection_attempt":
        return {"should_escalate": True, "reason": "injection_attempt", "status": "escalated"}

    # Trigger 2: corpus returned nothing — no basis for any answer
    if top_bm25_score == 0.0:
        return {"should_escalate": True, "reason": "empty_corpus_result", "status": "escalated"}

    return {"should_escalate": False, "reason": "ok", "status": None}
