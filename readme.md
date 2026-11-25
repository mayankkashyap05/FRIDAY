# FRIDAY

FRIDAY is a desktop AI companion for Windows, built around real-time Gemini Live voice interaction, a native Qt interface, and a safety-gated automation toolkit. The source tree is the authoritative reference for supported actions, optional integrations, and platform-specific behavior.

## Highlights

- **Native PySide6 Dynamic Island HUD** — an anchored top-notch mascot, liquid-glass search/command bar, settings hub, chat and action-confirmation cards, vision preview, and Vision Arcade. The tray and Qt bridge provide the desktop lifecycle and cross-thread event delivery.
- **Gemini Live API** — real-time audio conversation, function calling, and reconnect handling. `config/models.py` exports `MODEL_FLASH`, `MODEL_FLASH_LITE`, `MODEL_LIVE`, and compatibility aliases. `api_keys.py` supports a single key or a rotating multi-key pool; `generate_with_retry()` and the Live client key rotation recover from quota exhaustion.
- **39 action modules** — OS and system controls, file and document workflows, web/media tools, Google Workspace, developer helpers, vision and wake-word features, and smart-home/messaging actions.
- **Autonomous multi-step agent** — `agent/planner.py` plans steps, `agent/executor.py` executes bounded work, `agent/task_queue.py` schedules background requests, and `agent/loop_guard.py` limits repeated calls.
- **Security-first execution** — `safety.py` classifies actions in LOW/MEDIUM/HIGH tiers, issues exact-call single-use confirmation tokens, and sanitizes sensitive values. High-impact execution also requires human approval; optional `face_verify` adds local identity verification. Browser and download paths include SSRF defenses, archive extraction protects against zip-slip, and the normal desktop dispatcher uses explicit safe handlers rather than executing generated code.

## Architecture

```text
main.py                  Gemini Live session, tools, audio loops and UI wiring
api_keys.py              API key loading, rotation and quota failover
config/models.py         Canonical model constants
ui/                      PySide6 HUD, panels, bridge, themes and tray
agent/                   Planner, executor, task queue, resilience and loop guard
actions/                 39 user-facing automation modules
vision/                  Camera, OCR, face privacy, detection, arcade and pipelines
memory/                  Curated local memory and persistence
safety.py                Risk classification, consent tokens and sanitization
google_oauth.py           Google Desktop OAuth PKCE callback and token management
tests/                   Offline-focused pytest regression suites
```

### Action suite (39 modules)

| Category | Modules |
| --- | --- |
| **OS & System** | `autostart`, `computer_control`, `computer_settings`, `desktop`, `guardian`, `media_control`, `notifications`, `open_app`, `reminder`, `system_status`, `system_tray`, `task_manager`, `clipboard`, `cmd_control` |
| **Files & Documents** | `document_qa`, `file_controller`, `office_builder`, `pdf_reader` |
| **Web & Media** | `browser_control`, `flight_finder`, `game_updater`, `translate`, `weather_report`, `web_search`, `youtube_video` |
| **Google Workspace** | `calendar`, `drive`, `gmail`, `daily_briefing`, `monitor` |
| **Developer Tools** | `code_helper`, `dev_agent` |
| **Vision & Wake Word** | `friday_wake`, `screen_processor`, `vision_click`, `vision_gesture`, `wake_word` |
| **Smart Home & Comms** | `send_message`, `smart_home` |

Some action modules expose more than one tool. For example, `monitor` includes system and email monitoring, `pdf_reader` provides PDF reading and Q&A, and `office_builder` creates presentations and spreadsheets. `vision/` contains the underlying computer-vision engines and the gesture-driven arcade state.

## Requirements and setup

The project metadata supports Python 3.11 and newer; **Python 3.13 is the recommended setup**. On Windows, use PowerShell:

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m playwright install chromium
```

On Linux/macOS, create and activate the environment with `python3.13 -m venv .venv` and `source .venv/bin/activate`, then use the same pip commands. Native audio, Qt, camera, and desktop-control features may require OS libraries and permissions; Windows is the primary desktop target.

### Gemini API keys

Copy `config/api_keys.example.json` to `config/api_keys.json` and supply either a single key or a list of keys:

```json
{
  "gemini_api_key": "YOUR_GEMINI_API_KEY",
  "gemini_api_keys": ["YOUR_GEMINI_API_KEY", "OPTIONAL_SECOND_KEY"]
}
```

The key manager accepts either configuration shape and rotates keys on quota failures. Keep real keys and OAuth credentials out of version control.

### Google Workspace (Gmail, Calendar and Drive)

Create a Google Cloud OAuth client with **Desktop app** as its client type, enable the APIs/scopes required for the Workspace features you use, and configure credentials in either supported format:

- `config/google_oauth.json` containing the downloaded `{"installed": {"client_id": "...", "client_secret": "..."}}` object; or
- `google_client_id` and optional `google_client_secret` in `config/api_keys.json`.

Use the FRIDAY Google authorization action to open the browser. Sign-in uses an ephemeral loopback callback on `127.0.0.1` and PKCE with S256; tokens are saved atomically to `config/google_token.json`. Existing `auth_start()` / `auth_poll()` device-flow callers remain available for compatibility. The application requests Gmail, Calendar, Drive file, and account-email scopes.

## Run

```powershell
python main.py
```

For a development run without displaying the HUD/tray, use `python main.py --headless`. The autostart action supports a normal per-user Windows Run entry or a delayed Task Scheduler logon trigger; it also uses LaunchAgents on macOS and XDG autostart files on Linux.

## Safety model

Tool risk is centralized in `safety.py`:

- **LOW**: read-only or harmless actions.
- **MEDIUM**: state changes that are generally reversible.
- **HIGH**: destructive, externally consequential, or otherwise sensitive operations.

Calls that require approval receive a token bound to the exact tool and arguments. Tokens are single-use and expire; replaying one against another call does not authorize it. The UI confirmation card and verbal approval path are kept separate from the model's request. Browser navigation and remote downloads apply private-network/SSRF checks, and file extraction rejects path traversal. The desktop action dispatcher routes to explicit handlers; it does not evaluate a model-generated instruction string as Python.

## Testing and verification

Run the repository suite offline from the active environment:

```powershell
pytest -v
```

Focused regression coverage for Live tool schemas, autostart, OAuth, core integrations, UI wiring, and Live session behavior:

```powershell
pytest tests/test_contracts_and_configs.py tests/test_autostart_deep.py tests/test_google_oauth_deep.py tests/test_core_and_memory_deep.py tests/test_main_ui_wiring.py tests/test_live_session_fixes.py -v
```

Post-change focused offline verification: **192 passed, 1 skipped** across the focused core/Live set above plus `tests/test_actions_system_and_files.py`, `tests/test_safety.py`, `tests/test_calendar_deep.py`, `tests/test_drive_and_gmail_deep.py`, `tests/test_daily_briefing_and_desktop_deep.py`, and `tests/test_guardian_and_monitor_deep.py`. The UI wiring test is skipped because PySide6 is unavailable in this sandbox. The complete repository suite was attempted, but collection is blocked here by missing PySide6/Qt platform dependencies; no full-suite total is claimed. The OAuth callback tests mock external services and exercise the callback against a real local loopback socket; no Google credentials or external account access are required.

## Project status

FRIDAY is an active desktop project. Hardware-specific audio, camera, window-management, and overlay behavior should be validated on the target machine in addition to the offline tests. See `ui/ARCHITECTURE.md` for interface boundaries and known platform limitations.
