"""PURE — user-history risk overlay. ADDITIVE only: returns a subset of
{user_history_risk, manual_review_required}. It does NOT take or affect claim_status,
so 'history never overrides clear visual evidence' is structural (sample case_017
stays supported while still carrying both flags).

Primary signal: explicit history_flags tokens. Secondary: a few interpretable,
bounded numeric thresholds (from config) that ADD user_history_risk when the explicit
flag is absent but the numbers are extreme. Elevated user risk co-occurs with
manual_review_required in every labeled row, so user_history_risk => manual_review_required.
No I/O, no globals."""
from __future__ import annotations

from src.config import Thresholds
from src.io.reader import HistoryRow
from src.schema import RiskFlag


def history_overlay(
    history: HistoryRow | None,
    has_authenticity_flag: bool,
    th: Thresholds,
) -> list[RiskFlag]:
    flags: set[RiskFlag] = set()
    if history is not None:
        has_uhr_token = "user_history_risk" in history.history_flags
        has_mrr_token = "manual_review_required" in history.history_flags
        denom = max(history.past_claim_count, 1)
        numeric_risk = (
            history.rejected_claim / denom >= th.history_rejection_rate_min
            or history.last_90_days_claim_count >= th.history_recent_burst_min
            or history.manual_review_claim / denom >= th.history_review_rate_min
        )
        if has_uhr_token or numeric_risk:
            flags.add("user_history_risk")
        if has_uhr_token or has_mrr_token or numeric_risk:
            flags.add("manual_review_required")

    if has_authenticity_flag:
        flags.add("manual_review_required")

    return sorted(flags)
