"""PURE — a cheap deterministic authenticity prior that CORROBORATES `possible_manipulation`
(THREAT_MODEL E1). It never auto-rejects and is OFF by default (cfg.authenticity_prior): on
synthetic / screenshot-heavy data an EXIF-absence heuristic over-flags, so it is opt-in for
real photo streams. Pillow-only; no ELA/DCT middleware.

Signals (any -> weak manipulation prior):
  * a JPEG with NO EXIF/metadata at all (camera originals normally carry EXIF; stripped
    metadata is common after editing/round-tripping — a WEAK signal, hence corroborating only);
  * an EXIF Software tag naming a known image editor.
"""
from __future__ import annotations

import io as _io

from PIL import Image

_EDITOR_MARKERS = ("photoshop", "gimp", "lightroom", "pixlr", "affinity", "snapseed", "facetune")


def manipulation_prior(raw_bytes: bytes) -> bool:
    """True if the image's metadata weakly suggests editing/re-encoding. Best-effort; any
    decode/parse failure returns False (never raises, never blocks the pipeline)."""
    try:
        with Image.open(_io.BytesIO(raw_bytes)) as im:
            fmt = (im.format or "").upper()
            exif = im.getexif()
            if fmt in ("JPEG", "JPG") and len(exif) == 0:
                return True  # a JPEG with zero EXIF tags — no camera provenance
            software = str(exif.get(0x0131, "")).lower()  # 0x0131 = Software tag
            return any(m in software for m in _EDITOR_MARKERS)
    except Exception:
        return False
