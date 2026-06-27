"""Perception layer: quality-gate flags on synthetic images, and ingest resize /
dedupe / corrupt-and-missing handling."""
from PIL import Image

from src.config import Config, Thresholds
from src.io.reader import ClaimInput
from src.perception.ingest import (
    _FINGERPRINT_LOCK,
    _FINGERPRINTS,
    crop_region,
    load_images,
    register_image_hash,
)
from src.perception.quality_gate import assess_quality, variance_of_laplacian

TH = Thresholds()


def test_quality_sharp_vs_dark():
    noise = Image.effect_noise((400, 300), 80).convert("RGB")   # high-frequency -> sharp
    assert "blurry_image" not in assess_quality(noise, TH)
    assert variance_of_laplacian(noise.convert("L")) > TH.blur_var_min

    dark = Image.new("RGB", (400, 300), (8, 8, 8))               # very dark + flat
    flags = assess_quality(dark, TH)
    assert "low_light_or_glare" in flags


def test_glare_bright_image():
    bright = Image.new("RGB", (200, 200), (250, 250, 250))
    assert "low_light_or_glare" in assess_quality(bright, TH)


def _cfg(tmp_path) -> Config:
    return Config(dataset_dir=tmp_path, max_long_edge=256, context_long_edge=128, downsample_context_images=True)


def test_ingest_resize_dedupe_and_missing(tmp_path):
    img = Image.effect_noise((1000, 600), 90).convert("RGB")
    img.save(tmp_path / "a.jpg")
    img.save(tmp_path / "b.jpg")  # identical content -> duplicate
    claim = ClaimInput(user_id="u", image_paths="a.jpg;b.jpg;missing.jpg", user_claim="x", claim_object="car")

    loaded = load_images(claim, _cfg(tmp_path))
    by_id = {i.image_id: i for i in loaded}
    assert by_id["a"].ok and by_id["b"].ok and not by_id["missing"].ok
    assert max(by_id["a"].width, by_id["a"].height) <= 128   # resized to context_long_edge
    assert by_id["b"].duplicate_of == "a"                    # near-dup detected
    assert by_id["a"].b64 and by_id["missing"].b64 == ""
    assert by_id["missing"].error


def test_crop_region_named_and_bbox(tmp_path):
    Image.effect_noise((800, 800), 90).convert("RGB").save(tmp_path / "x.jpg")
    cfg = _cfg(tmp_path)
    p = str(tmp_path / "x.jpg")
    assert crop_region(p, "windshield", cfg) is not None        # named region alias
    assert crop_region(p, "center", cfg, bbox=(10, 10, 200, 200)) is not None
    assert crop_region(str(tmp_path / "nope.jpg"), "center", cfg) is None  # missing -> None


def test_crop_region_enforces_min_crop_size(tmp_path):
    # A tiny bbox on a large image must be expanded to at least MIN_CROP_PX (224)
    # so the VLM never sees a wildly-magnified, scale-distorted close-up.
    import base64
    import io

    Image.effect_noise((1000, 1000), 90).convert("RGB").save(tmp_path / "big.jpg")
    cfg = _cfg(tmp_path)  # max_long_edge=256 > 224, so the crop is not downsized
    p = str(tmp_path / "big.jpg")

    b64 = crop_region(p, "center", cfg, bbox=(495, 495, 505, 505))  # 10x10, centered
    assert b64 is not None
    out = Image.open(io.BytesIO(base64.standard_b64decode(b64)))
    assert out.width >= 224 and out.height >= 224


def test_register_image_hash_cross_claim_reuse():
    # perceptual dHash + Hamming-radius matching (not exact bytes).
    with _FINGERPRINT_LOCK:
        _FINGERPRINTS.clear()
    h = 0xA1B2C3D4E5F60718
    assert register_image_hash(h, "case_001") == ["case_001"]              # first use
    assert register_image_hash(h, "case_002") == ["case_001", "case_002"]  # exact reuse
    near = h ^ 0b1011                                                       # 3 bits flipped (<= 6)
    assert "case_001" in register_image_hash(near, "case_003")             # near-duplicate matches
    far = h ^ ((1 << 64) - 1)                                              # all 64 bits flipped
    assert register_image_hash(far, "case_004") == ["case_004"]            # far hash does NOT match


def test_fingerprint_registry_is_thread_safe():
    import threading
    assert isinstance(_FINGERPRINTS, list)
    assert isinstance(_FINGERPRINT_LOCK, type(threading.Lock()))


def test_load_images_flags_cross_claim_reuse(tmp_path):
    # The same image bytes under two different case_ids should be flagged as reused.
    with _FINGERPRINT_LOCK:
        _FINGERPRINTS.clear()
    img = Image.effect_noise((300, 200), 90).convert("RGB")
    img.save(tmp_path / "case_777_img_1.jpg")
    img.save(tmp_path / "case_888_img_1.jpg")  # identical bytes, different claim

    claim_a = ClaimInput(user_id="u1", image_paths="case_777_img_1.jpg", user_claim="x", claim_object="car")
    claim_b = ClaimInput(user_id="u2", image_paths="case_888_img_1.jpg", user_claim="x", claim_object="car")

    a = load_images(claim_a, _cfg(tmp_path))[0]
    b = load_images(claim_b, _cfg(tmp_path))[0]

    assert a.reused_in_cases == []                 # first submission: unique so far
    assert b.reused_in_cases == [claim_a.case_id()]  # second: points back to claim A


def test_dhash_robust_to_resize():
    # perceptual hash survives a resize (where exact-bytes md5 would not) -> near-duplicate.
    from src.perception.ingest import _dhash, _hamming
    base = Image.effect_noise((400, 300), 70).convert("RGB")
    assert _hamming(_dhash(base), _dhash(base.resize((200, 150)))) <= 6


def test_fingerprint_store_durable_near_dup(tmp_path):
    from src.perception.fingerprint_store import FingerprintStore
    db = str(tmp_path / "fp.db")
    s = FingerprintStore(db)
    h = 0x0F0F0F0F0F0F0F0F
    assert s.register(h, "case_a") == ["case_a"]
    assert s.register(h ^ 0b11, "case_b") == ["case_a", "case_b"]  # near-dup matches
    # a fresh store on the SAME db still sees prior fingerprints (cross-run persistence)
    assert "case_a" in FingerprintStore(db).register(h, "case_c")


def test_manipulation_prior_flags_no_exif_jpeg():
    import io
    from src.perception.authenticity_prior import manipulation_prior
    buf = io.BytesIO()
    Image.new("RGB", (64, 64), (120, 120, 120)).save(buf, format="JPEG")  # JPEG, no EXIF
    assert manipulation_prior(buf.getvalue()) is True
    assert manipulation_prior(b"not an image") is False  # never raises
