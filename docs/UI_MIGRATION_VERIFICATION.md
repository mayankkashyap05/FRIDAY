# UI Migration — Verification Record (Phases 1–5)

Companion to [`UI_AUDIT_CONTRACT.md`](UI_AUDIT_CONTRACT.md), which defined the
constraints. This file records what was actually **executed and observed** during
the migration, and — just as importantly — what could not be.

> **Read this before trusting the new UI.** The dev environment is a headless
> Linux container. Qt *Quick* (the scene graph, `QQuickWindow`, and therefore
> every pixel of this interface) has never been rendered. See
> [What was NOT verified](#what-was-not-verified).

---

## 1. Test suite

```
python -m pytest tests/ -q
221 passed, 12 skipped, 11 failed
```

All 11 failures are pre-existing and confined to modules with **no UI
dependency** (`test_image_enhancement`, `test_opencv_face_verifier`,
`test_production_resilience`, `test_segmentation`). They fail on this machine for
environmental reasons:

| Failure | Cause |
| --- | --- |
| `'NoneType' has no attribute 'shape'` / `LiveConnectConfig` | OpenCV / `google-genai` unavailable or None |
| `'Desktop stats' not found ... /home/user/Desktop` | no desktop session in the container |

The UI-relevant suites are fully green:

```
tests/test_ui_siri_core.py       51 passed
tests/test_ui_contract.py        55 passed, 1 skipped   (Tk absent)
tests/test_ui_siri_qt.py         16 passed, 11 skipped  (QtGui absent)
tests/test_ui_thread_safety.py   10 passed
tests/test_p0_security.py        20 passed
```

For reference, the same command **before** this work: `206 passed, 12 failed` —
including every `test_p0_security` failure (wrong `_api_keys_exist` semantics)
and the duplicate `ui_siri` lint failures.

### What each new suite actually proves

- **`test_ui_contract.py`** — every attribute, method and constructor signature
  that `main.py` and `actions/` rely on still exists and behaves as the Tk
  version did. This is the backward-compatibility guarantee, enforced.
- **`test_ui_siri_core.py`** — the Qt-free logic: event bus, 6-state machine
  (including barge-in and the mute/silent interactions that were bugs in the
  original), card mapping, Win32 degradation.
- **`test_ui_siri_qt.py`** — `QCoreApplication`-level behaviour plus a **full
  `qmllint` sweep of all 22 QML files, with zero tolerated warnings**.
- **`test_ui_thread_safety.py`** — see §2.

---

## 2. Threading: what the testing changed

The most valuable outcome of this migration was discovering that the *obvious*
implementation of cross-thread marshalling is unsafe. Two defects were found by
running real multi-threaded load, not by reading code:

### Defect 1 — `Signal.emit()` as the transport

Original design: producers call `Signal(object).emit(thunk)`; a queued
connection runs it on the GUI thread.

Reality, reproduced with 8 threads × 250 emits:

```
Fatal Python error: bool_dealloc: deallocating True or False:
bug likely caused by a refcount error in a C extension
```

### Defect 2 — `QThread.currentThread()` on a foreign thread

To decide whether it was already on the GUI thread, the dispatcher asked Qt for
the current thread. From a non-Qt thread this makes Qt **adopt** that thread and
allocate a wrapper object per call:

```
QObject::killTimer: Timers cannot be stopped from another thread
```

…which then corrupted the queue itself.

### The fix

`UiDispatcher` now does exactly one thing on producer threads: **append to a
`deque`**. `deque.append` is atomic under the GIL. A GUI-thread `QTimer` drains
it. No Qt object is touched, no signal is emitted, no timer is started from a
producer thread. Thread identity comes from `threading.get_ident()`.

This also removes a startup race: work queued before `exec()` simply waits for
the first drain.

**Measured after the fix** (8 producers, 250 items each, real event loop):

```
errors: []
dispatcher: {'queued': 4000, 'executed': 4000, 'dropped': 0, 'pending': 0}
transcript: 400 (cap 400)
```

100 % of 4 000 cross-thread calls delivered, in order, none dropped.

### ⚠ Dependency bound added for the same reason

Under PySide6 **6.12.0** even a textbook-minimal `QAbstractListModel` aborts when
rows are inserted:

```
Fatal Python error: none_dealloc: deallocating None: bug likely caused by a
refcount error in a C extension
```

The identical script passes on **6.8.1**. Since a list-model insert happens on
the very first card the UI shows, 6.12.0 would have crashed the app on first
paint. `requirements.txt` and `pyproject.toml` therefore pin:

```
PySide6>=6.8.0,<6.12
```

Anyone revisiting that bound should re-run `tests/test_ui_thread_safety.py`
against the newer wheel first.

---

## 3. QML: lint status

```
for f in ui_siri/qml/*.qml ui_siri/qml/cards/*.qml; do
    qmllint -I ui_siri/qml "$f"
done
→ 0 warnings, 0 errors across 22 files
```

The 6.8 linter is stricter than 6.12 and caught four classes of real bug that
would have produced *runtime* warnings (QML silently discards one of the two
bindings):

| Bug | Files | Consequence avoided |
| --- | --- | --- |
| `property real x: 0` + `NumberAnimation on x` | `PerimeterGlow`, `AnchorPill`, `BaseGlassCard` | duplicate-binding warning; animation silently wins |
| `modelData` in a JS-array `Repeater` delegate | `ScreenEffects`, `TelemetryCard`, `ControlPillCard`, `InspectorDrawer` | `Unqualified access`; fragile scope resolution |
| Stale `card.acts` after a rename | `ControlPillCard` | actions row would render blank |
| No-op arithmetic in a delegate position | `ScreenEffects` | harmless, but misleading |

The lint test carries **no exemption list** — a new warning anywhere fails CI.

---

## 4. Backward compatibility

`ui.py` remains the single import point (`main.py:31`). It exposes the full
legacy surface, asserted by `test_ui_contract.py`:

- constructor `FridayUI(face_path, size=None)`
- `root` — a duck-typed Qt adapter supporting `after`, `after_cancel`, `state`,
  `withdraw`, `deiconify`, `lift`, `iconify`, `destroy`, `quit`,
  `winfo_screenwidth`, `winfo_screenheight`, `geometry`, `title`, `configure`,
  `bind`, `protocol`, `mainloop`, `update`, `update_idletasks`; unknown
  `winfo_*` are no-ops. This is what keeps `actions/system_tray.py` and
  `actions/wake_word.py` (which call `root.after` from their own threads) working.
- all `player.*` members including `write_log`, the vision and arcade contracts,
  `silent_mode`, `speaking`, `set_state`, `set_connection`, `wait_for_api_key`
- `import ui` **loads no Qt module** (asserted), so a broken PySide6 install
  still reaches the Tk fallback rather than dying at import

Escape hatches: `FRIDAY_LEGACY_UI=1` forces the original Tk UI; `make_ui()`
falls back automatically and raises a `SystemExit` with both error messages only
if *both* implementations fail.

### Behavioural fixes made along the way

| Defect | Fix |
| --- | --- |
| Tray icon created on the asyncio worker thread (owns a Win32 message loop) | created via `root.after(0, ...)` on the UI thread |
| `silent_mode` latched: reaching the state did not suppress TTS | state machine owns it, and entering text-only mode flushes queued audio (`FridayLive.flush_audio`) |
| `wait_for_api_key()` span forever if the UI died | re-checks `_closed` at 4 Hz |
| Dead `from actions.friday_wake import _matcher` | removed |
| Hardcoded `C:/Windows/Fonts/segoeuib.ttf` | font stack with fallbacks |
| API key requested via a Tk modal (app looks hung without it) | `SetupCard` in the event stream + `ui.submit_api_key()` |

---

## What was NOT verified

Stated plainly, because the alternative is a false claim of completion:

1. **Nothing has ever been rendered.** `import PySide6.QtGui` cannot even be
   imported here (`libGL.so.1` absent; stubbing it segfaults inside the Qt
   shared libraries). No `QQuickView`, no scene graph, no screenshot.
   *Therefore unverified:* every visual design decision, the glass/multi-effect
   surfaces, animation timing, perimeter glow, bloom, reticle positioning.
2. **Window behaviour is untested.** Frameless / always-on-top / tool-window
   flags, per-monitor DPI, multi-monitor anchor persistence, click-through
   hit-testing.
3. **All Win32 paths are inert.** `SetWindowDisplayAffinity`,
   `DwmSetWindowAttribute`, `SHQueryUserNotificationState`, `pycaw` ducking,
   registry theme reads, `SystemParametersInfoW` — written with guarded
   `ctypes` and a degradation table, but never executed. `win32_native.probe()`
   is the single place that decides what is available.
4. **The QML/Python boundary is only partly proven.** `qmllint` validates types
   and bindings statically; the bridge's properties and methods are checked
   against every `bridge.*` reference in QML by test. But no `QQmlEngine` has
   ever instantiated `AnchorWindow.qml` against the real bridge, so a
   `required property` mismatch at runtime remains possible.
5. **Audio earcons were never played.** `synth_earcon()` produces arrays with
   tested shape/duration; no device was opened.

### Where to look first on a real Windows machine

```
python main.py
```

1. `[Controller] ⚠️ … failed to load` → a QML import path or `required
   property` problem; the message names the file and the QML error.
2. Blurry or scaled-wrong overlay → per-monitor DPI (`win32_native`).
3. Pill invisible but process running → window flags / capture exclusion.
4. Crash on first card → PySide6 version; check the pin in §2.

---

## Files added / changed

```
ui_siri/                 new package
  event_bus.py           UiState/CardKind/Activity/Severity, thread-safe bus
  state_machine.py       6-state lifecycle, barge-in, mute/silent semantics
  card_mapper.py         log line / payload -> card + activity
  win32_native.py        guarded Win32 capabilities, single degradation point
  audio_ux.py            earcon synthesis, pycaw ducking
  bridge.py              QObject bridge, 3 list models, UiDispatcher
  image_provider.py      image://friday/<stream>?rev=N for camera + arcade
  controller.py          4 window layers, monitor tracking, tray hooks
  themes.py              Qt-free palette table (shared with the facade)
  qml/                   22 files: windows, pill, cards, inspector, setup
ui.py                    rewritten as the compatibility facade
ui_legacy.py             original 1300-line Tk UI, verbatim (git mv)
main.py                  make_ui, tray thread fix, flush_audio, wake-status sync
requirements.txt          PySide6 pinned
pyproject.toml            dependency, QML package-data, legacy-ui extra
tests/                   +188 tests across 4 new modules
docs/UI_AUDIT_CONTRACT.md   the constraints this work was built against
```
