# FRIDAY — UI audit contract

> The binding contract between the untouched backend (`main.py`, `actions/`,
> `agent/`, `vision/`, `memory/`, `safety.py`) and the Electron frontend.
> Every clause below is asserted by a test; changing one without changing the
> other is a bug.

## 1. One package, one UI

| Rule | Enforced by |
|---|---|
| `import ui` resolves to the package `ui/` | `tests/test_ui_contract.py` |
| `ui/__init__.py` lives two levels below the repo root | `test_get_base_dir_points_at_the_repo_root` |
| No root `ui.py`, no Qt/QML submodule, no legacy shim | `test_no_legacy_fallback_remains` |
| `ui/win32_native.py` contains no GUI-toolkit fallback | source AST scan |

`get_base_dir()` **must** resolve `Path(__file__).resolve().parents[1]` — the
repository root — because `main.py` builds every config/data path from it.

## 2. Import purity

```python
import ui            # stdlib + ui.themes only
```

At import time `ui` must not:

* import a GUI toolkit (any `PySide*`, `PyQt*`, `shiboken*` top-level module),
* spawn Electron (`subprocess`),
* bind a socket (`websockets`, `asyncio`),
* pull heavy optional dependencies (`cv2`, `sounddevice`, `numpy`, …).

`make_ui()` is what starts the world: it constructs `UiController`, which binds
`127.0.0.1:0`, mints `secrets.token_urlsafe(32)`, and spawns
`ui/app/node_modules/electron/dist/electron.exe` **directly** (never `cmd.exe`,
never `electron.cmd`).

## 3. Module surface

```python
from ui import (
    FridayUI, make_ui, get_base_dir,
    BASE_DIR, CONFIG_DIR, API_FILE, THEME_FILE,
    THEMES, THEME_NAMES, DEFAULT_THEME, SYSTEM_NAME, MODEL_BADGE,
)
```

`THEMES` is `dict[str, dict]`, `THEME_NAMES` a tuple of its keys,
`DEFAULT_THEME == "siri"`, `SYSTEM_NAME == "FRIDAY"`.

## 4. `FridayUI` — the backend's whole view of the UI

Called by `main.py` and by `actions/*`:

```
start_speaking, stop_speaking, set_state, set_connection, set_connecting,
set_persona, write_log, set_theme, cycle_theme,
set_vision_signal, set_vision_detections, set_vision_frame,
open_vision_preview, close_vision_preview,
set_arcade_frame, set_arcade_signal, set_arcade_pose,
open_vision_arcade, close_vision_arcade,
show, hide, invoke, dismiss, raise_window, quit,
run, mainloop, wait_for_api_key, submit_api_key, diagnostics,
set_live_transcript, _api_keys_exist, _dispatch, _safe_ui
```

Behavioural requirements:

* `_dispatch(fn, *args)` is **thread-safe** and never raises: it queues onto
  `UiDispatcher`, which drains on its own loop thread.
* `_safe_ui(fn, *args)` swallows and logs; it is the guard for callbacks fired
  from audio/vision threads.
* `_api_keys_exist()` must tolerate an unbound `Stub()` (`__getattr__` returning
  a no-op) — the sandbox tests pass one.
* `write_log(text)` never blocks and never re-enters the UI thread.
* `quit()` is idempotent; a second call is harmless.

## 5. Root duck-type

Legacy call sites do `ui.root.after(...)`, `ui.root.state("withdrawn")`,
`ui.root.withdraw()`, `ui.root.deiconify()`, `ui.root.lift()`,
`ui.root.destroy()`, `ui.root.mainloop()`,
`ui.root.report_callback_exception`. `ui._RootAdapter` implements all of them over
the controller; unknown attributes resolve to a no-op callable so nothing from the
old Tk era can raise at startup.

## 6. WebSocket protocol

**Transport.** `ws://127.0.0.1:<ephemeral port>?token=<32 bytes, urlsafe>`, also
accepted as `Authorization: Bearer <token>`. A wrong/missing token is refused with
HTTP 401 at the handshake, and with close code **4001** once upgraded.

**Python → renderer** (text, compact JSON, `t` discriminator):

```
snapshot · state · amplitude · live_transcript · transcript_append ·
transcript_clear · cards · activities · persona · vision · arcade ·
setup · inspector · toast · wake · theme · pong · shutdown
```

* `snapshot` is pushed on **every** connect (and reconnect) and carries
  `state, cards, activities, activeCount, transcript, persona, vision, arcade,
  setup, inspector, theme, wake`. It is rebuilt on the UI thread after each delta
  (`_snapshot_cache`), never read from live stores on the socket thread.
* `cards` carries `items[]` (`uid, kind, title, body, footer, payload, severity,
  ts, ttl, dismissible, age`); `CardsModel` caps at 8 and replaces in place by
  `(kind, title)`.
* `activities` carries `items[]` (`aid, title, kind, detail, progress, active,
  ts`) + `activeCount`; capped at 16, pruned 25 s after finishing.
* `transcript_append` is one `LogEvent` (`text, channel, severity, source, ts,
  seq, clock`); `TranscriptModel` caps at 400.
* `amplitude` is rate-limited to **60 Hz** and only emitted when the level moved
  by ≥ 0.008.

**Binary frames** are `<stream byte><jpeg>`:

| byte | stream | notes |
|---|---|---|
| `\x01` | vision | JPEG q78, max side 960 px |
| `\x02` | arcade | same encoder |

`FramePump` is latest-frame-wins with a 1-slot mailbox: a slow renderer drops
frames instead of queueing them, `stats()` exposes `encoded` / `dropped`, and no
JPEG is encoded at all while no client is watching.

**Renderer → Python** (intents, unknown types ignored silently):

```
submit_text · submit_api_key · dismiss_card · clear_cards · clear_transcript ·
cancel_activity · toggle_mute · toggle_silent · cancel · barge_in ·
open_inspector · close_inspector · vision_action · arcade_game · command ·
window_dismissed · interaction · typing_changed · pointer_inside ·
set_theme · ping → pong · request_quit
```

## 7. Controller contract

`UiController` also owns the window hygiene the backend depends on:

* dismissal grace — 5 s base, extended while `_busy()` (BOOTING/THINKING/
  PROCESSING/SPEAKING), while a card is unread, while the inspector is open, or
  while the pointer is inside; `_check_dismissal()` re-armed on every input;
* `T_SHUTDOWN` is broadcast **unconditionally** (even headless) and pending writes
  are flushed before the socket closes with 1001;
* `shutdown()` is idempotent and joins the loop without a second
  `run_until_complete`;
* SIGINT shuts down gracefully once and `os._exit(130)` on the second;
* `enable_overlay=False` gives a fully headless controller (tests, CI) that still
  exercises bridge, state machine and WebSocket;
* `set_wake_status(active, status)` and `shutdown()` are the two entry points the
  tray/hotkey layer calls.

## 8. Failure policy

| Situation | Behaviour |
|---|---|
| Renderer missing / not built | `make_ui` raises `SystemExit` with a message naming the absolute `ui/app` path and `npm install` / `npm run build` |
| Electron package installed but its binary absent (npm's silent half-install) | the same `SystemExit` names the missing binary and says `npm rebuild electron`; `setup.py` fails instead of reporting success |
| Electron exits unexpectedly | the watcher calls `request_quit()`; FRIDAY never lingers windowless |
| Renderer crashes | Electron `render-process-gone` quits the app |
| WebGL2 unavailable | orb falls back to a CSS radial gradient **inside the same stage** (not a second UI) |
| Socket delivery fails | the controller logs and keeps serving; the renderer reconnects with backoff and exits ~1.2 s after `shutdown` |

## 9. What must never reappear

QML documents, a Qt binding as a dependency, a second UI package, a Tk
fallback, an
environment variable that switches frontends, or a `ui.py` module at the
repository root. `scripts/verify_windows_ui.py` asserts the Qt half of this on
every run; `tests/test_ui_contract.py` asserts the rest.
