"""One-shot setup: Python deps, Playwright browsers, config files, and the UI.

FRIDAY's frontend is an Electron + React renderer in ``ui/app``. There is no
fallback UI: if it is not built, the app refuses to start (see
``ui.controller.missing_ui_help``). This script builds it for you.
"""
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
APP = ROOT / "ui" / "app"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def create_default_configs() -> None:
    """Copy *.example.json -> *.json so the app runs without manual config editing."""
    cfg_dir = ROOT / "config"
    cfg_dir.mkdir(exist_ok=True)
    for example in sorted(cfg_dir.glob("*.example.json")):
        target = cfg_dir / (example.name.replace(".example.json", ".json"))
        if not target.exists():
            shutil.copyfile(example, target)
            print(f"  created {target.name}")


def electron_binary() -> Path | None:
    """Resolve Electron's binary exactly the way the app does.

    Prefer the controller's resolver so setup can never disagree with
    `python main.py`; fall back to a direct probe if the package cannot import.
    """
    try:
        from ui.controller import electron_executable

        found = electron_executable(APP)
        if found is not None:
            return Path(found)
        return None
    except Exception:
        pass
    for name in ("electron.exe", "electron"):
        candidate = APP / "node_modules" / "electron" / "dist" / name
        if candidate.is_file():
            return candidate
    return None


def ensure_electron_binary(npm: str) -> bool:
    """`npm install` succeeds even when Electron's binary download fails.

    That leaves node_modules/electron/dist empty and FRIDAY unable to start, so
    setup verifies the binary and repairs it instead of declaring victory.
    """
    if electron_binary() is not None:
        print("  ✓ electron binary present")
        return True

    pkg = APP / "node_modules" / "electron"
    if not pkg.is_dir():
        print("  ✗ node_modules/electron is missing — `npm install` did not complete.")
        return False

    print("  ! electron package present but its binary is missing")
    print("    (npm reports success even when that download fails) — repairing...")
    subprocess.run([npm, "rebuild", "electron"], cwd=str(APP), check=False)
    if electron_binary() is not None:
        print("  ✓ electron binary downloaded")
        return True

    node = shutil.which("node")
    if node is not None:
        subprocess.run(
            [node, str(pkg / "install.js")], cwd=str(APP), check=False
        )
    if electron_binary() is not None:
        print("  ✓ electron binary downloaded")
        return True

    print("  ✗ Electron's binary is still missing — the download is being blocked.")
    print("    Run this and read the error it prints:")
    print(f"      cd {APP}")
    print("      node node_modules\\electron\\install.js")
    return False


def build_ui() -> bool:
    """`npm install` + `npm run build` inside ui/app.

    Electron's binary is a ~120 MB download the first time; both steps are
    idempotent, so re-running setup is cheap.
    """
    if not APP.is_dir():
        print(f"  ✗ {APP} is missing — the renderer is part of the repository.")
        return False

    node = shutil.which("node")
    npm = shutil.which("npm")
    if node is None or npm is None:
        print("  ✗ Node.js / npm not found on PATH.")
        print("    Install Node 20+ from https://nodejs.org, then re-run setup,")
        print("    or build manually:  cd ui\\app  &&  npm install  &&  npm run build")
        return False

    try:
        version = subprocess.run(
            [node, "--version"], capture_output=True, text=True, timeout=30, check=True
        ).stdout.strip()
        major = int(version.lstrip("v").split(".")[0])
        if major < 20:
            print(f"  ✗ Node {version} is too old — FRIDAY's renderer needs Node 20+.")
            return False
        print(f"  node {version}")

        print("  installing renderer dependencies (Electron downloads on first run)...")
        subprocess.run(
            [npm, "install", "--no-audit", "--no-fund"],
            cwd=str(APP),
            check=True,
        )
        print("  building the renderer...")
        subprocess.run([npm, "run", "build"], cwd=str(APP), check=True)
    except subprocess.CalledProcessError as exc:
        print(f"  ✗ UI build failed ({exc}).")
        print(f"    Reproduce it directly:  cd {APP}  &&  npm run build")
        return False
    except Exception as exc:  # noqa: BLE001 - setup must explain, not traceback
        print(f"  ✗ UI build failed ({type(exc).__name__}: {exc}).")
        return False

    if not (APP / "dist" / "index.html").exists():
        print("  ✗ `npm run build` produced no dist/index.html.")
        return False
    print("  ✓ renderer built (ui/app/dist)")
    return ensure_electron_binary(npm)


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    print("Installing Python requirements...")
    subprocess.run([sys.executable, "-m", "pip", "install",
                    "-r", str(ROOT / "requirements.txt")], check=True)

    print("Installing Playwright Chromium browser...")
    try:
        subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], check=False)
    except Exception as e:
        print(f"  Note: Playwright browser install deferred ({e}).")

    print("Building FRIDAY's UI (Electron + React)...")
    ui_ready = build_ui()

    print("Creating default config files...")
    create_default_configs()

    if not ui_ready:
        print("\nSetup finished with warnings: FRIDAY's UI is not usable yet.")
        print("Fix the problem above, then run:")
        print("   cd ui\\app  &&  npm install  &&  npm rebuild electron  &&  npm run build")
        print("Verify everything at once with:")
        print("   python scripts\\verify_windows_ui.py")
        return 1

    print("\nSetup complete! Open config/api_keys.json, add your Gemini API key, and run:")
    print("   python main.py")
    print("\nRebuild the UI after renderer changes with:")
    print("   cd ui\\app  &&  npm run build")
    print("\nVerify the whole chain (toolchain, Electron, live session) with:")
    print("   python scripts\\verify_windows_ui.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
