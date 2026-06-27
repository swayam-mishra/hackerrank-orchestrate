"""Central configuration — the ONLY home for tunables (model id, paths, caps,
thresholds, prompt version). No magic numbers anywhere else in the codebase.

`Config` is constructed once in an entry point (main/cli/eval) and passed
explicitly down the call chain — never imported as a global at use sites.
Pure functions receive the small frozen `Thresholds` they need, not the whole
Config, so they stay isolable (see ENGINEERING_CONVENTIONS §5, §7).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# Repo root = .../hackerrank-orchestrate-june26 ; this file is code/src/config.py
REPO_ROOT = Path(__file__).resolve().parents[2]

DEFAULT_MODEL = "claude-opus-4-8"          # rejects temperature/top_p/top_k/budget_tokens; adaptive thinking only
AB_ALT_MODEL = "claude-sonnet-4-6"          # one-line A/B alternative ($3/$15, accepts temperature=0)

# Per-MTok USD prices (verified June 2026). cache_write = 1.25x input, cache_read = 0.1x input.
PRICES: dict[str, dict[str, float]] = {
    "claude-opus-4-8": {"input": 5.0, "output": 25.0, "cache_write": 6.25, "cache_read": 0.50},
    "claude-sonnet-4-6": {"input": 3.0, "output": 15.0, "cache_write": 3.75, "cache_read": 0.30},
}


def prices_for(model: str) -> dict[str, float]:
    return PRICES.get(model, PRICES[DEFAULT_MODEL])


@dataclass(frozen=True)
class Thresholds:
    """Tunables consumed by the PURE deterministic layers. Passed in explicitly."""
    # Quality gate (perception) — cheap priors that corroborate the VLM, never override it.
    blur_var_min: float = 90.0             # variance-of-Laplacian below this => blurry_image prior
    low_light_mean_max: float = 30.0       # mean luminance (0-255) below this => low_light_or_glare prior
    high_glare_mean_min: float = 245.0     # near-white mean => glare prior
    # Low-confidence abstention: a supported/contradicted verdict whose VLM confidence is
    # below this AND has no grounding cue is routed to NEI + manual_review_required (additive,
    # can only abstain). Conservative: the labeled sample's min confidence is 0.5, so this
    # never abstains a currently-correct sample row.
    vlm_confidence_min: float = 0.35
    # User-history risk (risk/history.py) — interpretable, bounded; derived from field ranges,
    # NOT fit to labels. They only ADD risk flags; they never change claim_status.
    history_rejection_rate_min: float = 0.40   # rejected_claim / max(past_claim_count,1)
    history_recent_burst_min: int = 4          # last_90_days_claim_count >= this
    history_review_rate_min: float = 0.40      # manual_review_claim / max(past_claim_count,1)


@dataclass(frozen=True)
class Config:
    """Everything an entry point needs. Frozen + passed explicitly."""
    model: str = DEFAULT_MODEL
    fallback_model: str | None = None
    prompt_version: str = "v4"

    # Image handling (verified current values for Opus 4.8 — NOT the stale 1568).
    max_long_edge: int = 2576              # claimed-region / primary images
    context_long_edge: int = 1568          # non-primary context images when downsampling
    downsample_context_images: bool = True
    jpeg_quality: int = 90

    # Agent loop
    max_tool_rounds: int = 6
    max_output_tokens: int = 4096
    use_strict_tool: bool = True
    max_repair_retries: int = 2
    api_max_retries: int = 4
    request_timeout_s: float = 120.0
    concurrency: int = 4

    # Self-consistency: re-sample perception N times on BORDERLINE rows only (a contradiction
    # signal present, or confidence <= conf_max) and majority-vote the decision-driving fields;
    # disagreement across reads -> manual_review_required. 1 disables (no extra cost). Cost rises
    # ~N x on borderline rows only, so the steady-state (clear) rows stay single-call.
    self_consistency_samples: int = 3
    self_consistency_conf_max: float = 0.60

    # Re-inspect: after the first submit_decision, if confidence is low (<= conf_max) and no
    # supporting image carried a cue, force ONE more inspect+resubmit round before finalizing.
    reinspect_low_confidence: bool = True
    reinspect_conf_max: float = 0.45

    # Cross-claim image fraud: perceptual dHash near-duplicate threshold (Hamming, 0..64).
    fingerprint_max_hamming: int = 6
    # Durable fingerprint store (SQLite) — persists dHashes across runs/restarts so reuse is
    # caught against HISTORICAL claims, not just within one process. None -> in-process registry.
    fingerprint_db: Path | None = None
    # Deterministic authenticity prior (EXIF/double-compression). Default OFF: it is noisy on
    # synthetic/screenshot-heavy data (would over-flag). Opt-in for production photo streams.
    authenticity_prior: bool = False

    thresholds: Thresholds = field(default_factory=Thresholds)

    # Paths (all derived from REPO_ROOT; overridable).
    repo_root: Path = REPO_ROOT
    dataset_dir: Path = REPO_ROOT / "dataset"
    sample_csv: Path = REPO_ROOT / "dataset" / "sample_claims.csv"
    test_csv: Path = REPO_ROOT / "dataset" / "claims.csv"
    history_csv: Path = REPO_ROOT / "dataset" / "user_history.csv"
    evidence_csv: Path = REPO_ROOT / "dataset" / "evidence_requirements.csv"
    output_csv: Path = REPO_ROOT / "output.csv"
    artifacts_dir: Path = REPO_ROOT / "code" / "artifacts"

    @property
    def audit_dir(self) -> Path:
        return self.artifacts_dir / "audit"

    @property
    def cache_dir(self) -> Path:
        """Base dir for cached PerceptionFacts. Per-split subdirs avoid sample/test
        case-id collisions (see facts_dir)."""
        return self.artifacts_dir / "facts_cache"

    def facts_dir(self, split: str) -> Path:
        """Split-namespaced PerceptionFacts cache (sample/test share case ids)."""
        return self.cache_dir / split

    def image_abs_path(self, image_rel_path: str) -> Path:
        """Resolve a CSV image path ('images/test/case_001/img_1.jpg') under dataset/."""
        return (self.dataset_dir / image_rel_path).resolve()


def load_config(model: str | None = None, fallback_model: str | None = None) -> Config:
    """Build Config from env + optional overrides. Env: ANTHROPIC_MODEL, ANTHROPIC_FALLBACK_MODEL."""
    chosen = model or os.environ.get("ANTHROPIC_MODEL") or DEFAULT_MODEL
    fb = fallback_model or os.environ.get("ANTHROPIC_FALLBACK_MODEL") or None
    return Config(model=chosen, fallback_model=fb)


def ensure_dirs(cfg: Config) -> None:
    """Create artifact directories (entry-point side effect, not in pure layers)."""
    cfg.audit_dir.mkdir(parents=True, exist_ok=True)
    cfg.cache_dir.mkdir(parents=True, exist_ok=True)
