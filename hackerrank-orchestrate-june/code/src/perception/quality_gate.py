"""Deterministic per-image quality signals (cheap priors that corroborate the VLM,
never override it). Pillow-only (no numpy). Takes a PIL image + Thresholds -> flags.
Side-effect-free given an in-memory image."""
from __future__ import annotations

from PIL import Image, ImageFilter, ImageStat

from src.config import Thresholds
from src.schema import QualityFlag

_LAPLACIAN = ImageFilter.Kernel((3, 3), (0, 1, 0, 1, -4, 1, 0, 1, 0), scale=1, offset=128)


def variance_of_laplacian(gray: Image.Image) -> float:
    """Focus measure: variance of a Laplacian-filtered grayscale image. Low => blurry."""
    return float(ImageStat.Stat(gray.filter(_LAPLACIAN)).var[0])


def mean_luminance(gray: Image.Image) -> float:
    return float(ImageStat.Stat(gray).mean[0])


def assess_quality(img: Image.Image, th: Thresholds) -> list[QualityFlag]:
    """Return quality flags observed by deterministic metrics. Mild blur is a flag,
    not an unusability — a blurry image can still be part of a supported claim
    (sample case_007). Only decode failure (handled in ingest) marks an image unusable."""
    gray = img.convert("L")
    flags: list[QualityFlag] = []
    if variance_of_laplacian(gray) < th.blur_var_min:
        flags.append("blurry_image")
    mean = mean_luminance(gray)
    if mean < th.low_light_mean_max or mean > th.high_glare_mean_min:
        flags.append("low_light_or_glare")
    return flags
