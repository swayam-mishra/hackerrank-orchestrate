import json
import threading
from datetime import datetime

from src.config import FAILURES_LOG_PATH as _LOG_PATH
from src.pii import redact

_lock = threading.Lock()


def log_failure(ticket_idx: int, error_type: str, message: str, issue_preview: str = ""):
    entry = {
        "timestamp": datetime.now().isoformat(),
        "ticket_idx": ticket_idx,
        "error_type": error_type,
        "message": redact(message),
        "issue_preview": redact(issue_preview[:120]),
    }
    with _lock:
        with open(_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
