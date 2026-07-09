"""OpenAI price table + cost computation (USD).

Single source of truth for what each model costs, so spend can be computed at
call time AND recomputed later if prices change (see
``repository.recompute_openai_costs``). We store the raw token counts alongside
the computed cost, so a price change never requires re-calling OpenAI.

Prices verified 2026-06-20 (USD). gpt-4o / gpt-4o-mini / whisper-1 are legacy
models with long-stable prices. If OpenAI changes them, edit the tables below
and bump ``PRICE_VERSION`` — old rows can then be recomputed from stored tokens.
"""
from __future__ import annotations

# Bump whenever the tables below change, so recompute knows which rows are stale.
PRICE_VERSION = "2026-06-20"

# Chat models — USD per 1,000,000 tokens.
#   input  : normal prompt tokens
#   cached : discounted rate for cached prompt tokens (a SUBSET of input tokens)
#   output : completion tokens
CHAT_PRICES: dict[str, dict[str, float]] = {
    "gpt-4o":      {"input": 2.50, "cached": 1.25,  "output": 10.00},
    "gpt-4o-mini": {"input": 0.15, "cached": 0.075, "output": 0.60},
}

# Audio transcription — USD per minute of audio.
AUDIO_PRICES: dict[str, float] = {
    "whisper-1": 0.006,
}


def _chat_table(model: str) -> dict[str, float] | None:
    if model in CHAT_PRICES:
        return CHAT_PRICES[model]
    # Tolerate dated aliases like "gpt-4o-2024-08-06".
    for key, tbl in CHAT_PRICES.items():
        if model.startswith(key):
            return tbl
    return None


def chat_cost_usd(
    model: str, prompt_tokens: int, cached_tokens: int, completion_tokens: int
) -> tuple[float, str]:
    """Return ``(cost_usd, price_version)`` for one chat completion.

    ``cached_tokens`` is a SUBSET of ``prompt_tokens``: the cached part is billed
    at the cheaper cached rate and the remainder at the input rate — never
    double-counted. An unknown model yields cost 0 with a visible
    ``"unknown:<model>"`` version tag (so it can be fixed later, no data lost).
    """
    tbl = _chat_table(model)
    if tbl is None:
        return 0.0, f"unknown:{model}"
    cached = max(0, min(int(cached_tokens or 0), int(prompt_tokens or 0)))
    fresh = max(0, int(prompt_tokens or 0) - cached)
    cost = (
        fresh * tbl["input"]
        + cached * tbl.get("cached", tbl["input"])
        + int(completion_tokens or 0) * tbl["output"]
    ) / 1_000_000.0
    return cost, PRICE_VERSION


def audio_cost_usd(model: str, audio_seconds: int | None) -> tuple[float, str]:
    """Return ``(cost_usd, price_version)`` for a per-minute audio model."""
    rate = AUDIO_PRICES.get(model)
    if rate is None:
        return 0.0, f"unknown:{model}"
    if not audio_seconds:
        return 0.0, PRICE_VERSION
    minutes = int(audio_seconds) / 60.0
    return rate * minutes, PRICE_VERSION
