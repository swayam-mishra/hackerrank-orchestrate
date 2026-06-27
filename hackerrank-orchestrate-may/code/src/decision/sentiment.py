FRUSTRATED_KEYWORDS = [
    "asap",
    "urgent",
    "immediately",
    "ridiculous",
    "unacceptable",
    "frustrated",
    "give me my money",
    "right now",
    "fix this",
]


def classify(text: str) -> str:
    if not text:
        return "neutral"
    lower = text.lower()
    if any(kw in lower for kw in FRUSTRATED_KEYWORDS):
        return "frustrated"
    if text.count("!") >= 2:
        return "frustrated"
    caps_words = [w for w in text.split() if len(w) >= 4 and w.isupper()]
    if len(caps_words) >= 2:
        return "frustrated"
    return "neutral"
