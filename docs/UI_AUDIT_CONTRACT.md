# FRIDAY — Phase 0 Audit & Compatibility Contract

> **Status:** Phase 0 COMPLETE (read-only audit). This document is the gate for Phase 1+.
> **Branch:** `arena/0a1af41c-friday` @ `495aa14`
> **Auditor:** Principal Windows/Qt UI systems pass
> **Date:** 2026-11-21

---

## 0. Headline correction (read this first)

**The premise "transform `ui.py`… Qt6/PySide6" is only half-true.**

`ui.py` is **not** Qt. It is **1 300 lines of Tkinter + PIL/Pillow canvas drawing**
(`tk.Tk`, `tk.Canvas`, `tk.Text`, `ImageTk.PhotoImage`). There is **no PySide6, no PyQt,
no QML anywhere in the repository**, and `requirements.txt` / `pyproject.toml` list **no GUI
framework at all**.

Implication: this work is a **framework migration (Tkinter → Qt Quick)**, not a QML
restyle. Consequences that shaped the design:

1. `PySide6` must be added to `requirements.txt` + `pyproject.toml` (done in Phase 4).
2. The Qt event loop must become the process's main loop, replacing `tk.mainloop()`.
3. Pillow is currently load-bearing for the entire render path (`ImageTk`); in the new
   stack Pillow keeps only the role of `QQuickImageProvider` frame conversion.
4. Tkinter must **not** be imported at all by the new facade, so a Windows box whose
   CPython lacks Tk cannot fail to boot. Qt becomes a *hard* requirement instead — this is
   an accepted, documented trade-off (Phase 4 adds a friendly `SystemExit` message if
   PySide6 is missing, matching the existing `_ensure_core_deps()` pattern).

---

## 1. Environment inspection

| Item | Audited value | Notes |
|---|---|---|
| Target OS | Windows 10 2004+ / Windows 11 | per `pyproject.toml` classifiers |
| `requires-python` | `>=3.11` | classifiers also claim 3.12–3.14 |
| Audit sandbox | Debian 12 (bookworm), Linux 6.1, **Python 3.11.2** | *cannot* execute the Windows-only paths |
| PySide6 | **6.12.0 / Qt 6.12.0** | verified installed & importable in sandbox |
| QML modules verified present | `QtQuick`, `QtQuick.Controls`, `QtQuick.Layouts`, `QtQuick.Shapes`, `QtQuick.Effects`, `QtQuick.Window`, `QtQml.Models`, `QtQuick.Dialogs`, `QtQuick.Templates` | via `PySide6/Qt/qml/**` |
| `MultiEffect` | **available** (`QtQuick.Effects/MultiEffect 6.5/6.7/6.12`) | gated at runtime to `Qt >= 6.5` |
| `qmllint` | present at `PySide6/qmllint` | used as the QML syntax/type validator |
| `tkinter` in sandbox | **absent** (`ModuleNotFoundError`) | confirms Tk path is untestable here; also why the facade must not import tkinter |
| `Pillow` | absent in sandbox initially, installed for testing | Windows side already requires it |
| `pyside6-qsb` (shader compiler) | **not shipped** in this wheel | → see §6, no shaders used |
| GUI framework in requirements | **none** | must add PySide6 |
| `requirements.txt` vs `pyproject.toml` | pyproject omits `keysound`, `mediapipe`, `ultralytics`, `pytesseract`, `openwakeword`, `youtube-transcript-api`, `psutil`-parity is fine, and is missing `beautifulsoup4`-parity is fine | pre-existing drift, out of scope; **no conflict** with adding PySide6 |
| `pyaudio` | imported by `actions/wake_word.py` but **not** in requirements | pre-existing, falls back to `sounddevice`; out of scope |

### Launch paths (verified)
* `start_friday.ps1` → resolves `.venv\Scripts\pythonw.exe` first, then `pythonw.exe`,
  then `python.exe`; kills prior `main.py` processes; `Start-Process … -ArgumentList @("-u", main.py)`.
  **No `pythonw -u` assumption is broken by adding Qt**; note `-u` is a no-op for `pythonw`
  but harmless.
* `actions/autostart.py::_launch_command()` → `"{pythonw.exe}" -u "{main.py}"` written to
  `HKCU\...\CurrentVersion\Run`.
* `pyproject.toml [project.scripts] friday = "main:main"`.
* Frozen support: `get_base_dir()` honours `sys.frozen` / `sys.executable` in `main.py`,
  `ui.py`, `actions/autostart.py`, `actions/vision_click.py`.
  **Risk:** `ui_siri/qml/*.qml` are data files. A PyInstaller build must add them via
  `--add-data`. Documented in Phase 4; runtime resolves QML from
  `Path(__file__).parent / "qml"` which works for `sys.frozen` **only if** the QML ships next
  to the binary. A fallback to `Qt resource (:/)` layout is *not* implemented — instead the
  controller logs a clear error and falls back to the legacy-safe minimal window.

---

## 2. Core entry & UI inspection

### 2.1 `main.py` (2 856 lines) — event-loop topology (CRITICAL: already correct)

```python
def main():
    _ensure_core_deps()
    ui = FridayUI("face.png")                    # ← MAIN OS THREAD
    def runner():                                # ← worker thread
        ui.wait_for_api_key()
        friday = FridayLive(ui); friday.attach_tray()
        asyncio.run(friday.run())
    threading.Thread(target=runner, daemon=True).start()
    ui.root.report_callback_exception = _tk_exception_handler
    ui.root.mainloop()                           # ← MAIN OS THREAD
```

**Verdict: no inversion is required.** `QApplication.exec()` can take the exact slot of
`ui.root.mainloop()` on the main OS thread, and the asyncio/voice loop stays in `runner()`.
The prompt's feared inversion problem **does not exist in this codebase** — `main.py` already
does the right thing. The contract is simply: *keep this shape; the Qt app object and
`exec()` live on the main thread; `runner()` stays a daemon thread.*

### 2.2 Threads that touch the UI (all must be marshalled)

| # | Producer thread | Touches |
|---|---|---|
| 1 | asyncio loop (`FridayLive.run` / `_receive_audio` / `_monitor_loop` / `_scheduler_loop` / telegram) | `write_log`, `set_state`, `set_connection`, `set_connecting`, `set_persona`, `muted` |
| 2 | `VisionGestureThread` (`actions/vision_gesture.py:179` `configure(player=ui)`) | `set_vision_frame`, `set_arcade_frame`, `set_arcade_signal`, `set_arcade_pose`, `set_vision_signal`, `set_vision_detections`, `write_log`, reads `vision_preview_open`, `arcade_open` |
| 3 | `WakeWordThread` | `_on_wake_detect` → writes `ui.muted`, `write_log` |
| 4 | `TrayThread` (pystray) | via `_on_ui(cb)` → `ui.root.after(0, cb)` |
| 5 | `keyboard` hotkey thread | same `_on_ui` wrapper |
| 6 | `AgentTaskQueue` worker (`agent/task_queue.py`, `threading.Thread`) | `speak` → `ui.write_log` indirectly |
| 7 | one-shot `threading.Thread` from `_on_input_submit` / `_deck_command` | `on_text_command(text)` |

### 2.3 `ui.py` — complete public surface

Constructor: `FridayUI(face_path, size=None)`.
`size` is accepted but **unused** — must stay accepted.

Module-level (imported by `tests/test_p0_security.py`): `API_FILE`, `CONFIG_DIR`, `BASE_DIR`,
`THEMES`, `SYSTEM_NAME`, `MODEL_BADGE`, `get_base_dir`, `_load_theme_name`, `_save_theme_name`,
class `FridayUI` with `FridayUI._api_keys_exist` used as an **unbound** method against a
`Stub()` instance (so `_api_keys_exist` must not require `self.__init__`).

---

## 3. THE LEGACY COMPATIBILITY CONTRACT

Everything below was derived by exhaustive `rg` over the repo, not by guesswork. **Each row is
a hard requirement.** The new `ui_siri` facade must satisfy all of it.

### 3.1 Attributes written by callers
| Attribute | Written at | Semantic |
|---|---|---|
| `muted` | `main.py:1395, 2338, 2346`; `ui._toggle_mute`; wake detect | bool. Written **directly, from asyncio + wake threads**. Facade must expose a property whose setter marshals to GUI thread and re-renders. |
| `on_text_command` | `main.py:1361` | `Callable[[str], None] \| None`, set once at startup. |
| `silent_mode` | set inside legacy UI only | bool, read by `main.py:2599, 2606, 2687` via `getattr(ui,"silent_mode",False)`. **Never written by main.py** (Bug D, §5). |

### 3.2 Attributes read by callers
`muted` (16×), `silent_mode` (3×), `vision_preview_open`, `arcade_open` (via `getattr`
in `vision_gesture.py:265,271`), `root` (10×), `on_text_command`, `_api_key_ready`
(inside `wait_for_api_key`).

### 3.3 Methods called from outside `ui.py`
| Method | Call sites |
|---|---|
| `write_log(text: str)` | 30× in `actions/`, 24× in `main.py`, 1× in `vision_gesture` |
| `set_state(state: str)` | 18× in `main.py` |
| `set_persona(mode, mood, voice)` | 4× in `main.py` |
| `set_connection(ok: bool)` | 2× |
| `set_connecting(on: bool = True)` | 2× |
| `set_vision_signal(text)` | `vision_gesture` ×3 |
| `set_vision_detections(events)` | `vision_gesture` ×1 (events = objects with `.label`) |
| `set_vision_frame(frame)` | `vision_gesture` ×1 (BGR `np.ndarray`) |
| `open_vision_preview()` | `_deck_command` |
| `open_vision_arcade()` | `_deck_command` |
| `set_arcade_frame(frame)` | `vision_gesture` |
| `set_arcade_signal(gesture, x=0.5)` | `vision_gesture` |
| `set_arcade_pose(arms_raised, lean=0.0)` | `vision_gesture` ×4 |
| `wait_for_api_key()` | `main.py:2831` (worker thread) |
| `_toggle_mute()` | `main.py:1451, 1463` (tray + hotkey) |
| `_draw_mute_button()` | `main.py:2341, 2349` |
| `_safe_ui(fn, *args)` | `main.py:2341, 2349` |
| `start_speaking()` / `stop_speaking()` | none currently — keep anyway |

### 3.4 `set_state` accepted values
Empirically only these are passed: `LISTENING` (14), `THINKING` (5), `SPEAKING` (3),
`MUTED` (2), `PROCESSING` (1), `ONLINE` (1). The legacy `_set_state` `else` branch maps
**anything else** to `ONLINE`, so unknown strings must not raise. Additionally
`_toggle_silent_mode` can pass `"TEXT-ONLY"`.

### 3.5 `root` duck-typed surface (Tk object leaking through the API)
| Member | Used by |
|---|---|
| `root.after(ms, cb)` | `main.py:1407` (`_on_ui`), `main.py:1446` |
| `root.state()` | `main.py:1432` |
| `root.withdraw()` / `deiconify()` / `lift()` | `main.py:1434–1437` |
| `root.destroy()` | `main.py:1446`, `main.py:2849` |
| `root.mainloop()` | `main.py:2840` |
| `root.report_callback_exception = fn` | `main.py:2838` |

→ **The facade must publish a `root` object that quacks like a Tk root.** Implemented as
`LegacyRootAdapter` wrapping the Qt app + `AnchorPill` window. This is the single most
important compatibility shim in the design, and it also *fixes* Bug A (§5) because a Qt-based
`.after()` is genuinely thread-safe.

### 3.6 Module-level contract used by `tests/test_p0_security.py`
`import ui` → `ui.API_FILE` must be a reassignable module global; `ui.FridayUI._api_keys_exist`
must be usable unbound on a bare `Stub()` object (so it may only use `self` for nothing, or
tolerate a missing attribute).

---

## 4. Architecture & risk sign-off

### 4.1 Chosen target layout
As specified in the brief: `ui.py` stays as the **public facade**; all new code lives in
`ui_siri/` (`event_bus.py`, `state_machine.py`, `win32_native.py`, `audio_ux.py`,
`card_mapper.py`, `bridge.py`, `image_provider.py`, `controller.py`, `qml/**`).

### 4.2 Thread-safety design (the core of the migration)
* **`UiDispatcher`** (in `controller.py`): a `QObject` with one signal. Every legacy public
  method body is `self._dispatcher.call(fn, *args)`. If `QThread.currentThread()` is the GUI
  thread → call inline; otherwise emit the queued signal and return **immediately**
  (never block a producer thread). This is the Qt equivalent of legacy `_safe_ui` but with
  the crucial extra: it works **before** `exec()` starts (events queue up).
* **`EventBus`**: `queue.Queue` based, drained by a `QTimer` on the GUI thread. Used for
  high-frequency frame/telemetry traffic so we never emit thousands of queued signals.
* **`ImageProvider`**: `QQuickImageProvider` with an internal frame slot + a monotonically
  increasing revision id. Producers only *write* a numpy/PIL buffer under a `threading.Lock`
  and bump a counter; QML requests by URL `image://friday/vision?rev=N`. Zero Qt-object
  touching from producers, zero busy-polling.
* **Audio UX / Win32 / pycaw**: all calls run on dedicated short-lived daemon threads; only
  scalar results are marshalled back.

### 4.3 `QApplication` vs `QGuiApplication`
Use `QApplication` (from `PySide6.QtWidgets`) because `pystray`/system tray interop and
`QDockWidget`-free widget-based fallbacks behave more predictably, and because
`QApplication` is a superset. **Risk:** pulls in `libQt6Widgets` — acceptable, same wheel.
If `QtWidgets` import fails, fall back to `QGuiApplication` and disable tray hooks.

### 4.4 Reduced-motion / transparency / theme
* Windows theme: read `HKCU\Software\Microsoft\Windows\CurrentVersion\Themes\Personalize\AppsUseLightTheme`
  via `winreg`, guarded; fall back to dark.
* Reduced motion: `SPI_GETCLIENTAREAANIMATION` via `SystemParametersInfoW`, guarded.
* Transparency: `SPI_GETACTIVEWINDOWTRACKING`/`DwmIsCompositionEnabled` guarded; fall back
  to opaque frosted surfaces.

### 4.5 Windows API fallback matrix (all guarded, never fatal)
| API | Failure mode | Fallback |
|---|---|---|
| `SetWindowDisplayAffinity` | non-rectangular/child window, pre-Win10 2004 | log once, skip capture exclusion |
| `DwmSetWindowAttribute` (DWMWA_SYSTEMBACKDROP_TYPE / DWMWA_WINDOW_CORNER_PREFERENCE / DWMWA_USE_IMMERSIVE_DARK_MODE) | unsupported attr on older builds (returns `E_INVALIDARG`) | keep plain translucent window |
| `SetLayeredWindowAttributes` / `WS_EX_LAYERED` | already layered / DWM off | skip |
| `SHQueryUserNotificationState` | shell not ready | assume "accepting" |
| `pycaw` duck/session | COM init failure, no audio device | no-op ducking |
| `SystemParametersInfoW` | returns 0 | assume animations enabled |
| `winreg` theme read | key missing | default dark |

### 4.6 Which Windows paths are *verifiable here*
Nothing Windows-specific can execute in this Linux sandbox. Mitigation:
1. every ctypes call is wrapped in try/except returning a documented default;
2. `win32_native.py` exposes pure-Python `probe*` functions that return *reasons*, so the
   test-suite can assert graceful degradation on Linux;
3. `tests/test_ui_siri.py` asserts import + instantiate + state-machine + card-mapping +
   dispatcher thread-safety on Linux, and the *headless* Qt path where available.

---

## 5. Defects found during audit (to fix, not to preserve)

| ID | Defect | Fix |
|---|---|---|
| **A** | `main.py:1407/1446` call `ui.root.after(...)` **from the pystray/keyboard threads**. Tk's `after` is not thread-safe → cross-thread Tcl crash / lost callbacks. | `LegacyRootAdapter.after` is thread-safe by construction (queued Qt signal). |
| **B** | `main.py:1440` `from actions.friday_wake import _matcher as kw_matcher` — **module does not exist** (`ls actions/` has no `friday_wake.py`). Swallowed by `except Exception`. | Remove dead import in Phase 4. |
| **C** | `main.py:2341/2349` depend on private `ui._safe_ui` + `ui._draw_mute_button`. | Keep both as public-ish façade methods (contract §3.3). |
| **D** | `silent_mode` is latched forever: `_toggle_mute` never clears it, and `main.py` never resets it. Once a user presses F6, TTS + mic capture stay suppressed across mute cycles. | New state machine owns `silent_mode` as an explicit, persisted-safe flag with an unmute escape. |
| **E** | `_ui_deferred` is drained only inside `_animate()`; if Tk is busy or the window is withdrawn, cross-thread UI calls are invisible for up to a frame. | Qt queued signals guarantee FIFO delivery independent of paint. |
| **F** | `_render_arcade_frame` hardcodes `C:/Windows/Fonts/segoeuib.ttf`; any resolution failure aborts the whole frame render. | Resolve a font with fallbacks; render HUD in QML instead of PIL. |
| **G** | `wait_for_api_key()` is `while not ready: sleep(0.1)` in the *runner* thread — leaks a thread forever if the user never enters a key. | Keep signature; make it wait on an `Event` set by the setup UI, with the same blocking semantics. |
| **H** | `_render_*_frame` swallow *all* exceptions silently. | New renderer logs once per unique error. |
| **I** | `open_vision_arcade` spawns `on_text_command("start vision gesture")` implicit side-effect. | Preserve behaviour (contract), but route through the state machine so it's cancellable. |

---

## 6. Forbidden techniques — confirmed decisions

* **No inline GLSL in `ShaderEffect`** — Qt 6 requires pre-compiled `.qsb`. `pyside6-qsb` is
  **not present** in the audited wheel ⇒ **no `ShaderEffect` and no `Effect` at all.**
* **No external C++ build step.** Everything is pure QML + ctypes.
* Achieved visuals via: `MultiEffect` (blur/shadow, Qt ≥ 6.5, runtime-gated),
  `Rectangle` + `Rectangle` gradients (Qt ≥ 6.6 `LinearGradient`/`RadialGradient` inside
  `Rectangle.gradient`), `OpacityMask`, `Shape`/`ShapePath` (conic-approximated iridescence via
  many arc segments), `SequentialAnimation`/`Behavior`/`SpringAnimation`, `Timer`.
* **No `QtQuick.Controls` styling tricks that need a `qmldir` deploy** beyond what ships in the
  PySide6 wheel (Controls/Layouts/Shapes/Effects/Templates all confirmed present).

---

## 7. Phase plan & verification gates

| Phase | Deliverable | Gate |
|---|---|---|
| 0 | this document | done |
| 1 | `ui_siri/{event_bus,state_machine,win32_native,audio_ux,card_mapper}.py` | `python -m compileall`, unit tests for FSM/bus/mapper, Windows-API graceful-degradation tests |
| 2 | `ui_siri/{bridge,image_provider,controller}.py` + all `qml/**` | `qmllint` clean on every `.qml`; headless instantiation of `QGuiApplication` + controller with `QT_QPA_PLATFORM=offscreen` |
| 3 | `ui.py` facade rewrite (contract §3) | contract test asserts **every** §3.3 method exists and is callable from a foreign thread; `test_p0_security` still green |
| 4 | `main.py` fixes (B), deps, docs | full `pytest` run; `python -c "import main"` |
| 5 | `tests/test_ui_siri.py` + README/docs | all green |

**Rollback safety:** `ui_siri` is additive. `ui.py` keeps a `FRIDAY_LEGACY_UI=1`
environment switch that restores the original Tk implementation (retained verbatim as
`ui_legacy.py`) so a user on a broken Qt install is never stranded. This is the
"100 % backwards compatibility" guarantee in its strongest form.
