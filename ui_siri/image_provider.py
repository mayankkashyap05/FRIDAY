"""FRIDAY ambient UI — QQuickImageProvider for zero-lag video frames.

Why not just emit a signal per frame? Because 30 fps × 4 producers of queued
cross-thread signals starves the GUI event loop. Instead:

* Producers **write** the latest frame into a per-stream slot under a lock and
  bump a revision counter. No Qt object is touched.
* QML requests ``image://friday/vision?rev=N``. Qt calls
  :meth:`ImageProvider.requestImage` **on a worker thread**, so this is safe.
* The revision is a query parameter purely as a cache-buster; the provider
  always serves the newest frame for that stream.

Streams are created on demand, so ``actions/vision_gesture.py`` needs zero
changes: whatever it passes to ``set_vision_frame`` / ``set_arcade_frame``
ends up here.

Frame formats accepted
----------------------
* ``numpy.ndarray`` HxWx3 **BGR** (what OpenCV gives us)
* ``numpy.ndarray`` HxWx3 RGB / HxW grayscale
* ``PIL.Image``
* raw ``bytes`` of an encoded PNG/JPEG

Nothing here imports cv2: BGR→RGB is a slice, which is free.
"""

from __future__ import annotations

import threading
import time
from typing import Any

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QImage, QImageReader, QPixmap

try:
    from PySide6.QtQuick import QQuickImageProvider
except Exception:  # pragma: no cover - QtWidgets-only fallback
    from PySide6.QtGui import QImageReader as _R  # noqa: F401

    class QQuickImageProvider:  # type: ignore[no-redef]
        def __init__(self, *a, **k):  # pragma: no cover
            raise RuntimeError("PySide6.QtQuick is unavailable")


class _Stream:
    """One logical video stream (vision preview, arcade, screen, …)."""

    __slots__ = ("lock", "image", "revision", "frames", "dropped", "last_ts",
                 "width", "height", "source")

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.image: QImage | None = None
        self.revision = 0
        self.frames = 0
        self.dropped = 0
        self.last_ts = 0.0
        self.width = 0
        self.height = 0
        self.source = ""

    def status(self) -> dict:
        return {
            "revision": self.revision,
            "frames": self.frames,
            "dropped": self.dropped,
            "width": self.width,
            "height": self.height,
            "age_ms": round((time.time() - self.last_ts) * 1000) if self.last_ts else -1,
            "source": self.source,
        }


# ─────────────────────────────────────────────────────────────────────────────
# Conversion helpers
# ─────────────────────────────────────────────────────────────────────────────

def to_qimage(frame: Any) -> QImage | None:
    """Best-effort conversion from any supported frame to a ``QImage``.

    Returns ``None`` on anything unexpected — callers treat that as "skip this
    frame", which is what you want for a live preview.
    """
    if frame is None:
        return None
    if isinstance(frame, QImage):
        return frame

    # numpy / array-likes with .shape and .dtype
    shape = getattr(frame, "shape", None)
    if shape is not None:
        try:
            import numpy as np

            arr = np.asarray(frame)
            if arr.ndim == 2:
                arr = np.ascontiguousarray(arr)
                h, w = arr.shape
                fmt = (QImage.Format.Format_Grayscale8 if arr.dtype == np.uint8
                       else QImage.Format.Format_Grayscale16)
                img = QImage(arr.data, w, h, arr.strides[0], fmt)
                return img.copy()
            if arr.ndim == 3 and arr.shape[2] >= 3:
                arr = np.ascontiguousarray(arr)
                h, w, _ = arr.shape
                img = QImage(arr.data, w, h, arr.strides[0],
                             QImage.Format.Format_RGB888)
                return img.copy()
            if arr.ndim == 3 and arr.shape[2] == 4:
                arr = np.ascontiguousarray(arr)
                h, w, _ = arr.shape
                img = QImage(arr.data, w, h, arr.strides[0],
                             QImage.Format.Format_RGBA8888)
                return img.copy()
        except Exception:
            pass

    # PIL
    tobytes = getattr(frame, "tobytes", None)
    mode = getattr(frame, "mode", None)
    if tobytes is not None and mode is not None:
        try:
            rgb = frame.convert("RGB") if mode != "RGB" else frame
            w, h = rgb.size
            data = rgb.tobytes("raw", "RGB")
            img = QImage(data, w, h, 3 * w, QImage.Format.Format_RGB888)
            return img.copy()
        except Exception:
            pass

    # encoded bytes
    if isinstance(frame, (bytes, bytearray, memoryview)):
        try:
            reader = QImageReader()
            from PySide6.QtCore import QBuffer, QByteArray

            buf = QBuffer()
            buf.setData(QByteArray(bytes(frame)))
            buf.open(QBuffer.OpenModeFlag.ReadOnly)
            reader.setDevice(buf)
            img = reader.read()
            return None if img.isNull() else img
        except Exception:
            return None
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Provider
# ─────────────────────────────────────────────────────────────────────────────

class FridayImageProvider(QQuickImageProvider):
    """``image://friday/<stream>?rev=<n>``"""

    PROVIDER_ID = "friday"

    def __init__(self) -> None:
        super().__init__(QQuickImageProvider.ImageType.Image)
        self._streams: dict[str, _Stream] = {}
        self._lock = threading.Lock()
        self._max_side = 1280

    # ── producer API (any thread, no Qt objects touched) ─────────────────────
    def stream(self, name: str) -> _Stream:
        name = (name or "vision").strip().lower()
        with self._lock:
            st = self._streams.get(name)
            if st is None:
                st = _Stream()
                self._streams[name] = st
            return st

    def push(self, name: str, frame: Any) -> bool:
        """Store the newest frame for ``name``. Returns False if unusable."""
        img = to_qimage(frame)
        if img is None or img.isNull():
            st = self.stream(name)
            st.dropped += 1
            return False
        if max(img.width(), img.height()) > self._max_side:
            img = img.scaled(self._max_side, self._max_side,
                             Qt.AspectRatioMode.KeepAspectRatio,
                             Qt.TransformationMode.SmoothTransformation)
        st = self.stream(name)
        with st.lock:
            st.image = img
            st.revision += 1
            st.frames += 1
            st.last_ts = time.time()
            st.width = img.width()
            st.height = img.height()
        return True

    def clear(self, name: str) -> None:
        st = self.stream(name)
        with st.lock:
            st.image = None
            st.revision += 1

    # ── consumer API (called by Qt, possibly off the GUI thread) ─────────────
    def requestImage(self, image_id: str, size: QSize, requested_size: QSize):  # noqa: N802
        name = (image_id or "").split("?")[0].split("/")[0].strip().lower() or "vision"
        st = self.stream(name)
        with st.lock:
            img = st.image
            if img is not None and not img.isNull():
                out = img
                if size is not None and img.width() > 4:
                    try:
                        size.setWidth(img.width())
                        size.setHeight(img.height())
                    except Exception:
                        pass
                return (QPixmap.fromImage(out), QSize(img.width(), img.height()))
        # Never return a null QPixmap: QML would log a decode error every frame.
        placeholder = QImage(2, 2, QImage.Format.Format_ARGB32)
        placeholder.fill(0)
        if size is not None:
            try:
                size.setWidth(2)
                size.setHeight(2)
            except Exception:
                pass
        return (QPixmap.fromImage(placeholder), QSize(2, 2))

    # ── introspection ────────────────────────────────────────────────────────
    def url(self, name: str) -> str:
        """Cache-busting URL for the current revision."""
        st = self.stream(name)
        with st.lock:
            rev = st.revision
        return f"image://{self.PROVIDER_ID}/{name}?rev={rev}"

    def status(self) -> dict:
        with self._lock:
            names = list(self._streams)
        return {name: self._streams[name].status() for name in names}


__all__ = ["FridayImageProvider", "to_qimage"]
