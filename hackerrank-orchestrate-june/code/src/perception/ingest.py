"""Image ingestion: resolve paths under dataset/, decode (Pillow), resize for the
initial send, base64-encode, run the quality gate, and flag near-duplicates. Keeps
the original absolute path so `inspect_image` can crop the FULL-RES original later.

Failure-safe: an undecodable/missing image yields `ok=False` (never raises) so the
batch always produces a row (FAILURE_MODES D1/D2)."""
from __future__ import annotations

import base64
import hashlib
import io as _io
import logging
import threading

from PIL import Image

from src.config import Config
from src.io.reader import ClaimInput
from src.perception.authenticity_prior import manipulation_prior
from src.perception.quality_gate import assess_quality
from src.schema import QualityFlag

from pydantic import BaseModel, ConfigDict

logger = logging.getLogger(__name__)

# Smallest crop edge (px) we hand to the VLM. Tiny boxes get magnified so hard that
# global scale context is lost and cosmetic marks look severe; we expand them first.
MIN_CROP_PX = 224

# Cross-claim image fingerprint registry (fraud detection). Keyed on a PERCEPTUAL dHash
# (not exact bytes) so re-saved / re-compressed / resized re-uploads still match within a
# Hamming radius. The in-process list persists for the life of the process (catches reuse
# WITHIN a batch); set cfg.fingerprint_db for a durable SQLite store that also catches
# HISTORICAL reuse. Lock-guarded because perception runs claims concurrently.
_FINGERPRINTS: list[tuple[int, str]] = []
_FINGERPRINT_LOCK = threading.Lock()


def _dhash(img: Image.Image, size: int = 8) -> int:
    """Perceptual difference-hash -> 64-bit int. Near-duplicates have a small Hamming distance."""
    small = img.convert("L").resize((size + 1, size), Image.LANCZOS)
    px = small.tobytes()  # row-major, one byte per pixel (mode 'L')
    bits = 0
    for row in range(size):
        base = row * (size + 1)
        for col in range(size):
            bits = (bits << 1) | int(px[base + col] > px[base + col + 1])
    return bits


def _hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def register_image_hash(dhash: int, case_id: str, max_hamming: int = 6) -> list[str]:
    """Record this image's perceptual dHash for `case_id` and return ALL case_ids whose stored
    dHash is within max_hamming of it (near-duplicates), including the current one, de-duplicated.
    More than one entry => the image (or a near-copy) was reused across claims."""
    with _FINGERPRINT_LOCK:
        matches = [cid for (h, cid) in _FINGERPRINTS if _hamming(h, dhash) <= max_hamming]
        _FINGERPRINTS.append((dhash, case_id))
    seen: set[str] = set()
    out: list[str] = []
    for cid in matches + [case_id]:
        if cid not in seen:
            seen.add(cid)
            out.append(cid)
    return out


class LoadedImage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    image_id: str
    ok: bool
    abs_path: str
    media_type: str = "image/jpeg"
    b64: str = ""
    width: int = 0
    height: int = 0
    quality_flags: list[QualityFlag] = []
    duplicate_of: str | None = None
    # OTHER case_ids that submitted this image or a near-duplicate (empty = unique to this claim).
    reused_in_cases: list[str] = []
    # deterministic authenticity prior (EXIF/double-compression); only set when cfg.authenticity_prior.
    manipulation_prior: bool = False
    error: str | None = None


def _resize_long_edge(img: Image.Image, long_edge: int) -> Image.Image:
    w, h = img.size
    longest = max(w, h)
    if longest <= long_edge:
        return img
    scale = long_edge / longest
    return img.resize((max(1, round(w * scale)), max(1, round(h * scale))), Image.LANCZOS)


def _thumb_hash(img: Image.Image) -> str:
    """Content hash of a tiny grayscale thumbnail — catches near-duplicate images."""
    t = img.convert("L").resize((16, 16), Image.LANCZOS)
    return hashlib.sha1(t.tobytes()).hexdigest()


def _encode_jpeg(img: Image.Image, quality: int) -> str:
    buf = _io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=quality)
    return base64.standard_b64encode(buf.getvalue()).decode("ascii")


def load_images(claim: ClaimInput, cfg: Config) -> list[LoadedImage]:
    """Load every image referenced by the claim. Initial send is downsampled to
    `context_long_edge` when cfg.downsample_context_images (zoom via inspect_image),
    else `max_long_edge`."""
    initial_edge = cfg.context_long_edge if cfg.downsample_context_images else cfg.max_long_edge
    out: list[LoadedImage] = []
    seen_hashes: dict[str, str] = {}
    for rel, image_id in zip(claim.image_rel_paths(), claim.image_ids()):
        abs_path = cfg.image_abs_path(rel)
        try:
            with open(abs_path, "rb") as fh:
                raw_bytes = fh.read()
            with Image.open(_io.BytesIO(raw_bytes)) as im:
                im.load()
                im = im.convert("RGB")
                thash = _thumb_hash(im)
                dhash = _dhash(im)
                qflags = assess_quality(im, cfg.thresholds)
                resized = _resize_long_edge(im, initial_edge)
                b64 = _encode_jpeg(resized, cfg.jpeg_quality)
            dup = seen_hashes.get(thash)
            if dup is None:
                seen_hashes[thash] = image_id
            # Cross-claim reuse: register the PERCEPTUAL dHash and record any OTHER case_ids
            # that submitted the same or a near-duplicate image (durable store if configured).
            case_id = claim.case_id()
            if cfg.fingerprint_db is not None:
                from src.perception.fingerprint_store import get_store
                all_cases = get_store(str(cfg.fingerprint_db)).register(dhash, case_id, cfg.fingerprint_max_hamming)
            else:
                all_cases = register_image_hash(dhash, case_id, cfg.fingerprint_max_hamming)
            reused_in_cases = list(dict.fromkeys(c for c in all_cases if c != case_id))
            out.append(LoadedImage(
                image_id=image_id, ok=True, abs_path=str(abs_path), b64=b64,
                width=resized.width, height=resized.height, quality_flags=qflags,
                duplicate_of=dup, reused_in_cases=reused_in_cases,
                manipulation_prior=(manipulation_prior(raw_bytes) if cfg.authenticity_prior else False),
            ))
        except Exception as e:  # missing / corrupt / undecodable
            out.append(LoadedImage(
                image_id=image_id, ok=False, abs_path=str(abs_path),
                error=f"{type(e).__name__}: {e}",
            ))
    return out


def crop_region(abs_path: str, focus_area: str, cfg: Config, bbox: tuple[int, int, int, int] | None = None, image_id: str = "?") -> str | None:
    """Crop/zoom the ORIGINAL full-res image for inspect_image. `focus_area` is a
    coarse named region; `bbox` (pixel coords on the original) overrides it. Returns
    base64 JPEG, or None on failure. Deterministic."""
    try:
        with Image.open(abs_path) as im:
            im.load()
            im = im.convert("RGB")
            w, h = im.size
            box = bbox if bbox else _named_box(focus_area, w, h)
            box = _clamp_box(box, w, h)
            # Enforce a minimum crop size so sub-224px boxes aren't magnified into
            # misleadingly-severe close-ups. Expand outward from the box centroid,
            # then clamp back to the image boundaries.
            x0, y0, x1, y1 = box
            crop_w = x1 - x0
            crop_h = y1 - y0
            if crop_w < MIN_CROP_PX or crop_h < MIN_CROP_PX:
                cx = (x0 + x1) / 2.0
                cy = (y0 + y1) / 2.0
                half_w = max(crop_w, MIN_CROP_PX) / 2.0
                half_h = max(crop_h, MIN_CROP_PX) / 2.0
                box = _clamp_box((int(cx - half_w), int(cy - half_h), int(cx + half_w), int(cy + half_h)), w, h)
                logger.debug(f"Crop expanded from {crop_w}x{crop_h} to minimum {MIN_CROP_PX}px for {image_id}")
            crop = _resize_long_edge(im.crop(box), cfg.max_long_edge)
            return _encode_jpeg(crop, cfg.jpeg_quality)
    except Exception:
        return None


# Coarse named regions -> fractional boxes. Object-specific aliases map to a quadrant.
_NAMED_FRACTIONS: dict[str, tuple[float, float, float, float]] = {
    "center": (0.20, 0.20, 0.80, 0.80),
    "top": (0.0, 0.0, 1.0, 0.55), "bottom": (0.0, 0.45, 1.0, 1.0),
    "left": (0.0, 0.0, 0.55, 1.0), "right": (0.45, 0.0, 1.0, 1.0),
    "top_left": (0.0, 0.0, 0.6, 0.6), "top_right": (0.4, 0.0, 1.0, 0.6),
    "bottom_left": (0.0, 0.4, 0.6, 1.0), "bottom_right": (0.4, 0.4, 1.0, 1.0),
    "full": (0.0, 0.0, 1.0, 1.0),
}
_REGION_ALIASES: dict[str, str] = {
    "front_bumper": "bottom", "rear_bumper": "bottom", "hood": "top", "windshield": "top",
    "headlight": "top_left", "taillight": "bottom_right", "side_mirror": "left", "door": "center",
    "fender": "bottom_left", "quarter_panel": "bottom_right",
    "screen": "top", "keyboard": "bottom", "trackpad": "bottom", "hinge": "center", "lid": "top",
    "corner": "bottom_right", "port": "left", "base": "bottom",
    "seal": "top", "label": "center", "package_corner": "bottom_right", "package_side": "right",
    "contents": "center", "box": "full", "item": "center", "body": "full",
}


def _named_box(focus_area: str, w: int, h: int) -> tuple[int, int, int, int]:
    key = (focus_area or "center").strip().lower()
    if key not in _NAMED_FRACTIONS:
        key = _REGION_ALIASES.get(key, "center")
    fx0, fy0, fx1, fy1 = _NAMED_FRACTIONS[key]
    return (int(fx0 * w), int(fy0 * h), int(fx1 * w), int(fy1 * h))


def _clamp_box(box: tuple[int, int, int, int], w: int, h: int) -> tuple[int, int, int, int]:
    x0, y0, x1, y1 = box
    x0, y0 = max(0, min(x0, w - 1)), max(0, min(y0, h - 1))
    x1, y1 = max(x0 + 1, min(x1, w)), max(y0 + 1, min(y1, h))
    return (x0, y0, x1, y1)
