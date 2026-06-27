"""
Coverage gap logging. Appends one JSONL line to support_tickets/coverage_gaps.log
when a ticket's retrieval confidence is low — useful for spotting topics where
the corpus is thin and answering "where does my agent struggle?" at the
interview with data, not guesses.
"""
import json
import threading
from datetime import datetime

from src.config import COVERAGE_LOG_PATH as _LOG_PATH
from src.pii import redact

_lock = threading.Lock()

# Threshold below which a ticket is considered a coverage gap.
_THRESHOLD = 0.4


def reset():
    if _LOG_PATH.exists():
        _LOG_PATH.unlink()


def maybe_log(ticket_idx: int, confidence_value: float, issue_preview: str,
              top_sources: list, company: str | None):
    if confidence_value is None or confidence_value >= _THRESHOLD:
        return
    entry = {
        "timestamp": datetime.now().isoformat(),
        "ticket_idx": ticket_idx,
        "confidence": confidence_value,
        "company": company or "",
        "top_sources": top_sources[:3] if top_sources else [],
        "issue_preview": redact(issue_preview[:160]) if issue_preview else "",
    }
    with _lock:
        with open(_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
