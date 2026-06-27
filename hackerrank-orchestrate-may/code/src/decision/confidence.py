import math
from collections import Counter


def score(rerank_scores, chunks, company):
    """
    Quantify retrieval confidence on [0, 1].
    Components:
      norm_top: sigmoid of cross-encoder top logit (relevance signal)
      gap:     normalised separation between top-1 and top-2 (decisiveness)
      match:   fraction of chunks whose company tag matches input company
               (or dominance of a single company when input is unknown)
    Weighted blend: 0.5 * norm_top + 0.3 * gap + 0.2 * match.
    Returns dict with value, bucket, components.
    """
    if not rerank_scores or not chunks:
        return {"value": 0.0, "bucket": "low",
                "components": {"top": 0.0, "gap": 0.0, "match": 0.0}}

    top = float(rerank_scores[0])
    second = float(rerank_scores[1]) if len(rerank_scores) > 1 else top - 5.0

    norm_top = 1.0 / (1.0 + math.exp(-top))
    gap = max(0.0, min(1.0, (top - second) / 5.0))

    if company:
        norm_co = company.strip().lower()
        match = sum(
            1 for c in chunks if c.get("company", "").lower() == norm_co
        ) / len(chunks)
    else:
        counts = Counter(c.get("company", "") for c in chunks)
        match = max(counts.values()) / len(chunks) if counts else 0.0

    value = 0.5 * norm_top + 0.3 * gap + 0.2 * match
    if value >= 0.7:
        bucket = "high"
    elif value >= 0.4:
        bucket = "medium"
    else:
        bucket = "low"

    return {
        "value": round(value, 3),
        "bucket": bucket,
        "components": {
            "top": round(norm_top, 3),
            "gap": round(gap, 3),
            "match": round(match, 3),
        },
    }
