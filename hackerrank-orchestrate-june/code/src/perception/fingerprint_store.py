"""Durable cross-claim image-fingerprint store (SQLite, stdlib-only).

The in-process registry in `ingest.py` resets every run, so it only catches reuse WITHIN a
single batch. This store persists perceptual dHashes across runs/restarts so a reused image is
caught against HISTORICAL claims too (THREAT_MODEL E3). Near-duplicate = Hamming distance
<= max_hamming on the 64-bit dHash, so re-saved / re-compressed / resized re-uploads still match.

Opt-in via cfg.fingerprint_db; default is the in-process registry. Thread-safe (a lock + a
short-lived connection per call, fine at this scale).
"""
from __future__ import annotations

import sqlite3
import threading

_LOCK = threading.Lock()
_STORES: dict[str, "FingerprintStore"] = {}


def _hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


class FingerprintStore:
    def __init__(self, db_path: str) -> None:
        self.db_path = str(db_path)
        with _LOCK, sqlite3.connect(self.db_path) as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS fingerprints (dhash INTEGER NOT NULL, case_id TEXT NOT NULL)")

    def register(self, dhash: int, case_id: str, max_hamming: int = 6) -> list[str]:
        """Record (dhash, case_id) and return ALL case_ids whose stored dHash is within
        max_hamming of it (near-duplicates), including the current one, de-duplicated."""
        with _LOCK, sqlite3.connect(self.db_path) as conn:
            rows = conn.execute("SELECT dhash, case_id FROM fingerprints").fetchall()
            matches = [cid for (h, cid) in rows if _hamming(int(h), dhash) <= max_hamming]
            conn.execute("INSERT INTO fingerprints (dhash, case_id) VALUES (?, ?)", (int(dhash), case_id))
        seen: set[str] = set()
        out: list[str] = []
        for cid in matches + [case_id]:
            if cid not in seen:
                seen.add(cid)
                out.append(cid)
        return out


def get_store(db_path: str) -> FingerprintStore:
    """Memoized store per db path (one connection-factory per file)."""
    with _LOCK:
        store = _STORES.get(db_path)
        if store is None:
            store = FingerprintStore(db_path)
            _STORES[db_path] = store
        return store
