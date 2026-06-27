"""Input readers. Uses the stdlib `csv` module so the four input fields round-trip
byte-for-byte (semicolons, pipes, Hinglish) — never reformatted. Typed models out,
never loose dicts (ENGINEERING_CONVENTIONS §4)."""
from __future__ import annotations

import csv
import re
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from src.schema import ClaimObject

_CASE_RE = re.compile(r"(case_\d+)")


class ClaimInput(BaseModel):
    """One input row, carried verbatim (the 4 fields are echoed unchanged to output)."""
    model_config = ConfigDict(extra="forbid")
    user_id: str
    image_paths: str
    user_claim: str
    claim_object: ClaimObject
    # Stable, guaranteed-unique claim identity, assigned at read time (read_claims) and
    # used as the cache/checkpoint/audit key. Empty when a ClaimInput is built ad-hoc
    # (tests, single-case debug); uid() then derives one. NEVER scrape identity by regex.
    claim_id: str = ""

    def image_rel_paths(self) -> list[str]:
        return [p.strip() for p in self.image_paths.split(";") if p.strip()]

    def image_ids(self) -> list[str]:
        return [Path(p).stem for p in self.image_rel_paths()]

    def case_id(self) -> str:
        """Human-facing case label scraped from the path (e.g. 'case_008'). Used for the
        eval label-join only — NOT guaranteed unique across arbitrary inputs (it falls back
        to user_id and can collide). Use uid() as the cache/checkpoint key."""
        m = _CASE_RE.search(self.image_paths)
        return m.group(1) if m else self.user_id

    def uid(self) -> str:
        """Guaranteed-unique identity for cache/checkpoint/audit. Prefers the explicit
        claim_id assigned by read_claims; falls back to case_id() for ad-hoc construction."""
        return self.claim_id or self.case_id()


class HistoryRow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_id: str
    past_claim_count: int = 0
    accept_claim: int = 0
    manual_review_claim: int = 0
    rejected_claim: int = 0
    last_90_days_claim_count: int = 0
    history_flags: list[str] = []
    history_summary: str = ""


class EvidenceRule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    requirement_id: str
    claim_object: str           # car | laptop | package | all
    applies_to: str
    minimum_image_evidence: str


def read_claims(path: Path) -> list[ClaimInput]:
    out: list[ClaimInput] = []
    seen: dict[str, int] = {}
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            m = _CASE_RE.search(row["image_paths"])
            base = m.group(1) if m else row["user_id"]
            # Guarantee uniqueness: the FIRST claim for a base keeps it verbatim (so existing
            # 'case_NNN' cache files stay valid); genuine collisions get a positional suffix.
            n = seen.get(base, 0)
            seen[base] = n + 1
            claim_id = base if n == 0 else f"{base}__{n}"
            out.append(ClaimInput(
                user_id=row["user_id"],
                image_paths=row["image_paths"],
                user_claim=row["user_claim"],
                claim_object=row["claim_object"],  # validated by the Literal
                claim_id=claim_id,
            ))
    return out


def read_history(path: Path) -> dict[str, HistoryRow]:
    out: dict[str, HistoryRow] = {}
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            flags = [t for t in row["history_flags"].split(";") if t and t != "none"]
            out[row["user_id"]] = HistoryRow(
                user_id=row["user_id"],
                past_claim_count=_int(row["past_claim_count"]),
                accept_claim=_int(row["accept_claim"]),
                manual_review_claim=_int(row["manual_review_claim"]),
                rejected_claim=_int(row["rejected_claim"]),
                last_90_days_claim_count=_int(row["last_90_days_claim_count"]),
                history_flags=flags,
                history_summary=row["history_summary"],
            )
    return out


def read_evidence_rules(path: Path) -> list[EvidenceRule]:
    with open(path, newline="", encoding="utf-8") as f:
        return [EvidenceRule(**{k: row[k] for k in EvidenceRule.model_fields}) for row in csv.DictReader(f)]


def read_sample_labels(path: Path) -> dict[str, dict[str, str]]:
    """Return {case_id: {label_column: value}} for the 10 prediction columns, raw strings.
    Used by the evaluation harness only."""
    label_cols = (
        "evidence_standard_met", "evidence_standard_met_reason", "risk_flags", "issue_type",
        "object_part", "claim_status", "claim_status_justification", "supporting_image_ids",
        "valid_image", "severity",
    )
    out: dict[str, dict[str, str]] = {}
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            m = _CASE_RE.search(row["image_paths"])
            key = m.group(1) if m else row["user_id"]
            out[key] = {c: row[c] for c in label_cols}
    return out


def _int(s: str) -> int:
    try:
        return int(str(s).strip())
    except (ValueError, TypeError):
        return 0
