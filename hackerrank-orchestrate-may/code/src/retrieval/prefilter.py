import re
import threading

from langdetect import detect, DetectorFactory, LangDetectException

DetectorFactory.seed = 0
_langdetect_lock = threading.Lock()

INJECTION_PHRASES = [
    # basic
    "ignore previous instructions",
    "ignore prior instructions",
    "disregard",
    "you are now",
    "forget your instructions",
    "act as",
    "jailbreak",
    "pretend you are",
    "new instructions",
    "override instructions",
    # advanced — role-play, system-tag injection, indirection
    "system:",
    "reveal your prompt",
    "as an admin",
    "override system",
    "developer mode",
    "<|im_start|>",
    "### system",
    "show me your system prompt",
    "print your instructions",
]

_JUNK_ALPHA_RATIO_THRESHOLD = 0.4  # below this fraction of alphanumeric chars = junk
_JUNK_MIN_WORDS = 2                 # fewer real words than this = junk
_VERY_SHORT_WORDS = 3                # < this many words → low_signal flag (not a shortcircuit)
_VERY_SHORT_CHARS = 15
_BASE64_BLOB_RE = re.compile(r"^[A-Za-z0-9+/=]{40,}$")


def prefilter(text: str) -> dict:
    if not text or not text.strip():
        return {"is_valid": False, "reason": "empty", "should_shortcircuit": True,
                "low_signal": False}

    stripped = text.strip()
    lower = stripped.lower()

    for phrase in INJECTION_PHRASES:
        if phrase in lower:
            return {"is_valid": False, "reason": "injection_attempt",
                    "should_shortcircuit": True, "low_signal": False}

    # Opaque base64-style blobs without spaces/words: treat as junk (don't try to decode).
    if _BASE64_BLOB_RE.match(stripped.replace("\n", "")):
        return {"is_valid": False, "reason": "junk",
                "should_shortcircuit": True, "low_signal": False}

    total_chars = len(text.replace(" ", ""))
    alpha_chars = sum(1 for c in text if c.isalnum())
    if total_chars > 0 and (alpha_chars / total_chars) < _JUNK_ALPHA_RATIO_THRESHOLD:
        return {"is_valid": False, "reason": "junk",
                "should_shortcircuit": True, "low_signal": False}

    words = re.findall(r"[a-zA-Z]{2,}", text)
    if len(words) < _JUNK_MIN_WORDS:
        return {"is_valid": False, "reason": "junk",
                "should_shortcircuit": True, "low_signal": False}

    try:
        with _langdetect_lock:
            lang = detect(text)
        if lang != "en":
            return {"is_valid": False, "reason": "non_english",
                    "should_shortcircuit": True, "low_signal": False}
    except LangDetectException:
        return {"is_valid": False, "reason": "junk",
                "should_shortcircuit": True, "low_signal": False}

    # Non-blocking very-short signal — proceed but mark for low-confidence prompt path
    low_signal = len(words) < _VERY_SHORT_WORDS or len(stripped) < _VERY_SHORT_CHARS

    return {"is_valid": True, "reason": "ok",
            "should_shortcircuit": False, "low_signal": low_signal}
