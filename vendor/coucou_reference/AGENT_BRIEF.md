# FRIDAY Island UI — AI Agent Porting Brief (Hey Nixi 1:1 via Coucou Reference)

## Directory Roles
- `ui/` — **ACTIVE TARGET**: Build FRIDAY's native Windows PySide6 island UI here.
- `vendor/coucou_reference/windows_app/` — Working TypeScript/Canvas 2D reference for spring physics (`anim.ts`), layout (`layout.ts`), FSM (`fsm.ts`), desktop face-out (`desktop/`), dictation pill (`pill/`), and unit tests (`tests/`).
- `vendor/coucou_reference/win32_tauri_ref/` — Exact Win32 overlay/click-through/DPI/fullscreen-hide/focus-safe paste calls (`src/platform/windows.rs`, `src/flow/os_windows.rs`).
- `vendor/coucou_reference/swift_math_ref/` — Original CoucouKit geometry, eye tracking, and hover/auto-close timing constants.
- `vendor/coucou_reference/_REPLACE_BEFORE_RELEASE/` — Temporary visual prototypes/captures for local reference only. **DO NOT ship these assets in public releases.**
- `vendor/heynixi_spec_ref/` — Binary-extracted Hey Nixi QSS tokens and UI structure notes (note: verify against Coucou math where constants were tagged UNVERIFIED).

## Visual & Behavioral Target (Hey Nixi 1:1)
1. Single frameless, always-on-top, per-pixel translucent PySide6 window (`Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool`, `WA_TranslucentBackground`).
2. Top-center docked black notch with **outward-flaring concave top shoulders** flush with `y = 0`, rounded bottom corners, and two centered pill-shaped white eyes (minimalist capsule aesthetic — NOT Mochi's squircle body).
3. Seamless morphing between:
   - `RESTING` (compact black notch + eyes)
   - `HOVER` (expands to show Command / Dictate / Write primary tiles + secondary tabs)
   - `SLIM_LISTENING` / `DICTATION` / `WRITE` (breathing halo, audio waveform, working dots, tick/undo)
   - `ISLAND_OPEN` (Chat, Media, Tasks/Reminders, Vision, File Drop Shelf, Settings)
   - `FACE_OUT` (drag-out floating full-capsule pill on desktop with magnetic snap-back)
4. Dynamic click-through on transparent pixels and zero focus-stealing from active Windows apps (`WS_EX_NOACTIVATE` / `WS_EX_TOOLWINDOW` pattern from `win32_tauri_ref/src/platform/windows.rs`).

## FRIDAY Backend Wiring Contract
Wire `ui/bridge/` directly into FRIDAY's existing modules (respecting `tests/test_ui_contract.py` and `tests/test_ui_thread_safety.py`):
- **Wake & Voice**: `actions/wake_word.py`, `actions/friday_wake.py`, `main.py`
- **Agent / Chat / Tasks**: `agent/planner.py`, `agent/executor.py`, `agent/task_queue.py`, `actions/task_manager.py`, `actions/reminder.py`
- **Media Bar**: `actions/media_control.py`
- **Vision / Screen**: `actions/screen_processor.py`, `vision/vision_pipeline.py`
- **File Drop Shelf**: `actions/document_qa.py`, `actions/pdf_reader.py`, `actions/file_controller.py`
- **System / Guardian / Tray**: `actions/guardian.py`, `actions/system_status.py`, `actions/system_tray.py`