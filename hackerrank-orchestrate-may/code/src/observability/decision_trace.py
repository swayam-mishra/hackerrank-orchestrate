import json
import threading
from datetime import datetime

from src.config import DECISION_TRACE_PATH as _LOG_PATH
from src.pii import redact

_lock = threading.Lock()


def reset():
    """Truncate the trace file at the start of a fresh run."""
    if _LOG_PATH.exists():
        _LOG_PATH.unlink()


def trace(entry: dict):
    """Append one PII-redacted JSON line to the trace log."""
    safe = json.loads(json.dumps(entry, default=str))  # deep copy via JSON round-trip
    if isinstance(safe.get("issue_preview"), str):
        safe["issue_preview"] = redact(safe["issue_preview"])
    safe["timestamp"] = datetime.now().isoformat()
    with _lock:
        with open(_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(safe, ensure_ascii=False) + "\n")
