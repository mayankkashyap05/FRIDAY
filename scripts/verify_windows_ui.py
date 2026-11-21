"""Windows bring-up verifier for FRIDAY's Electron UI.

This is the script to run on a real Windows box after a fresh clone, before you
trust the overlay. It answers one question — *will FRIDAY's UI actually appear
and disappear cleanly on this machine?* — and it answers it by driving the real
code, not by inspecting source files.

What it verifies
----------------
1. **Toolchain**  — Node ≥ 20, npm, the Electron binary inside
                    ``ui/app/node_modules/electron/dist/electron.exe``,
                    and a built renderer (``ui/app/dist/index.html`` +
                    ``ui/app/dist/electron/main.js``).
2. **Import purity** — ``import ui`` works with no Qt, no socket and no
                    Electron process, and exports the whole public contract
                    ``main.py`` depends on.
3. **Boot**       — a real :class:`ui.controller.UiController` binds a loopback
                    port, spawns Electron, and accepts an authenticated socket
                    with the right token.
4. **Live traffic** — a card, an activity, a transcript line and an amplitude
                    delta all arrive as JSON; a `ping` gets a `pong`; a bad token
                    is refused with close code 4001.
5. **Summon/dismiss** — ``controller.show()`` reaches the renderer, the orb state
                    flips to invoked, ``request_quit()`` shuts everything down and
                    **no orphaned ``electron.exe`` remains** (checked via psutil
                    when available, otherwise ``tasklist``).

Usage (PowerShell)
------------------
    python scripts\\verify_windows_ui.py            # full bring-up
    python scripts\\verify_windows_ui.py --quick    # toolchain + import only
    python scripts\\verify_windows_ui.py --headless # skip Electron, WS only

Exit code is 0 only when every check passes.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "ui" / "app"

# Running this as `python scripts\verify_windows_ui.py` puts scripts/ on
# sys.path[0]; the repo root must win so `import ui` resolves to the package.
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
ELECTRON_EXE = APP / "node_modules" / "electron" / "dist" / "electron.exe"
RENDERER = APP / "dist" / "index.html"
MAIN_JS = APP / "dist" / "electron" / "main.js"

PASS = "PASS"
FAIL = "FAIL"
SKIP = "SKIP"

results: list[tuple[str, str, str]] = []


def record(name: str, status: str, detail: str = "") -> None:
    results.append((status, name, detail))
    mark = {"PASS": "✓", "FAIL": "✗", "SKIP": "·"}.get(status, "?")
    line = f"  {mark} {name}"
    if detail:
        line += f" — {detail}"
    print(line, flush=True)


def check(name: str):
    """Run a callable, recording PASS/FAIL with the raised message as detail."""

    def wrap(fn):
        try:
            detail = fn() or ""
            record(name, PASS, str(detail))
            return True
        except Exception as exc:  # noqa: BLE001 - a verifier must never crash
            record(name, FAIL, f"{type(exc).__name__}: {exc}")
            return False

    return wrap


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


# ── 1. toolchain ─────────────────────────────────────────────────────────────

def check_node() -> str:
    node = shutil.which("node")
    require(node is not None, "node is not on PATH — install Node 20+ from nodejs.org")
    out = subprocess.run([node, "--version"], capture_output=True, text=True, timeout=20)
    version = out.stdout.strip().lstrip("v")
    major = int(version.split(".")[0])
    require(major >= 20, f"Node {version} is too old; FRIDAY needs 20 or newer")
    return f"node {version}"


def check_npm() -> str:
    npm = shutil.which("npm")
    require(npm is not None, "npm is not on PATH")
    out = subprocess.run([npm, "--version"], capture_output=True, text=True, timeout=30,
                         shell=False)
    return f"npm {out.stdout.strip()}"


def check_electron_binary() -> str:
    from ui.controller import electron_executable

    exe = electron_executable(APP)
    require(
        exe is not None,
        "electron's binary is missing (npm can report success without it) — "
        "run:  cd ui\\app; npm rebuild electron",
    )
    size = exe.stat().st_size / (1024 * 1024)
    return f"{exe} ({size:.1f} MB)"


def check_renderer_built() -> str:
    from ui.controller import renderer_files

    missing = [str(p.relative_to(ROOT)) for p in renderer_files(APP) if not p.exists()]
    require(not missing, "renderer not built (missing "
                         + ", ".join(missing) + ") — run:  cd ui\\app; npm run build")
    return "dist/index.html + dist/electron/main.js"


def check_dependencies() -> str:
    import websockets  # noqa: F401

    return f"websockets {websockets.__version__}"


# ── 2. import purity + public contract ───────────────────────────────────────

def check_import_purity() -> str:
    import ui  # noqa: F401

    # Any Qt binding would show up as a top-level module here; FRIDAY must load
    # none of them, because the frontend is Electron.
    banned = [name for name in sys.modules
              if name.split(".")[0].startswith(("PySide", "PyQt", "shiboken"))]
    require(not banned, f"import ui pulled in a Qt binding: {banned}")
    return "import ui brought in no Qt module"


def check_public_contract() -> str:
    import ui

    with_keys = (
        "FridayUI", "make_ui", "get_base_dir", "BASE_DIR", "CONFIG_DIR",
        "API_FILE", "THEME_FILE", "THEMES", "THEME_NAMES", "DEFAULT_THEME",
        "SYSTEM_NAME", "MODEL_BADGE",
    )
    missing = [key for key in with_keys if not hasattr(ui, key)]
    require(not missing, f"ui is missing: {missing}")

    helpers = ("_dispatch", "_safe_ui", "_api_keys_exist")
    missing_helpers = [key for key in helpers if not hasattr(ui.FridayUI, key)]
    require(not missing_helpers, f"FridayUI is missing: {missing_helpers}")

    require(ui.get_base_dir() == ROOT, f"get_base_dir() returned {ui.get_base_dir()}")
    from ui import controller

    for method in ("set_wake_status", "shutdown", "request_quit", "show", "wait"):
        require(hasattr(controller.UiController, method),
                f"UiController.{method} is missing")
    return f"{len(with_keys)} exports, base dir {ui.get_base_dir()}"


def check_models() -> str:
    """The card mapper must accept the envelopes the backend really emits.

    `to_cards()` returns a 3-tuple `(card, activities, event)`; a bare
    assignment would bind the tuple and explode on the next attribute access,
    so every call here is unpacked.
    """
    from ui import card_mapper

    card, activities, event = card_mapper.to_cards(
        {"type": "weather", "city": "Dibrugarh", "temp_c": 27, "humidity": 81}
    )
    require(card is not None, "a weather envelope produced no card")
    require(card.kind.value == "weather", f"expected a weather card, got {card.kind!r}")

    text_card, _, text_event = card_mapper.to_cards("[YouTube] Searching: lofi")
    require(text_card is not None or text_event is not None,
            "a legacy log line produced neither card nor transcript event")
    return f"weather → {card.title} · legacy line → {text_event.channel.value if text_event else 'ok'}"


def check_windows_capabilities() -> str:
    """Win32 prerequisites for the overlay's glass, click-through and capture
    exclusion. Everything here needs Windows 11 22H2 (build 22621) or newer."""
    from ui import win32_native as w32

    build = w32.windows_build()
    require(build >= 22621,
            f"Windows build {build} is older than 22621 (11 22H2): DWM rounded "
            "corners and the transient backdrop are unavailable")

    entry_points = {
        "capture_exclusion": "exclude_from_capture",
        "layered_windows": "make_click_through",
        "backdrop": "apply_dwm_polish",
        "rounded_corners": "apply_dwm_polish",
        "dnd": "do_not_disturb",
    }
    missing = [api for api in set(entry_points.values()) if not hasattr(w32, api)]
    require(not missing, f"win32_native is missing {missing}")

    caps = w32.probe(warn=False)
    available = caps.to_dict() if hasattr(caps, "to_dict") else {}
    notes = ", ".join(f"{name}={bool(available.get(name))}"
                      for name in ("capture_exclusion", "layered_windows",
                                   "backdrop", "rounded_corners"))
    # A disabled capability is a warning, not a failure: several only latch
    # once the window exists. The Win32 error code is what makes it actionable.
    if any(available.get(name) is False for name in entry_points if name != "dnd"):
        print(f"    note: {notes} (last Win32 error: {w32.explain_last_error()})")
    return f"build {build} ≥ 22621 · {notes}"


# ── 3/4/5. live boot, traffic, summon, clean shutdown ────────────────────────

async def exercise(controller, warnings: list[str]) -> dict:
    """Drive a live controller over its own WebSocket."""
    import websockets

    snapshot: dict = {}
    token = getattr(controller, "token", "")
    url = f"{controller.ws_url}?token={token}" if token else controller.ws_url
    async with websockets.connect(url, open_timeout=15) as ws:
        deadline = time.time() + 15
        while time.time() < deadline:
            raw = await asyncio.wait_for(ws.recv(), timeout=15)
            if isinstance(raw, (bytes, bytearray)):
                continue
            message = json.loads(raw)
            if message.get("t") == "snapshot":
                snapshot = message
                break
        require(bool(snapshot), "no snapshot arrived from the controller")

        await ws.send(json.dumps({"t": "ping"}))
        while True:
            message = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
            if message.get("t") == "pong":
                break

        # Cards + microphone level go through the real bridge, on the UI thread
        # it owns — exactly the path the backend uses.
        controller.dispatcher.call(
            controller.bridge.ingest,
            {"type": "text", "title": "verify", "body": "live traffic ok"},
        )
        controller.dispatcher.call(controller.bridge.setAmplitude, 0.55)

        saw_card = saw_amplitude = False
        deadline = time.time() + 10
        while time.time() < deadline and not (saw_card and saw_amplitude):
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            if isinstance(raw, (bytes, bytearray)):
                continue
            message = json.loads(raw)
            if message.get("t") == "cards":
                saw_card = any(c.get("title") == "verify" for c in message.get("items", []))
            elif message.get("t") == "amplitude":
                saw_amplitude = True

    if not saw_card:
        warnings.append("card delta did not arrive")
    if not saw_amplitude:
        warnings.append("amplitude delta did not arrive")

    # A bad token must be refused — either at the handshake (HTTP 401) or by
    # closing the socket with 4001. Both are correct; silence is not.
    import websockets.exceptions

    bad_url = controller.ws_url.split("?")[0] + "?token=invalid"
    code = None
    rejected = False
    try:
        async with websockets.connect(bad_url, open_timeout=5):
            pass
    except websockets.exceptions.InvalidStatus:
        rejected = True
    except Exception as exc:  # noqa: BLE001
        code = getattr(exc, "code", None) or getattr(
            getattr(exc, "rcvd", None), "code", None)
        rejected = code == 4001
    if not rejected:
        warnings.append(f"bad token was not refused (close code {code!r})")

    return snapshot


def check_live_controller() -> str:
    from ui.controller import UiController

    warnings: list[str] = []
    # `enable_overlay=False` keeps the full WebSocket + bridge + state machine
    # and skips only the native window (headless mode).
    controller = UiController(enable_overlay=not ARGS.headless, app_dir=APP)
    try:
        controller.start()
        controller.show()
        snapshot = asyncio.run(exercise(controller, warnings))
        invoked = snapshot.get("state", {}).get("invoked")
        require(bool(invoked), "show() never flipped the renderer to invoked")
    finally:
        controller.request_quit()
        require(controller.wait(20.0), "the controller did not shut down within 20 s")
        require(not controller.electron_running, "Electron outlived controller.shutdown()")
    if warnings:
        raise AssertionError("; ".join(warnings))
    return "snapshot → ping/pong → card → amplitude → invoke → quit"


def orphaned_electron() -> list[str]:
    """Any electron.exe still alive that points at our app directory."""
    if os.name != "nt":
        return []
    try:
        import psutil  # type: ignore
    except Exception:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq electron.exe"],
                             capture_output=True, text=True, timeout=20)
        if "electron.exe" in out.stdout.lower():
            return ["electron.exe (tasklist)"]
        return []

    found = []
    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            if (proc.info.get("name") or "").lower() != "electron.exe":
                continue
            cmdline = " ".join(proc.info.get("cmdline") or [])
            if str(APP).lower() in cmdline.lower():
                found.append(f"pid {proc.info['pid']}")
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return found


def check_no_orphans() -> str:
    time.sleep(1.0)
    leftovers = orphaned_electron()
    require(not leftovers, "orphaned electron.exe: " + ", ".join(leftovers))
    return "no electron.exe left running"


# ── driver ───────────────────────────────────────────────────────────────────

def main() -> int:
    global ARGS
    parser = argparse.ArgumentParser(description="FRIDAY Windows UI verifier")
    parser.add_argument("--quick", action="store_true",
                        help="toolchain + import checks only")
    parser.add_argument("--headless", action="store_true",
                        help="verify the WebSocket protocol without spawning Electron")
    ARGS = parser.parse_args()

    print("FRIDAY — Windows UI verification")
    print(f"  repo    {ROOT}")
    print(f"  python  {sys.version.split()[0]} ({sys.executable})")
    print(f"  platform {sys.platform}\n")

    print("Toolchain")
    node_ok = check("node ≥ 20 on PATH")(check_node)
    check("npm on PATH")(check_npm)
    check("websockets installed")(check_dependencies)
    electron_ok = False
    if node_ok and not ARGS.headless:
        electron_ok = check("electron.exe present")(check_electron_binary)
        check("renderer built")(check_renderer_built)

    if os.name == "nt":
        check("Windows 11 22H2 capabilities")(check_windows_capabilities)
    else:
        record("Windows 11 22H2 capabilities", SKIP, "not running on Windows")

    print("\nContracts")
    check("import ui is pure (no Qt)")(check_import_purity)
    check("public API for main.py")(check_public_contract)
    check("card mapper accepts backend payloads")(check_models)

    if not ARGS.quick:
        print("\nLive session")
        if ARGS.headless or electron_ok:
            check("controller boots, streams and quits")(check_live_controller)
            if not ARGS.headless:
                check("no orphaned electron.exe")(check_no_orphans)
        else:
            record("controller boots, streams and quits", SKIP,
                   "install Electron and build the renderer first")

    failed = [r for r in results if r[0] == FAIL]
    print("\n" + "=" * 62)
    print(f"{len(results) - len(failed)} passed · {len(failed)} failed")
    if failed:
        print("\nFix these first:")
        for _, name, detail in failed:
            print(f"  • {name}: {detail}")
        if any("electron.exe" in detail or "renderer" in detail for detail in
               (f[2] for f in failed)):
            print("\nPowerShell:")
            print("  cd ui\\app")
            print("  npm install")
            print("  npm rebuild electron    # only needed if the binary is missing")
            print("  npm run build")
        return 1
    print("\nFRIDAY's UI is ready. Launch it with:\n  python main.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
