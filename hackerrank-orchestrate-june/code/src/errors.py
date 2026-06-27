"""PURE — a tiny structured error taxonomy so per-row failures are classified into a small
enum (for dashboards/alerting) instead of opaque stringified exceptions. No I/O, no deps."""
from __future__ import annotations

ERROR_CLASSES = (
    "no_usable_images", "decode_fail", "api_error", "rate_limit",
    "timeout", "validation_fail", "perception_finalize", "unknown",
)


def classify_error(err: object) -> str:
    """Map an Exception (or an error string) to one of ERROR_CLASSES. Best-effort, never raises."""
    name = type(err).__name__ if isinstance(err, BaseException) else ""
    text = (f"{name}: {err}" if isinstance(err, BaseException) else str(err or "")).lower()
    if not text:
        return "unknown"
    if "no usable image" in text:
        return "no_usable_images"
    if "timeout" in text or name.endswith("TimeoutError"):
        return "timeout"
    if "ratelimit" in text or "rate limit" in text or "429" in text:
        return "rate_limit"
    if name == "ValidationError" or "validation" in text:
        return "validation_fail"
    if "finalize" in text:
        return "perception_finalize"
    if name.startswith("API") or any(k in text for k in ("apistatuserror", "apiconnection", "anthropic", "status code")):
        return "api_error"
    if any(k in text for k in ("cannot identify image", "unidentifiedimage", "decode", "truncated", "cannot open")):
        return "decode_fail"
    return "unknown"
