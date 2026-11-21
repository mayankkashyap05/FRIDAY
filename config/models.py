"""Model identifiers used with the Gemini API — the single source of truth.

Every ``genai.GenerativeModel(...)`` / ``client.models.generate_content(...)``
call site in FRIDAY imports its model name from here instead of hard-coding a
string. That matters because Google retires model ids on a schedule: the live
API failed with

    404 NOT_FOUND: models/gemini-2.5-flash-lite is no longer available to new
    users. Please update your code to use models/gemini-3.5-flash-lite

which had to be fixed in nine files at once because the name was copy-pasted
everywhere. Add a new id here and every caller follows.

This module must stay import-free (no ``google``, no project imports) so it can
be imported from ``actions/``, ``agent/``, ``memory/`` and tests alike without
triggering any optional dependency.
"""

from __future__ import annotations

# ── primary models ──────────────────────────────────────────────────────────
#: General-purpose flash model. Fast enough for interactive turns, strong
#: enough for planning and code.
MODEL_FLASH = "gemini-2.5-flash"

#: Cheap/fast tier. Used for classification, routing, summarisation, language
#: detection and as the overload fallback when the flash tier 429s.
#:
#: NOTE: the previous id, ``gemini-2.5-flash-lite``, now returns
#: ``404 NOT_FOUND ... no longer available to new users``.
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
