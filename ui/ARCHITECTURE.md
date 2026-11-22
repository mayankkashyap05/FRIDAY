# Native island: audit and prototype status

This is a work-in-progress standalone prototype, **not** a replacement for the
existing FRIDAY frontend. `main.py` imports `FridayUI, make_ui` from
`_archive.ui_legacy` (line 39). Do not switch it until the facade, callbacks,
API-key bootstrap, confirmations and shutdown semantics are implemented.

## Reference mapping

* `core/anim.ts`: spring response 0.5 s, damping fraction 0.72, 1/240 s
  integration substeps, settle thresholds 0.01 and 0.05; `island/island.ts`
  caps the frame delta at 0.05 s. Ported in `island/physics.py`. The reference
  shrink uses cubic-bezier (0.45, 0, 0.2, 1), 340 ms; the curve is ported,
  but not yet connected to the widget.
* `core/layout.ts`: compact 288 × 32, expanded 640 × 160, with 14/22 radii;
  `core/closed.ts`: closed minimum 80 × 24. The prototype uses the attached Hey
  Nixi screenshot's larger visible resting notch rather than these sizes.
* `island/fsm.ts`: hidden/petit/home/coucou, 15 s home collapse, 60 s petit
  hide, 0.5 s hover open, 0.6 s hover close and greeting completion, 10 s
  hovered greeting. `island/fsm.py` ports the model. `VoiceHUD` uses it for
  hover open and close, with hover-open enabled to match the requested behavior;
  Coucou defaults that preference to off. Not all FSM inputs are wired yet.
* `mochi/desktop-logic.ts`: drag threshold 3 px and gaze tanh(dx/260, dy/200).
  The prototype uses these two numbers, but not the full mascot engine or
  desktop lifecycle. `mascot/eyes.py` draws a simplified pill rig.
* `win32_tauri_ref/src/platform/windows.rs`: `island/win32_overlay.py` isolates
  native style and hit-test calls. Windows behavior, mixed-DPI coordinates and
  actual click-through have not been validated; fullscreen and focus handling
  remain unimplemented.

## Backend contract and current bridge

`bridge/protocol.py` defines a small native state and log model;
`bridge/event_bus.py` contains a bounded, lock-protected producer queue; and
`bridge/bridge.py` contains the Qt-thread dispatcher and model. The dispatcher
always posts queued Qt signals, batches delivery on its owning thread and bounds
its backlog. `bridge/controller.py` connects state, amplitude and logs to the
prototype HUD. The demo simulates amplitude; it is not attached to FRIDAY.

The legacy facade additionally exposes `root.after`, `mainloop`,
visibility/shutdown, `write_log`, state/connection/persona, mute/silent
toggles, speech and live transcript methods, vision/arcade callbacks,
`on_text_command` and API-key bootstrap. `_archive/ui_legacy/{protocol,
event_bus,bridge,controller}.py` are reference contracts only. Their Electron
transport and rendering must not be imported into the native frontend. The
main entry point depends on those APIs and its startup worker and tray invoke
them cross-thread. A complete Qt facade must retain startup log buffering,
approval and API-key handling before replacing the import.

## Current limitations

The demo and generated snapshots are visual prototypes only. Panel content is
placeholder text except for display of logs sent through the standalone bridge;
there are no backend calls. Command entry, approval flows, tray, settings
persistence and `main.py` migration are absent. Eyes do not implement Coucou's
full animation; drag has a simple snap zone but no persistence or inertia. The
widget repaints continuously and does not meet the idle CPU target. Window
activation, fullscreen behavior and input hit-testing require real Windows
verification.

The sandbox preview run uses Qt's offscreen software rasterizer with temporary
stub GL/EGL/DBus libraries, because the container lacks those system libraries.
The stubs are outside the repository and are not a production dependency. The
rendered PNGs validate the QPainter prototype only; they do not validate native
Windows composition or show functional panels.
