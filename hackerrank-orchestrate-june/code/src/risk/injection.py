"""PURE — deterministic prompt-injection / instruction screen over UNTRUSTED text
(the claim conversation + any VLM-transcribed in-image text).

This is the deterministic BACKSTOP promised in THREAT_MODEL §3.3 / A1-A2: the VLM is
asked to self-report instruction-like text via `claim_text_instruction_present`, but a
single model boolean is a single point of failure on the very input being attacked. This
screen OR's a code-side phrase match into that signal so a missed flag is still caught.

Patterns are deliberately TIGHT (clear imperative override phrases) to avoid false
positives on legitimate damage descriptions. Matching is case-insensitive. No I/O, no
globals. The decision layer maps a hit to `text_instruction_present` + `manual_review_required`.
"""
from __future__ import annotations

import re

# Each pattern targets an unambiguous attempt to steer the verdict or override authority.
# Kept narrow on purpose: "mark as fragile" / "approved by sender" style benign text must NOT match.
_INJECTION_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\bignore\s+(?:all\s+|any\s+|the\s+)?(?:previous|prior|above|earlier)\s+instructions?\b",
        r"\bdisregard\s+(?:all\s+|any\s+|the\s+)?(?:previous|prior|above|earlier|system)\b",
        r"\bapprove\s+(?:this|the|my)\s+claim\b",
        r"\bmark\s+(?:this|it|the\s+claim)\s+as\s+(?:supported|approved|valid|verified)\b",
        r"\bset\s+(?:the\s+)?(?:claim[_\s]?)?status\s+to\b",
        r"\byou\s+(?:must|should|need\s+to|have\s+to)\s+(?:approve|support|accept|mark|pass)\b",
        r"\boverride\s+(?:the\s+)?(?:decision|system|instructions?|review)\b",
        r"\bthis\s+(?:claim\s+)?(?:is|has\s+been)\s+(?:already\s+)?(?:verified|approved|pre[-\s]?approved)\b",
        r"\bdo\s+not\s+(?:flag|reject|deny|review|question)\b",
        r"(?:^|\n)\s*(?:system|assistant|developer)\s*:",   # role-injection header
    )
)


def find_injection_phrases(*texts: str) -> list[str]:
    """Return the de-duplicated, order-preserving list of matched injection snippets across
    all provided texts (claim conversation, transcribed image text). Empty if none match."""
    found: list[str] = []
    for text in texts:
        if not text:
            continue
        for pat in _INJECTION_PATTERNS:
            for m in pat.finditer(text):
                snippet = m.group(0).strip()
                if snippet and snippet not in found:
                    found.append(snippet)
    return found


def detect_injection(*texts: str) -> bool:
    """True if any UNTRUSTED text contains an instruction-injection / override phrase."""
    return bool(find_injection_phrases(*texts))
