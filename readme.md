<p align="center">
  <img src="assets/banner.png" alt="Friday Banner" width="100%">
</p>

<p align="center">
  <b>Real-Time, Voice-Driven Multimodal AI Companion for Windows</b><br>
  <i>Sub-second speech-to-speech interaction, desktop vision, autonomous tooling, and hardened local automation.</i>
</p>

<p align="center">
  <a href="https://www.python.org/"><img src="https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13%20%7C%203.14-blue.svg" alt="Python Version"></a>
  <a href="https://opensource.org/licenses/MIT"><img src="https://img.shields.io/badge/License-MIT-yellow.svg" alt="License: MIT"></a>
  <a href="https://www.microsoft.com/windows"><img src="https://img.shields.io/badge/platform-Windows%2010%20%7C%2011-0078D6.svg" alt="Windows"></a>
  <a href="https://www.apple.com/macos/"><img src="https://img.shields.io/badge/platform-macOS-000000.svg" alt="macOS"></a>
  <a href="https://ai.google.dev/"><img src="https://img.shields.io/badge/AI-Google%20Gemini%20Live-8E75C2.svg" alt="Gemini Live"></a>
</p>

<p align="center">
  <a href="#-project-overview">Overview</a> •
  <a href="#-system-architecture">Architecture</a> •
  <a href="#-features--tooling">Features & Tools</a> •
  <a href="#-quick-start">Quick Start</a> •
  <a href="#-security--safety-model">Security Model</a> •
  <a href="#-configuration">Configuration</a> •
  <a href="#-testing--verification">Testing</a> •
  <a href="#-roadmap">Roadmap</a> •
  <a href="#-contributing">Contributing</a>
</p>

---

# FRIDAY

FRIDAY is a real-time, voice-driven desktop AI companion built around Gemini Live, a native PySide6/Qt6 Dynamic Island UI, and a safety-gated automation toolkit. It runs on **Windows, macOS, and Linux**, with platform-native overlay behavior on Windows and macOS.

## Highlights

- **Real-time Gemini Live assistant** — low-latency voice conversation, live transcription, tool calling, and reconnect/key-rotation handling. Model IDs are defined in [`config/models.py`](config/models.py): `MODEL_FLASH = "gemini-3.8-flash"`, `MODEL_FLASH_LITE = "gemini-3.5-flash-lite"`, `MODEL_LIVE = "gemini-3.1-flash-live-preview"`.
- **Native cross-platform Dynamic Island UI (PySide6 / Qt6)** — frameless top-docked island, liquid-glass search/command pill, settings hub, chat/action-confirmation cards, vision preview, and integrated panels.
  - **Windows 11**: frameless topmost overlay with Win32 hit-testing and event-driven Z-order guard (`ui/island/win32_overlay.py`), automatic Administrator elevation on launch (opt out with `--no-admin` or `FRIDAY_NO_ADMIN=1`), and native tray/HUD lifecycle.
  - **macOS (Apple Silicon & Intel)**: zero-duplication **`ui_mac/` dynamic Cocoa bridge** (PEP 562 live proxy over `ui/`) that auto-syncs `ui/` updates without duplicating widgets/themes. It applies native NSPanel-style overlay behavior for full-screen Spaces, avoids focus stealing, aligns with MacBook notches/menu bar insets, maps fonts to SF Pro/SF Mono, and provides native `Cmd+V` dictation paste via PyObjC.
- **Fast, dynamic app launcher (`actions/open_app.py`)** — persistent atomic JSON app index cache (`data/app_index_cache.json`) with stale-while-revalidate background refresh, cross-source deduplication (Start Menu, App Paths, Uninstall registry, UWP `Get-StartApps`), UAC-safe UWP AUMID launching, and smart multi-app disambiguation.
- **Safety & human confirmation (`safety.py`)** — centralized `LOW` / `MEDIUM` / `HIGH` risk classification, single-use confirmation tokens bound to exact tool calls, UI confirmation cards and spoken approval, shell-bypass protection, and sensitive-value redaction in logs/memory.
- **Automation & assistant capabilities** — screen/vision analysis, web search, weather & daily briefing, desktop/window/media control, reminders, YouTube, PDF/document Q&A, Office generation, clipboard, smart home, task queue/agent planner, persistent memory, wake word, and gesture/vision features.

## Architecture

```text
FRIDAY/
├── main.py                  # Entry point, CLI flags, Gemini Live session, tool wiring
├── api_keys.py              # API key loading, rotation, quota failover
├── safety.py                # Risk classification, confirmation tokens, redaction
├── google_oauth.py          # Google Desktop OAuth PKCE callback/token management
├── logger.py                # Logging setup
├── actions/                 # 39 automation/action modules
├── agent/                   # Planner, executor, task queue, resilience, loop guard
├── config/                  # Model constants, audio/runtime config, example configs
├── core/                    # Prompt/system instructions
├── memory/                  # Curated local memory and persistence
├── ui/                      # Native PySide6/Qt6 UI (source of truth)
├── ui_mac/                  # macOS Cocoa bridge/proxy over ui/ (zero duplication)
├── vision/                  # Camera, OCR, face/privacy, detection, arcade, pipelines
├── tests/                   # Offline-focused pytest regression suites
├── pyproject.toml           # Project metadata and dependency declarations
├── requirements.txt         # Cross-platform pip requirements (PEP 508 markers)
└── readme.md                # This file
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

Some modules expose multiple tools (for example, `monitor`, `pdf_reader`, and `office_builder`). `vision/` contains the underlying computer-vision engines and pipelines used by vision-related actions.

## Requirements

- **Python 3.11 – 3.13** (tested on Python 3.13)
- A Gemini API key from [Google AI Studio](https://aistudio.google.com/apikey)
- OS-specific runtime permissions/libraries for audio, camera, screen capture, and desktop automation (see [OS Permissions](#os-permissions-guide))

## Quick Start & Installation

### 1. Clone and create a virtual environment

**Windows (PowerShell):**

```powershell
git clone https://github.com/mayankkashyap05/FRIDAY.git
cd FRIDAY
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python setup.py
```

**macOS / Linux (Bash/Zsh):**

```bash
git clone https://github.com/mayankkashyap05/FRIDAY.git
cd FRIDAY
python3.13 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python setup.py
```

`python setup.py` installs dependencies from `requirements.txt`, attempts to install Playwright Chromium, and creates default config files from `config/*.example.json` when missing.

> If you prefer manual installation, you can run `pip install -r requirements.txt` instead. `requirements.txt` uses PEP 508 environment markers, so the same file installs Windows-only and macOS-only dependencies where appropriate.

### 2. Configure Gemini API keys

Create `config/api_keys.json` (you can copy from `config/api_keys.example.json`) and add your key(s). **Never commit real API keys.**

**Single key:**

```json
{
  "gemini_api_key": "YOUR_GEMINI_API_KEY"
}
```

**Multiple keys (rotation/failover):**

```json
{
  "gemini_api_key": "YOUR_GEMINI_API_KEY",
  "gemini_api_keys": [
    "YOUR_GEMINI_API_KEY",
    "OPTIONAL_SECOND_KEY"
  ]
}
```

`api_keys.py` loads either shape and can rotate keys on quota/rate-limit failures.

### 3. Optional: Google Workspace (Gmail, Calendar, Drive)

Create a Google Cloud OAuth client (Desktop app), enable the APIs/scopes you need, and configure credentials in either supported format:

- `config/google_oauth.json` containing the downloaded `{"installed": {"client_id": "...", "client_secret": "..."}}` object; or
- `google_client_id` and optional `google_client_secret` in `config/api_keys.json`.

Use the FRIDAY Google authorization action to open the browser. Sign-in uses an ephemeral loopback callback on `127.0.0.1` with PKCE (S256); tokens are saved atomically to `config/google_token.json`.

## Running FRIDAY

```bash
python main.py
```

`main.py` auto-selects the native UI: `ui_mac` on macOS (`darwin`) with fallback to `ui`, and `ui` on Windows/Linux.

### CLI flags & environment variables

| Flag / Env | Effect |
| --- | --- |
| `--headless` | Run without showing the native island or tray. |
| `--minimized` | Start with the HUD hidden (ignored if `--headless`). |
| `--no-admin` | Skip automatic Windows Administrator elevation. |
| `FRIDAY_NO_ADMIN=1` | Same as `--no-admin`; disables automatic UAC elevation on Windows. |

> `--elevated` is an internal Windows relaunch flag used by FRIDAY after UAC elevation.

## OS Permissions Guide

### Windows

- **UAC / Administrator elevation**: FRIDAY automatically attempts to relaunch with Administrator privileges so the Dynamic Island overlay can stay above elevated apps (for example, VS Code running as admin). Use `--no-admin` or set `FRIDAY_NO_ADMIN=1` to opt out.
- **Microphone / Camera / Screen access**: allow access when prompted by Windows or by the specific capture/audio device stack you use.

### macOS

Grant permissions in **System Settings > Privacy & Security** (no `sudo` required):

- **Accessibility** — required for `Cmd+V` dictation paste and some desktop control/hotkey features.
- **Microphone** — required for voice input.
- **Camera** — required for vision/camera features.
- **Screen Recording** — required for screen capture/vision analysis.

If Accessibility is missing, macOS paste injection will fail honestly; grant permission and retry.

## Testing & Quality Checks

Run the offline test suite from the active environment:

```bash
python -m pytest -q
```

Run lint/static checks:

```bash
python -m pyflakes .
```

## Troubleshooting

- **Gemini 429 / quota errors**: free-tier quotas are limited. Add multiple keys from separate projects in `config/api_keys.json` (`gemini_api_keys`) so `api_keys.py` can rotate/fail over.
- **macOS paste/hotkeys not working**: ensure FRIDAY has **Accessibility** permission in System Settings > Privacy & Security > Accessibility.
- **Windows overlay not staying above elevated apps**: run FRIDAY normally and accept UAC, or launch an elevated terminal. If you intentionally don't want elevation, use `--no-admin` / `FRIDAY_NO_ADMIN=1`.
- **Missing optional feature dependency**: install the relevant optional package from the commented section in `requirements.txt` (for example Playwright, OCR, wake-word, or vision ML packages).

## Safety Model

Tool risk is centralized in [`safety.py`](safety.py):

- **LOW**: read-only/harmless actions.
- **MEDIUM**: state-changing but generally reversible/low impact.
- **HIGH**: destructive, irreversible, externally sensitive, or arbitrary-execution actions.

Calls requiring approval receive a confirmation token bound to the exact tool and arguments. Tokens are single-use and expire; replaying a token against a different call does not authorize it. UI confirmation and spoken approval are handled separately from model-generated requests.

## Project Status

FRIDAY is an active desktop project. Hardware-specific audio, camera, window-management, and overlay behavior should be validated on the target machine in addition to the offline tests. See [`ui/ARCHITECTURE.md`](ui/ARCHITECTURE.md) for UI boundaries and platform notes.
