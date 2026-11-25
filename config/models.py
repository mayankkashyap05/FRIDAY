"""Model identifiers used with the Gemini API — the single source of truth.

Every Gemini call site in FRIDAY imports its model name from here instead of
hard-coding a string. Centralizing identifiers makes model migrations reliable:
update an id here and all callers follow.

This module must stay import-free (no ``google``, no project imports) so it can
be imported from ``actions/``, ``agent/``, ``memory/`` and tests alike without
triggering any optional dependency.
"""

from __future__ import annotations

# ── primary models ──────────────────────────────────────────────────────────
#: General-purpose flash model. Fast enough for interactive turns, strong
#: enough for planning and code.
MODEL_FLASH = "gemini-3.8-flash"

#: Cheap/fast tier. Used for classification, routing, summarisation, language
#: detection and as the overload fallback when the flash tier 429s.
#:
MODEL_FLASH_LITE = "gemini-3.5-flash-lite"

#: Live/realtime audio conversation model (``FridayLive`` in ``agent/live.py``).
MODEL_LIVE = "gemini-3.1-flash-live-preview"

# ── back-compat aliases ─────────────────────────────────────────────────────
#: Short names used by modules that historically declared a local ``MODEL``.
MODEL = MODEL_FLASH
LITE_MODEL = MODEL_FLASH_LITE

__all__ = [
    "MODEL",
    "LITE_MODEL",
    "MODEL_FLASH",
    "MODEL_FLASH_LITE",
    "MODEL_LIVE",
]
