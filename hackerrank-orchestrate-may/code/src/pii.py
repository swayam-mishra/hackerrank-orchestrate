import re

EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE = re.compile(r"(?:\+\d{1,3}[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}")
LONG_ID = re.compile(r"\b[A-Za-z0-9_-]{20,}\b")  # session tokens, UUIDs, cs_live_*


def redact(text: str) -> str:
    if not text:
        return text
    text = EMAIL.sub("[EMAIL]", text)
    text = PHONE.sub("[PHONE]", text)
    text = LONG_ID.sub("[ID]", text)
    return text
