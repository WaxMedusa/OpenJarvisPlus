"""PyInstaller entry point (runs outside the package, so imports are absolute)."""

import sys

from jarvis_desktop.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
