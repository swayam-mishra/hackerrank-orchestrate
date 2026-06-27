import re

# Conjunctions that often indicate multiple requests in support tickets
_SPLIT_PATTERNS = [
    r"\s+and also\s+",
    r"\s+also\s+",
    r"\s+plus\s+",
    r"\s+additionally\s+",
    r"\s+secondly\s+",
    r";\s+",
]

# Verb-bearing tokens — used to gate splits to true multi-action requests
_VERB_HINTS = {
    "delete", "remove", "cancel", "close", "refund", "reset", "update",
    "change", "fix", "resolve", "help", "want", "need", "request", "create",
    "send", "block", "report", "restore", "pause", "investigate", "process",
    "give", "show", "tell", "make", "explain", "set", "add", "share",
    "unban", "renew", "verify", "increase", "decrease", "stop", "start",
}


def _has_verb(clause: str) -> bool:
    words = {w.lower().rstrip(",.!?;:") for w in clause.split()}
    return bool(words & _VERB_HINTS)


def split_requests(text: str) -> list:
    """
    Split a ticket into N >= 1 sub-queries. Conservative: only splits when both
    halves contain at least one verb-bearing word, AND the split point is one of
    the strong conjunctions. Returns the original text as a single element if no
    confident split is found.
    """
    if not text or not text.strip():
        return [text or ""]

    # Try each split pattern in order; first match wins.
    for pat in _SPLIT_PATTERNS:
        parts = re.split(pat, text, maxsplit=2, flags=re.IGNORECASE)
        if len(parts) >= 2 and all(_has_verb(p) for p in parts):
            cleaned = [p.strip() for p in parts if p.strip()]
            if len(cleaned) >= 2:
                return cleaned

    # " and " is too noisy — only split on it if both halves are clearly imperative
    # (each ≥ 4 words AND each contains a verb).
    parts = re.split(r"\s+and\s+", text, maxsplit=1, flags=re.IGNORECASE)
    if len(parts) == 2:
        a, b = parts[0].strip(), parts[1].strip()
        if (
            len(a.split()) >= 4
            and len(b.split()) >= 4
            and _has_verb(a)
            and _has_verb(b)
        ):
            return [a, b]

    return [text]
