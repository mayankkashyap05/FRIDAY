"""Native macOS dictation paste adapter (Cmd+V).

Implements a protocol-compatible adapter mirroring
``ui.island.dictation_paste.QtWindowsAdapter`` for macOS using PyObjC.
"""
from __future__ import annotations

import sys
from typing import Any

# Reuse Target dataclass and helpers from the canonical module.
try:
    from ui.island.dictation_paste import Target, style_text  # type: ignore
except Exception:  # pragma: no cover - fallback for isolated tests
    from dataclasses import dataclass

    @dataclass(frozen=True)
    class Target:  # type: ignore[no-redef]
        handle: int
        title: str

    def style_text(text, style):  # type: ignore[no-redef]
        text = str(text)
        if style == "Exact":
            return text
        if style not in ("Clean", "Polished"):
            raise ValueError("Unknown dictation style")
        import re

        text = re.sub(r"[ \t]+", " ", text).strip()
        text = re.sub(r" +([,.;:!?])", r"\1", text)
        if style == "Polished" and text:
            text = text[0].upper() + text[1:]
            if text[-1] not in ".!?":
                text += "."
        return text


def _load_pyobjc():
    if sys.platform != "darwin":
        return None, None, None, None
    try:
        import objc  # type: ignore
        import AppKit  # type: ignore
        import Quartz  # type: ignore
        import ApplicationServices  # type: ignore

        return objc, AppKit, Quartz, ApplicationServices
    except Exception:
        return None, None, None, None


def is_accessibility_trusted() -> bool:
    """Return True if process is trusted for Accessibility (AXIsProcessTrusted)."""
    _objc, _AppKit, _Quartz, ApplicationServices = _load_pyobjc()
    if ApplicationServices is None:
        return False
    try:
        return bool(ApplicationServices.AXIsProcessTrusted())
    except Exception:
        return False


class CocoaPasteAdapter:
    """Qt clipboard + macOS AppKit/Quartz paste adapter.

    Protocol-compatible with ``ui.island.dictation_paste.QtWindowsAdapter``.
    All methods are intended to be called on the Qt thread.
    """

    def __init__(self):
        objc, AppKit, Quartz, ApplicationServices = _load_pyobjc()
        if AppKit is None or Quartz is None or ApplicationServices is None:
            raise RuntimeError("External paste is only available on macOS with PyObjC")
        self._objc = objc
        self._AppKit = AppKit
        self._Quartz = Quartz
        self._ApplicationServices = ApplicationServices

        try:
            from PySide6.QtWidgets import QApplication

            self.clipboard = QApplication.clipboard()
        except Exception as exc:
            raise RuntimeError(f"Qt clipboard unavailable: {exc}") from exc

    # --- Focus / target tracking ---
    def foreground(self) -> Target:
        try:
            workspace = self._AppKit.NSWorkspace.sharedWorkspace()
            front = workspace.frontmostApplication()
            if front is None:
                return Target(0, "")
            pid = int(front.processIdentifier())
            name = str(front.localizedName() or "")
            return Target(pid, name)
        except Exception:
            return Target(0, "")

    # --- Pasteboard snapshot / restore ---
    def snapshot(self) -> dict:
        """Snapshot all NSPasteboardItem types and raw NSData payloads."""
        try:
            pb = self._AppKit.NSPasteboard.generalPasteboard()
            items = pb.pasteboardItems() or []
        except Exception:
            items = []

        snapshot: dict[str, Any] = {"__items__": []}
        for item in items:
            try:
                types = item.types() or []
            except Exception:
                types = []
            item_data = {}
            for t in types:
                try:
                    data = item.dataForType_(t)
                    if data is None:
                        continue
                    # Store as raw bytes.
                    try:
                        item_data[str(t)] = bytes(data)
                    except Exception:
                        # NSData -> bytes fallback
                        item_data[str(t)] = data.bytes().tobytes() if hasattr(data, "bytes") else bytes()
                except Exception:
                    continue
            snapshot["__items__"].append(item_data)

        # Also include Qt mime snapshot for compatibility with DictationPaste
        # which expects a dict-like snapshot; keep Qt formats too.
        try:
            mime = self.clipboard.mimeData()
            if mime is not None:
                for fmt in mime.formats():
                    try:
                        snapshot[f"qt:{fmt}"] = bytes(mime.data(fmt))
                    except Exception:
                        pass
        except Exception:
            pass
        return snapshot

    def set_text(self, text: str) -> None:
        """Write dictated text and mark as transient for clipboard managers."""
        pb = self._AppKit.NSPasteboard.generalPasteboard()
        try:
            pb.clearContents()
        except Exception:
            pass
        # Set plain text
        try:
            NSStringPboardType = getattr(self._AppKit, "NSPasteboardTypeString", "public.utf8-plain-text")
            pb.setString_forType_(str(text), NSStringPboardType)
        except Exception:
            # Fallback to Qt clipboard
            self.clipboard.setText(str(text))

        # Mark transient so clipboard managers ignore it.
        try:
            # Transient type: org.nspasteboard.TransientType, data None
            pb.setData_forType_(None, "org.nspasteboard.TransientType")
        except Exception:
            try:
                # Some bridges prefer NSData; but prompt specifies None.
                pb.setString_forType_("", "org.nspasteboard.TransientType")
            except Exception:
                pass

    def restore(self, snapshot: dict) -> None:
        """Restore full NSPasteboard snapshot."""
        if not isinstance(snapshot, dict):
            return
        pb = self._AppKit.NSPasteboard.generalPasteboard()
        items_data = snapshot.get("__items__")
        try:
            pb.clearContents()
        except Exception:
            pass

        restored_any = False
        if isinstance(items_data, list) and items_data:
            try:
                NSPasteboardItem = self._AppKit.NSPasteboardItem
                new_items = []
                for item_data in items_data:
                    if not isinstance(item_data, dict):
                        continue
                    item = NSPasteboardItem.alloc().init()
                    ok_item = False
                    for t, payload in item_data.items():
                        try:
                            if payload is None:
                                continue
                            ns_data = self._to_nsdata(payload)
                            if ns_data is None:
                                continue
                            item.setData_forType_(ns_data, str(t))
                            ok_item = True
                        except Exception:
                            continue
                    if ok_item:
                        new_items.append(item)
                if new_items:
                    pb.writeObjects_(new_items)
                    restored_any = True
            except Exception:
                restored_any = False

        if not restored_any:
            # Fallback: restore Qt mime formats we captured.
            try:
                from PySide6.QtCore import QByteArray, QMimeData

                mime = QMimeData()
                for name, data in snapshot.items():
                    if not isinstance(name, str) or not name.startswith("qt:"):
                        continue
                    fmt = name[3:]
                    try:
                        mime.setData(fmt, QByteArray(data))
                    except Exception:
                        pass
                if not mime.formats().isEmpty() if hasattr(mime.formats(), "isEmpty") else mime.formats():
                    self.clipboard.setMimeData(mime)
            except Exception:
                pass

    def _to_nsdata(self, payload):
        try:
            if payload is None:
                return None
            if hasattr(payload, "bytes") and callable(payload.bytes):
                # NSData-like
                return payload
            if isinstance(payload, (bytes, bytearray)):
                return self._AppKit.NSData.dataWithBytes_length_(bytes(payload), len(payload))
            try:
                b = bytes(payload)
                return self._AppKit.NSData.dataWithBytes_length_(b, len(b))
            except Exception:
                return None
        except Exception:
            return None

    def sequence(self) -> int:
        pb = self._AppKit.NSPasteboard.generalPasteboard()
        return int(pb.changeCount())

    def paste(self) -> None:
        # Fail honestly if Accessibility not granted.
        if not is_accessibility_trusted():
            raise RuntimeError(
                "macOS Accessibility permission is required for paste injection. "
                "Enable it in System Settings > Privacy & Security > Accessibility."
            )
        key_code = 9  # kVK_ANSI_V
        Quartz = self._Quartz
        cmd_flag = int(getattr(Quartz, "kCGEventFlagMaskCommand", 1 << 20))
        src = Quartz.CGEventSourceCreate(getattr(Quartz, "kCGEventSourceStateHIDSystemState", 1))
        key_down = Quartz.CGEventCreateKeyboardEvent(src, key_code, True)
        key_up = Quartz.CGEventCreateKeyboardEvent(src, key_code, False)
        Quartz.CGEventSetFlags(key_down, cmd_flag)
        Quartz.CGEventSetFlags(key_up, cmd_flag)
        tap = getattr(Quartz, "kCGHIDEventTap", 0)
        Quartz.CGEventPost(tap, key_down)
        Quartz.CGEventPost(tap, key_up)

    def later(self, ms: int, fn):
        from PySide6.QtCore import QTimer

        QTimer.singleShot(int(ms), fn)
