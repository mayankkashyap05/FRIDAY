# Native FRIDAY UI

`python main.py` starts the Qt facade in `ui/__init__.py`. The explicit
`--headless` option runs Qt with the offscreen platform; it does not silently
replace the normal interface when a graphical platform is unavailable. The
facade provides the legacy-facing state, transcript, logging, visibility,
callback, API-key and lifecycle methods. API-key entry and all widget work run
on the Qt thread. Logs received before the event loop are buffered with a
fixed limit.

`bridge/event_bus.py` and `bridge/bridge.py` provide bounded event delivery and
queued dispatch to Qt. `bridge/controller.py` connects the HUD and panel
services. `panels/common.py` runs blocking operations on bounded workers and
posts results back to Qt. Settings are validated and written atomically by
`settings_store.py`; QSS resources are in `themes/`. The system tray uses
`QSystemTrayIcon` in `tray/`.

High-risk tool requests are gated by the backend's one-shot, argument-bound
confirmation tokens and a separate human approval. The chat card displays a
sanitized description and arguments; an answer applies only to its current
request. Approval sends a message to the assistant, which must retry the exact
tool call with its token. A disconnected assistant cannot approve via the
card. The existing voice/text confirmation path also binds consent to the
current pending token. This integration still requires real-session testing.

## Reference mapping

* `core/anim.ts`: spring response 0.5 s, damping 0.72, 1/240 s integration
  substeps and settle thresholds 0.01 and 0.05. The frame delta is capped at
  0.05 s. The 340 ms cubic-bezier shrink curve is implemented but not wired.
* `core/layout.ts`: compact 288 × 32 and expanded 640 × 160. The resting notch
  uses the larger proportions from the visual references.
* `island/fsm.ts`: 15 s home collapse, 60 s petit hide, 0.5 s hover-open,
  0.6 s hover-close and 10 s hovered greeting. Hover-open is enabled by
  default here, unlike the reference.
* `mochi/desktop-logic.ts`: 3 px drag threshold and tanh-based gaze. Eyes are
  drawn as pills; the full reference mascot engine is not used.
* `island/win32_overlay.py` isolates Windows native calls. Other platforms use
  guarded fallbacks.

## Known limitations

Dictation does not yet paste into an external application. Not every requested
panel feature, eye animation or visual detail is implemented. The offscreen
snapshot renderer exercises actual widgets but is not a native desktop test.
Windows nonactivation, hit testing, focus, DPI, fullscreen detection, tray and
live backend flows need validation on Windows. The Qt timer repaints regularly;
no idle-CPU target has been verified. The Linux test sandbox uses temporary
system-library stubs solely for offscreen rendering; these are not runtime
dependencies or substitutes for desktop system libraries.
