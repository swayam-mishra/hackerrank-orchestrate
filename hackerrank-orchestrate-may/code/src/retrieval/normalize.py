import re

# Abbreviations get expanded inline (replaces the abbreviation in-place).
ABBREVIATIONS = {
    r"\bhr\b": "hackerrank",
    r"\b2fa\b": "two-factor authentication",
    r"\bmfa\b": "multi-factor authentication",
    r"\bsso\b": "single sign-on",
    r"\blti\b": "learning tools interoperability",
    r"\bpwd\b": "password",
    r"\bacct\b": "account",
    r"\binfosec\b": "information security",
    r"\bats\b": "applicant tracking system",
}

# Synonyms get APPENDED to the query (so the original word stays for BM25 matching
# AND the synonym broadens recall). Kept tiny and inspectable.
SYNONYMS = {
    r"\bdelete\b":   ["remove", "cancel", "close"],
    r"\blost\b":     ["stolen", "missing"],
    r"\bstolen\b":   ["lost", "missing"],
    r"\brefund\b":   ["chargeback", "reimburse", "money back"],
    r"\bbroken\b":   ["not working", "failing", "failed"],
    r"\blogin\b":    ["sign in", "authenticate"],
    r"\breschedule\b": ["change date", "move appointment"],
    r"\bblocked\b":  ["disabled", "frozen", "locked"],
}


def normalize_query(text: str) -> str:
    out = text.lower()
    expansions = []
    # Abbreviations: keep the original token (corpus may use the abbreviation)
    # AND append the expansion so we get both forms in the BM25 query.
    for pattern, replacement in ABBREVIATIONS.items():
        if re.search(pattern, out):
            expansions.append(replacement)
    # Synonyms: append related terms to broaden recall.
    for pattern, syns in SYNONYMS.items():
        if re.search(pattern, out):
            expansions.extend(syns)
    if expansions:
        out = out + " " + " ".join(expansions)
    return re.sub(r"\s+", " ", out).strip()
