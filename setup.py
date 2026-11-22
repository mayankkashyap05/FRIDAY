"""Install Python dependencies, browser support, and initial configuration."""
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def create_default_configs() -> None:
    cfg_dir = ROOT / 'config'
    cfg_dir.mkdir(exist_ok=True)
    for example in sorted(cfg_dir.glob('*.example.json')):
        target = cfg_dir / example.name.replace('.example.json', '.json')
        if not target.exists():
            shutil.copyfile(example, target)
            print(f'  created {target.name}')


def main() -> int:
    print('Installing Python requirements...')
    subprocess.run([sys.executable, '-m', 'pip', 'install', '-r', str(ROOT/'requirements.txt')], check=True)
    print('Installing Playwright Chromium browser...')
    try:
        subprocess.run([sys.executable, '-m', 'playwright', 'install', 'chromium'], check=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f'  Browser installation deferred: {exc}')
    create_default_configs()
    print('Add a Gemini API key in config/api_keys.json and run: python main.py')
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
