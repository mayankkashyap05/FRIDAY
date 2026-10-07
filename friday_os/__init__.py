"""Friday desktop assistant runtime."""

from __future__ import annotations

import ctypes
import os
import sys
import types

__version__ = "7.0.0"

if not hasattr(os, "startfile"):
    os.startfile = lambda *args, **kwargs: None  # type: ignore[attr-defined]

# Provide non-Windows / headless compatibility shims so modules and unit tests
# can be imported and exercised on any platform while preserving native Windows
# and Tk behavior whenever available.
if not hasattr(ctypes, "windll"):
    class _WinFunctionStub:
        def __call__(self, *args, **kwargs):
            return 0

    class _WinDllNamespace:
        def __getattr__(self, name: str):
            func = _WinFunctionStub()
            setattr(self, name, func)
            return func

    class _WinDllLoader:
        def __init__(self) -> None:
            self.user32 = _WinDllNamespace()
            self.kernel32 = _WinDllNamespace()
            self.wininet = _WinDllNamespace()
            self.shell32 = _WinDllNamespace()

        def __getattr__(self, name: str) -> _WinDllNamespace:
            ns = _WinDllNamespace()
            setattr(self, name, ns)
            return ns

    ctypes.windll = _WinDllLoader()  # type: ignore[attr-defined]
    if not hasattr(ctypes, "WINFUNCTYPE"):
        ctypes.WINFUNCTYPE = ctypes.CFUNCTYPE  # type: ignore[attr-defined]

try:
    import tkinter as _tk  # noqa: F401
except ModuleNotFoundError:
    class _TclError(RuntimeError):
        pass

    class _WidgetStub:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def __getattr__(self, name: str):
            return lambda *a, **kw: None

    class _TkStub(_WidgetStub):
        def __init__(self, *args, **kwargs) -> None:
            raise _TclError("no display name and no $DISPLAY environment variable")

    class _VarStub:
        def __init__(self, master=None, value=None, name=None) -> None:
            self._value = value

        def get(self):
            return self._value

        def set(self, value) -> None:
            self._value = value

    _tk_mod = types.ModuleType("tkinter")
    _tk_mod.TclError = _TclError  # type: ignore[attr-defined]
    _tk_mod.Tk = _TkStub  # type: ignore[attr-defined]
    _tk_mod.Toplevel = _WidgetStub  # type: ignore[attr-defined]
    _tk_mod.Frame = _WidgetStub  # type: ignore[attr-defined]
    _tk_mod.Canvas = _WidgetStub  # type: ignore[attr-defined]
    _tk_mod.Label = _WidgetStub  # type: ignore[attr-defined]
    _tk_mod.Button = _WidgetStub  # type: ignore[attr-defined]
    _tk_mod.Entry = _WidgetStub  # type: ignore[attr-defined]
    _tk_mod.Text = _WidgetStub  # type: ignore[attr-defined]
    _tk_mod.Listbox = _WidgetStub  # type: ignore[attr-defined]
    _tk_mod.Scrollbar = _WidgetStub  # type: ignore[attr-defined]
    _tk_mod.Checkbutton = _WidgetStub  # type: ignore[attr-defined]
    _tk_mod.Radiobutton = _WidgetStub  # type: ignore[attr-defined]
    _tk_mod.Scale = _WidgetStub  # type: ignore[attr-defined]
    _tk_mod.Spinbox = _WidgetStub  # type: ignore[attr-defined]
    _tk_mod.Variable = _VarStub  # type: ignore[attr-defined]
    _tk_mod.StringVar = _VarStub  # type: ignore[attr-defined]
    _tk_mod.BooleanVar = _VarStub  # type: ignore[attr-defined]
    _tk_mod.IntVar = _VarStub  # type: ignore[attr-defined]
    _tk_mod.DoubleVar = _VarStub  # type: ignore[attr-defined]
    _tk_mod.END = "end"  # type: ignore[attr-defined]
    _tk_mod.BOTH = "both"  # type: ignore[attr-defined]
    _tk_mod.LEFT = "left"  # type: ignore[attr-defined]
    _tk_mod.RIGHT = "right"  # type: ignore[attr-defined]
    _tk_mod.TOP = "top"  # type: ignore[attr-defined]
    _tk_mod.BOTTOM = "bottom"  # type: ignore[attr-defined]
    _tk_mod.X = "x"  # type: ignore[attr-defined]
    _tk_mod.Y = "y"  # type: ignore[attr-defined]
    _tk_mod.W = "w"  # type: ignore[attr-defined]
    _tk_mod.E = "e"  # type: ignore[attr-defined]
    _tk_mod.N = "n"  # type: ignore[attr-defined]
    _tk_mod.S = "s"  # type: ignore[attr-defined]
    _tk_mod.NW = "nw"  # type: ignore[attr-defined]
    _tk_mod.NE = "ne"  # type: ignore[attr-defined]
    _tk_mod.SW = "sw"  # type: ignore[attr-defined]
    _tk_mod.SE = "se"  # type: ignore[attr-defined]
    _tk_mod.CENTER = "center"  # type: ignore[attr-defined]
    _tk_mod.WORD = "word"  # type: ignore[attr-defined]
    _tk_mod.DISABLED = "disabled"  # type: ignore[attr-defined]
    _tk_mod.NORMAL = "normal"  # type: ignore[attr-defined]
    _tk_mod.FLAT = "flat"  # type: ignore[attr-defined]
    _tk_mod.SINGLE = "single"  # type: ignore[attr-defined]

    for _sub_name in ("filedialog", "messagebox", "scrolledtext", "ttk"):
        _sub_mod = types.ModuleType(f"tkinter.{_sub_name}")
        _sub_mod.ScrolledText = _WidgetStub  # type: ignore[attr-defined]
        _sub_mod.Notebook = _WidgetStub  # type: ignore[attr-defined]
        _sub_mod.Combobox = _WidgetStub  # type: ignore[attr-defined]
        _sub_mod.Treeview = _WidgetStub  # type: ignore[attr-defined]
        _sub_mod.Style = _WidgetStub  # type: ignore[attr-defined]
        _sub_mod.Frame = _WidgetStub  # type: ignore[attr-defined]
        _sub_mod.showinfo = lambda *a, **kw: None  # type: ignore[attr-defined]
        _sub_mod.showwarning = lambda *a, **kw: None  # type: ignore[attr-defined]
        _sub_mod.showerror = lambda *a, **kw: None  # type: ignore[attr-defined]
        _sub_mod.askyesno = lambda *a, **kw: False  # type: ignore[attr-defined]
        _sub_mod.askopenfilename = lambda *a, **kw: ""  # type: ignore[attr-defined]
        _sub_mod.asksaveasfilename = lambda *a, **kw: ""  # type: ignore[attr-defined]
        _sub_mod.askdirectory = lambda *a, **kw: ""  # type: ignore[attr-defined]
        setattr(_tk_mod, _sub_name, _sub_mod)
        sys.modules[f"tkinter.{_sub_name}"] = _sub_mod

    sys.modules["tkinter"] = _tk_mod
