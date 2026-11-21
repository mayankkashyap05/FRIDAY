"""FRIDAY UI — shared wire protocol between Python and the Electron renderer.

Two channels travel over the single loopback WebSocket:

**Text (JSON)** — compact, typed, delta-or-snapshot messages. Every message is
a flat object with a ``t`` (type) discriminator. The authoritative full state is
pushed as a ``snapshot`` immediately after (re)connect so a renderer that missed
deltas can never drift.

**Binary** — one leading stream byte followed by a JPEG payload::

    b"\\x01" + jpeg   # Inspector camera preview
    b"\\x02" + jpeg   # Vision Arcade

Frames are only produced while a surface that consumes them is open, and the
sender keeps a one-slot buffer per stream (latest-frame-wins) so a slow link can
never build a backlog.

This module is stdlib-only at import time; Pillow/numpy are imported lazily
inside :func:`encode_jpeg` so ``import ui`` stays cheap and dependency-free.
"""

from __future__ import annotations

import io
import json
import secrets
from typing import Any, Final

# ─────────────────────────────────────────────────────────────────────────────
# Handshake / auth
# ─────────────────────────────────────────────────────────────────────────────

TOKEN_ENV: Final = "FRIDAY_WS_TOKEN"
URL_ENV: Final = "FRIDAY_WS_URL"

#: WebSocket close code used for an unauthenticated client.
CLOSE_UNAUTHORIZED: Final = 4001
#: WebSocket close code used when Python is shutting down.
CLOSE_GOING_AWAY: Final = 1001

#: Sub-protocol name Electron asks for; the token may ride in it instead of the
#: query string (``Sec-WebSocket-Protocol: friday.v1.<token>``).
SUBPROTOCOL_PREFIX: Final = "friday.v1."


def new_token() -> str:
    """A per-launch 256-bit capability token (URL-safe, no padding)."""
    return secrets.token_urlsafe(32)


# ─────────────────────────────────────────────────────────────────────────────
# Stream ids (binary channel)
# ─────────────────────────────────────────────────────────────────────────────

STREAM_VISION: Final = 1
STREAM_ARCADE: Final = 2

STREAM_BY_NAME: Final = {"vision": STREAM_VISION, "arcade": STREAM_ARCADE}
NAME_BY_STREAM: Final = {STREAM_VISION: "vision", STREAM_ARCADE: "arcade"}

JPEG_MAX_SIDE: Final = 960
JPEG_QUALITY: Final = 78


# ─────────────────────────────────────────────────────────────────────────────
# Message types — Python → Electron
# ─────────────────────────────────────────────────────────────────────────────

#: Full authoritative state; sent on connect and after a renderer reload.
T_SNAPSHOT: Final = "snapshot"
T_STATE: Final = "state"
T_AMPLITUDE: Final = "amplitude"
T_LIVE_TRANSCRIPT: Final = "live_transcript"
T_TRANSCRIPT_APPEND: Final = "transcript_append"
T_TRANSCRIPT_CLEAR: Final = "transcript_clear"
T_CARDS: Final = "cards"
T_ACTIVITIES: Final = "activities"
T_PERSONA: Final = "persona"
T_VISION: Final = "vision"
T_ARCADE: Final = "arcade"
T_SETUP: Final = "setup"
T_INSPECTOR: Final = "inspector"
T_TOAST: Final = "toast"
T_WAKE: Final = "wake"
T_THEME: Final = "theme"
T_SHUTDOWN: Final = "shutdown"
T_PONG: Final = "pong"

# ─────────────────────────────────────────────────────────────────────────────
# Message types — Electron → Python
# ─────────────────────────────────────────────────────────────────────────────

C_SUBMIT_TEXT: Final = "submit_text"
C_SUBMIT_API_KEY: Final = "submit_api_key"
C_DISMISS_CARD: Final = "dismiss_card"
C_CLEAR_CARDS: Final = "clear_cards"
C_CLEAR_TRANSCRIPT: Final = "clear_transcript"
C_CANCEL_ACTIVITY: Final = "cancel_activity"
C_TOGGLE_MUTE: Final = "toggle_mute"
C_TOGGLE_SILENT: Final = "toggle_silent"
C_CANCEL: Final = "cancel"
C_BARGE_IN: Final = "barge_in"
C_OPEN_INSPECTOR: Final = "open_inspector"
C_CLOSE_INSPECTOR: Final = "close_inspector"
C_VISION_ACTION: Final = "vision_action"
C_ARCADE_GAME: Final = "arcade_game"
C_COMMAND: Final = "command"
C_WINDOW_DISMISSED: Final = "window_dismissed"
C_INTERACTION: Final = "interaction"
C_TYPING_CHANGED: Final = "typing_changed"
C_POINTER_INSIDE: Final = "pointer_inside"
C_SET_THEME: Final = "set_theme"
C_PING: Final = "ping"
C_REQUEST_QUIT: Final = "request_quit"

#: Every type the controller knows how to handle. Anything else is ignored
#: (and counted) rather than raising inside the socket loop.
CLIENT_TYPES: Final = frozenset({
    C_SUBMIT_TEXT, C_SUBMIT_API_KEY, C_DISMISS_CARD, C_CLEAR_CARDS,
    C_CLEAR_TRANSCRIPT, C_CANCEL_ACTIVITY, C_TOGGLE_MUTE, C_TOGGLE_SILENT,
    C_CANCEL, C_BARGE_IN, C_OPEN_INSPECTOR, C_CLOSE_INSPECTOR, C_VISION_ACTION,
    C_ARCADE_GAME, C_COMMAND, C_WINDOW_DISMISSED, C_INTERACTION,
    C_TYPING_CHANGED, C_POINTER_INSIDE, C_SET_THEME, C_PING, C_REQUEST_QUIT,
})


def dumps(message: dict[str, Any]) -> str:
    """Compact JSON (no spaces): the renderer parses ~60 messages/s."""
    return json.dumps(message, separators=(",", ":"), default=str)


def loads(raw: str | bytes) -> dict[str, Any]:
    """Parse one client message; never raises."""
    try:
        data = json.loads(raw)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


# ─────────────────────────────────────────────────────────────────────────────
# Binary frame codec
# ─────────────────────────────────────────────────────────────────────────────

def encode_jpeg(frame: Any, *, max_side: int = JPEG_MAX_SIDE,
                quality: int = JPEG_QUALITY) -> bytes | None:
    """Encode one camera/arcade frame as JPEG bytes. Never raises.

    Accepted inputs:

    * ``numpy.ndarray`` HxWx3 **BGR** (OpenCV) / HxWx4 BGRA / HxW grayscale
    * ``PIL.Image`` (any mode; converted to RGB)
    * ``bytes``/``bytearray``/``memoryview`` already holding encoded JPEG/PNG
      (passed through untouched — the renderer decodes it in a ``blob``)

    Returns ``None`` for anything unusable, which callers treat as "skip this
    frame" — the live preview must never raise into the vision thread.
    """
    if frame is None:
        return None

    # Already-encoded payload (fast path).
    if isinstance(frame, (bytes, bytearray, memoryview)):
        data = bytes(frame)
        return data or None

    try:
        from PIL import Image
    except Exception:  # pragma: no cover - Pillow is a hard requirement
        return None

    image: Any = None
    shape = getattr(frame, "shape", None)
    if shape is not None:
        try:
            import numpy as np

            arr = np.asarray(frame)
            if arr.ndim == 2:
                image = Image.fromarray(arr.astype("uint8", copy=False), mode="L")
            elif arr.ndim == 3 and arr.shape[2] == 3:
                # OpenCV hands us BGR; PIL wants RGB.
                image = Image.fromarray(arr[:, :, ::-1].astype("uint8", copy=False),
                                        mode="RGB")
            elif arr.ndim == 3 and arr.shape[2] == 4:
                image = Image.fromarray(arr[:, :, 2::-1].astype("uint8", copy=False),
                                        mode="RGB")
        except Exception:
            image = None

    if image is None and hasattr(frame, "convert") and hasattr(frame, "size"):
        try:  # PIL.Image
            image = frame if frame.mode == "RGB" else frame.convert("RGB")
        except Exception:
            image = None

    if image is None:
        return None

    try:
        width, height = image.size
        longest = max(width, height)
        if longest > max_side:
            ratio = max_side / float(longest)
            image = image.resize((max(1, int(width * ratio)),
                                  max(1, int(height * ratio))),
                                 Image.Resampling.BILINEAR)
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=int(quality), optimize=False)
        return buffer.getvalue() or None
    except Exception:
        return None


def frame_message(stream: int, jpeg: bytes) -> bytes:
    """One binary WebSocket payload: ``<stream byte><jpeg>``."""
    return bytes((int(stream) & 0xFF,)) + jpeg


def split_frame(payload: bytes) -> tuple[int, bytes]:
    """Inverse of :func:`frame_message` (used by tests and the renderer)."""
    if not payload:
        return 0, b""
    return payload[0], payload[1:]


__all__ = [
    "TOKEN_ENV", "URL_ENV", "CLOSE_UNAUTHORIZED", "CLOSE_GOING_AWAY",
    "SUBPROTOCOL_PREFIX", "new_token",
    "STREAM_VISION", "STREAM_ARCADE", "STREAM_BY_NAME", "NAME_BY_STREAM",
    "JPEG_MAX_SIDE", "JPEG_QUALITY", "encode_jpeg", "frame_message", "split_frame",
    "dumps", "loads", "CLIENT_TYPES",
    "T_SNAPSHOT", "T_STATE", "T_AMPLITUDE", "T_LIVE_TRANSCRIPT",
    "T_TRANSCRIPT_APPEND", "T_TRANSCRIPT_CLEAR", "T_CARDS", "T_ACTIVITIES",
    "T_PERSONA", "T_VISION", "T_ARCADE", "T_SETUP", "T_INSPECTOR", "T_TOAST",
    "T_WAKE", "T_THEME", "T_SHUTDOWN", "T_PONG",
    "C_SUBMIT_TEXT", "C_SUBMIT_API_KEY", "C_DISMISS_CARD", "C_CLEAR_CARDS",
    "C_CLEAR_TRANSCRIPT", "C_CANCEL_ACTIVITY", "C_TOGGLE_MUTE",
    "C_TOGGLE_SILENT", "C_CANCEL", "C_BARGE_IN", "C_OPEN_INSPECTOR",
    "C_CLOSE_INSPECTOR", "C_VISION_ACTION", "C_ARCADE_GAME", "C_COMMAND",
    "C_WINDOW_DISMISSED", "C_INTERACTION", "C_TYPING_CHANGED",
    "C_POINTER_INSIDE", "C_SET_THEME", "C_PING", "C_REQUEST_QUIT",
]
