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


def create_default_configs() -> None:
    """Copy *.example.json -> *.json so the app runs without manual config editing."""
    cfg_dir = ROOT / "config"
    cfg_dir.mkdir(exist_ok=True)
    for example in sorted(cfg_dir.glob("*.example.json")):
        target = cfg_dir / (example.name.replace(".example.json", ".json"))
        if not target.exists():
            shutil.copyfile(example, target)
            print(f"  created {target.name}")


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
    return True


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
        print("\nSetup finished with warnings: the UI did not build.")
        print("Fix the Node/Electron problem above, then run:")
        print("   cd ui\\app  &&  npm install  &&  npm run build")
        print("Verify everything at once with:")
        print("   python scripts\\verify_windows_ui.py")
        return 1

    print("\nSetup complete! Open config/api_keys.json, add your Gemini API key, and run:")
    print("   python main.py")
    print("\nRebuild the UI after renderer changes with:")
    print("   cd ui\\app  &&  npm run build")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
