"""Thin Anthropic client factory. Lazy import so the rest of the codebase imports
without the `anthropic` package. The key comes from ANTHROPIC_API_KEY (env / .env)."""
from __future__ import annotations

import os
from typing import Any

from src.config import Config


def has_api_key() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def build_client(cfg: Config) -> Any:
    import anthropic  # lazy

    return anthropic.Anthropic(max_retries=cfg.api_max_retries, timeout=cfg.request_timeout_s)
