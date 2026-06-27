from pathlib import Path

# Allowed product_area values per company. Derived from data/ subdirectories,
# sample CSV labels, and observed Phase 4 outputs that scored well.
ALLOWED = {
    "hackerrank": {
        "screen", "interviews", "library", "settings", "integrations",
        "skillup", "engage", "chakra", "general_help", "community",
        "certifications", "mock_interviews", "payments_and_billing",
        "account", "team_management", "resume_builder",
        "hackerrank_community", "uncategorized",
    },
    "claude": {
        "account", "conversation_management", "privacy", "safeguards",
        "claude_for_education", "claude_code", "claude_api",
        "claude_desktop", "claude_mobile", "claude_in_chrome",
        "amazon_bedrock", "connectors", "identity_management",
        "team_and_enterprise", "pro_and_max", "claude_for_government",
        "claude_for_nonprofits", "billing", "troubleshooting",
        "privacy_and_legal", "claude_api_and_console",
    },
    "visa": {
        "travel_support", "general_support", "merchant_rules",
        "dispute", "security", "credit_cards",
    },
}

# Generic organisational dirs that should NOT be used as product_area values,
# even though they appear in the corpus path tree.
_PATH_SKIP = {"support", "data", "consumer", "small_business", "small-business",
              "claude"}

ALL = set().union(*ALLOWED.values())

_COMPANIES_IN_PATH = {"hackerrank", "claude", "visa"}


def allowed_for(company):
    if not company:
        return ALL
    return ALLOWED.get(company.strip().lower(), ALL)


def derive_from_chunks(chunks):
    """Take the top chunk's source_file path; walk past the company segment and
    any generic organisational dirs; return the first topical subdir
    (lowercased, hyphens → underscores). Falls back to "" if no candidate."""
    if not chunks:
        return ""
    parts = [p.lower() for p in Path(chunks[0].get("source_file", "")).parts]
    in_company = False
    for p in parts:
        if p in _COMPANIES_IN_PATH:
            in_company = True
            continue
        if not in_company:
            continue
        if p in _PATH_SKIP:
            continue
        return p.replace("-", "_")
    return ""


def format_for_prompt(company):
    """Render a comma-separated list for inclusion in the system prompt."""
    if company and company.strip().lower() in ALLOWED:
        items = sorted(ALLOWED[company.strip().lower()])
    else:
        items = sorted(ALL)
    return ", ".join(items)


# ── Disambiguation hints injected alongside the taxonomy list ──────────────
_HINTS = {
    "hackerrank": (
        "Disambiguation: use `community` when the ticket is about the HackerRank "
        "Community platform, community profiles, or community account issues (even if "
        "the user also mentions their account). Use `account` only for HackerRank for "
        "Work / hiring accounts."
    ),
    "claude": (
        "Disambiguation: use `privacy` when the ticket involves personal data, private "
        "information, data retention, or GDPR concerns. Use `conversation_management` "
        "only when the ticket is specifically about managing chat history (creating, "
        "deleting, or renaming conversations)."
    ),
    "visa": (
        "Disambiguation: use `general_support` for lost/stolen card reports, general "
        "card enquiries, and card blocking. Use `security` only for identity theft or "
        "account compromise beyond a lost card."
    ),
}


def hints_for_prompt(company):
    """Return optional disambiguation hint for the given company, or empty string."""
    if not company:
        return ""
    return _HINTS.get(company.strip().lower(), "")


# ── Post-LLM deterministic correction rules ────────────────────────────────
_COMMUNITY_SIGNALS = ["hackerrank community", "community profile", "community account",
                      "signed up using google login on hackerrank community"]
_PRIVACY_SIGNALS = ["private info", "personal info", "private data", "personal data",
                    "data retention", "my data", "use my data"]
_VISA_GENERAL_SIGNALS = ["lost card", "stolen card", "report a lost", "report a stolen",
                         "card stolen", "card lost", "card was stolen", "card was lost",
                         "block my card", "block the card"]


def apply_corrections(product_area: str, issue: str, company: str) -> str:
    """
    Deterministic post-LLM corrections for known misclassification patterns.
    Applied in the validator after taxonomy checks.
    """
    if not issue or not company:
        return product_area
    lower = issue.lower()
    co = company.strip().lower()

    # HackerRank: 'community' platform signals override generic 'account'
    if co == "hackerrank" and product_area == "account":
        if any(sig in lower for sig in _COMMUNITY_SIGNALS):
            return "community"

    # Claude: 'privacy' when issue is about personal/private data
    if co == "claude" and product_area == "conversation_management":
        if any(sig in lower for sig in _PRIVACY_SIGNALS):
            return "privacy"

    # Visa: 'general_support' for card loss/theft (not identity theft)
    if co == "visa" and product_area == "security":
        identity_theft = "identity" in lower or "identity theft" in lower
        if not identity_theft and any(sig in lower for sig in _VISA_GENERAL_SIGNALS):
            return "general_support"

    return product_area
