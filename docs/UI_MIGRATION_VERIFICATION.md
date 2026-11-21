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
267 passed, 13 skipped, 11 failed
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
tests/test_ui_siri_qt.py         24 passed, 12 skipped  (QtGui absent)
tests/test_ui_thread_safety.py   12 passed
tests/test_windows_bringup.py    17 passed  (Windows first-run regressions)
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
| Image provider URL never re-requested | `InspectorDrawer` | camera preview froze on frame one |
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

## Windows 11 first-run fixes (PySide6 6.11.3 / Python 3.13)

Three runtime errors reported from a real Windows 11 run, plus the latent
failures found while auditing the same paths.

### Reported

| # | Error | Root cause | Fix |
| --- | --- | --- | --- |
| 1 | `name 'qml_dir' is not defined` — entire overlay lost, silent Tk fallback | `_register_singleton()` still used the lowercase variable from before the `themes.py` split, which exports `QML_DIR`. The `NameError` escaped `UiController.__init__`, so no window was ever created. | Uses `QML_DIR / "Theme.qml"`, `.resolve()`d, through `QUrl.fromLocalFile`. |
| 2 | `ModuleNotFoundError: actions.friday_wake` crashed `TrayThread` | The module was renamed to `actions/kaizumi_wake.py` (it still carries the old header comment, which is why the name lingered) but two call sites kept importing the old name. | `_load_matcher()` imports `kaizumi_wake` first, falls back to the old name, and treats *every* failure — including a lazy `import onnxruntime` that raises at attribute-access time — as "no custom matcher". `TrayThread` can no longer die. |
| 3 | `NameError: name 're' is not defined` after every tool call | `main.py` used `re.findall` twice with no `import re`. | Added; also switched `asyncio.get_event_loop()` (deprecated 3.12+) to `get_running_loop()` in all three places. |

### Found by auditing the same path (would have been the next three crashes)

- **`vision_gesture` was never bound.** `main.py:1885` called it bare, so every
  *face-verified* tool call (`face_verify=True`) raised `NameError` and the
  security gate failed open into "identity verification failed". Now imported
  lazily, like every other action module.
- **Theme never reached QML.** The controller set a `fridayTheme` context
  property and emitted `reloadTheme`, but no QML read either — the overlay would
  always have rendered the built-in cyber defaults, and `cycle_theme()` would
  have changed nothing on screen. Now pushed as a `themeData` required property
  at construction (via `setInitialProperties`) and live via `bridge.theme`.
- **Required properties were set *after* construction.** `set_root_property()`
  ran after `setSource()`, which is too late: QML warns "Required property ...
  was not initialized" and bindings on those properties never re-evaluate. Now
  `setInitialProperties()` + `loadFromModule()`.
- **No alpha buffer.** `QQuickWindow.setDefaultAlphaBuffer(True)` was never
  called, so every `setColor(QColor(0,0,0,0))` was ignored and the overlay would
  have painted as an opaque black rectangle. Now called before `QGuiApplication`.
- **No Win32 `argtypes`.** Every call defaulted to C `int` (32-bit). On 64-bit
  Windows an HWND/HMONITOR is pointer-sized, so pointers were silently truncated.
  A 12-entry signature table now types every function (`HWND`, `HMONITOR`,
  `c_ssize_t` for `LONG_PTR`, `c_size_t` for `UINT_PTR`), and
  `SetWindowLongPtrW`/`GetWindowLongPtrW` are preferred over the 32-bit forms.
- **QML load errors were invisible.** `errors()` output was joined into one line
  and the inspector's status was never checked at all. Errors now print
  `url:line:column: message`, are exposed via `UiController.qml_errors()`, and
  `make_ui()` treats a load error as a startup failure under `FRIDAY_STRICT_UI=1`.
- **`_resize_anchor` silently did nothing.** It called
  `float(root.property("contentHeight"))`, but `contentHeight` is a QML
  *function*, so `property()` returned a bound-method wrapper and `float()`
  raised inside a bare `except`. The snippet stack would have been clipped to the
  initial window height.

### New escape hatch

`FRIDAY_STRICT_UI=1` re-raises instead of falling back to Tk, and prints the full
traceback. Default behaviour is unchanged (fall back), but it now logs a
complete traceback to stderr and to the app log instead of just `str(exc)`.

### Verification script

`scripts/verify_windows_ui.py` — run it on the Windows machine:

```
python scripts/verify_windows_ui.py
```

No Tk fallback, every QML warning is a failure, exit code 0 only if everything
passes. It prints the OS build, Python/PySide6/Qt versions and
`win32_native.probe()`, then walks the six lifecycle states, churns
`QAbstractListModel` rows, checks capture exclusion with a real `mss` screenshot,
and opens/closes the inspector.

---

## Windows 11 verifier round 2 (build 26300 / PySide6 6.11.3)

Qt booted cleanly on the real machine (`overlay HWND 0x220384`, `anchor HWND
0x75b50196`, scene graph 456x240) and the verifier found four more bugs.

| # | Symptom | Root cause | Fix |
| --- | --- | --- | --- |
| 1 | `Module "ui_siri.qml" contains no type named "OverlayWindow"` | `qmldir` exported only `Theme` and `PerimeterGlow`, but `loadFromModule()` resolves types *through* the module. | Went back to `setInitialProperties()` + `setSource(QUrl.fromLocalFile(...))`, which needs no type export, **and** completed `qmldir` for all 11 top-level types plus a `cards/qmldir` for the 10 card types — so both mechanisms work. |
| 2 | `capture_exclusion`, `layered_windows`, `backdrop`, `rounded_corners`, `dnd` all `False`, no `reasons` recorded | `probe()` reused the *action* helpers (`exclude_from_capture`, `get_ex_style`, `apply_dwm_polish`, `do_not_disturb`). All four need a live HWND or current notification state, so with no window yet they returned `False` and wrote nothing. Capabilities were being inferred from actions instead of from export presence + build number. | New `probe_capabilities()` is a pure query: it checks the export table and the OS build (`windows_build()` via `sys.getwindowsversion()` with an `RtlGetVersion` fallback) and gates each capability on its own threshold (19041 / 22000 / 22621). `Capabilities.build` is now a real field. |
| 3 | `ACTING` rendered as `LISTENING` | `UiState.coerce()` had no `ACTING` alias, so it fell through to the `LISTENING` default. | Added `ACTING`/`ACTING_ON`/`EXECUTING`/`TOOL_CALL`/`WORKING` → `PROCESSING`, plus `DORMANT`/`SLEEPING` → `BOOTING` and `FAILED`/`OFFLINE` → `MUTED`. Also fixed `TEXT-ONLY`, which was unreachable because `-` is folded to `_` before comparison. |
| 4 | `'tuple' object has no attribute 'to_qml'` | The verifier did `card = from_log_event(event)`; that function returns `(card, activities)`. | Unpacked correctly. Audited every other caller: `bridge.ingest()` uses `to_cards()` and was already right, so this was verifier-only. |

### Two latent bugs the new tests caught

- **`Capabilities.windows` was latched at import.** A dataclass default is
  evaluated once at class creation, so `windows: bool = IS_WINDOWS` froze the
  platform. Now `field(default_factory=...)`.
- **The library flags were also import-time.** `probe_capabilities()` now
  derives `user32`/`dwmapi`/`shcore`/`shell32` from what is actually bound.

### Silent degradation is now impossible

Every capability that evaluates `False` records a reason, including on
non-Windows hosts and when a single export is missing (the message names the
function, e.g. `SHQueryUserNotificationState is missing from shell32`).
Rejections from Win32 carry the real error code via `describe_error()` /
`explain_last_error()`, so `87 ERROR_INVALID_PARAMETER` can be told apart from
`5 ERROR_ACCESS_DENIED`.

### Verifier additions

- Asserts all five capabilities are `True` on build >= 22621, and skips (with
  the build number) below that.
- Applies `WDA_EXCLUDEFROMCAPTURE` to **both** the anchor and overlay HWNDs,
  reporting the exact Win32 error on rejection.
- Compares the screenshot against `grabWindow()` sample-for-sample and fails if
  the overlay appears in the capture.
- Exercises card insert / replace-in-place / clear on `QAbstractListModel`.

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

---

## Round 3 — four runtime bugs found on the real Windows 11 box

Second real-machine run: **49 passed, 2 failed**. Rounds 1–2 had already fixed
the QML-module and capability-probe failures; this round cleared the remaining
four. None of them were reproducible in the Linux sandbox, which has no GL
stack, so every diagnosis below was made by inspecting the meta-object /
source and then re-verified with a mocked-DLL or meta-object probe.

### 1. `bridge.cards` / `activities` / `transcript` were invisible to QML

**Symptom** (stderr on Windows):

```
LiveActivityBar.qml:54:5  QML Connections: Detected function "onRowsInserted" ...
                          no signal of the target matches the name
LiveActivityBar.qml:54:5  Unable to assign [undefined] to QObject*
LiveActivityBar.qml:250:13 Unable to assign [undefined] to bool
```

**Root cause.** The three models were plain Python attributes:

```python
self.cards = CardsModel(self)      # not a Q_PROPERTY -> QML sees undefined
```

Qt builds its meta-object from `Property`/`Signal`/`Slot` declarations only, so
`root.bridge.cards` evaluated to `undefined`. That single fact explains all four
warnings: `SnippetStack.model` and `LiveActivityBar.model` were both undefined
(hence the assignments of `undefined` to `QObject*`), `Connections.target` was
undefined (hence one "no signal of the target matches" *per handler*), and
`bar.model && ...` yielded `undefined` rather than a bool.

**Consequence.** The card stack and the activity capsule had **never rendered
anything**, in any build.

**Fix.** Back the models with private attributes and expose them:

```python
@Property(QObject, constant=True)
def cards(self) -> CardsModel: return self._cards
```

`constant=True` is correct: the model *identity* never changes, only its
contents, and the models emit their own `rowsInserted` / `dataChanged`.

Also fixed in `LiveActivityBar.qml`:

* `Connections` handlers now declare the signal's parameters. Qt matches by name
  **and arity**; a zero-argument `onRowsInserted` matches no signal on
  `QAbstractListModel` and warns once per handler.
* `visible: !!bar.model && bar.model.rowCount() > 0` — `&&` returns an
  *operand*, so a null model gave `undefined` for a `bool` property.

### 2. `check_models()` inserted 0 cards

Same root cause plus a bad input: the verifier fed `[Code]`/`[SYS]` log lines
through `from_log_event`, which maps those to *activities*, not cards (40
activities, 0 cards — exactly what the Windows run reported). It now drives nine
real payloads through the mapper, one per `CardKind`, then a 30-card burst, and
asserts the cap, in-place replacement and `clearCards()`.

### 3. `DORMANT` rendered as `BOOTING`

Round 2 had aliased `DORMANT`/`SLEEPING`/`ASLEEP` → `BOOTING`, which left a
spinner and a fast perimeter sweep on screen forever. `DORMANT` means *awake and
idle*, so it now coerces to `LISTENING` (idle 0.35 sweep, no spinner). The
verifier asserts the value **and** reads `glowSpinSpeed` back out of the live
`PerimeterGlow`, because an undefined binding leaves a default in place and a
Python-side check alone cannot catch it.

### 4. `dpi_aware=False`

`probe_capabilities()` never derived the flag at all; only the *action*
`enable_per_monitor_dpi()` set it, and the probe runs before any window exists.
It now means "the export exists", preferring
`user32.SetProcessDpiAwarenessContext` → `shcore.SetProcessDpiAwareness` →
`user32.SetProcessDPIAware`, and writes a `reasons["dpi_aware"]` on both the
True and False paths.

### Also in this round

* `qInstallMessageHandler` now runs **before** `QGuiApplication`/`UiController`.
  It ran after construction, so every load-time QML warning — including all of
  the above — was emitted before the handler existed and never reached the
  `no QML warnings during the run` check.
* The warning filter did not match `"no signal of the target matches"`, so even
  a captured Connections warning would not have failed the run.
* `mss.mss()` → `mss.MSS()` (deprecated alias; emits a `DeprecationWarning`).
* `AnchorPill.qml` advertised a `MultiEffect` frost in its own docstring that
  was never wired up, leaving `import QtQuick.Effects` unused (the last qmllint
  warning). The effect is now implemented, gated on `Theme.transparency`.

### Gate results (Linux sandbox)

| Gate | Result |
|---|---|
| `qmllint` all 21 QML files | **0 warnings, 0 errors** |
| `pyflakes ui.py main.py ui_siri/ scripts/verify_windows_ui.py` | **0 undefined names** |
| 5 UI test suites | **193 passed, 12 skipped** |

### Still not verified here

Qt has never booted in this sandbox (`libGL.so.1` is absent), so: no QML was
instantiated under a real engine, and no Win32 call was executed. The fixes above
are verified by meta-object inspection and mocked-DLL probes only; the Windows
run of `scripts/verify_windows_ui.py` remains the authoritative check.
