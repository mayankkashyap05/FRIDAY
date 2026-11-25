"""Allow ``python -m ui_mac`` on macOS."""
from __future__ import annotations

from ui_mac import FridayUI


def main():
    ui = FridayUI()
    ui.run()


if __name__ == "__main__":
    main()
