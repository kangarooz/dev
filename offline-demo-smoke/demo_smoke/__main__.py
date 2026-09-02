"""Entry point: ``python -m demo_smoke <cmd> ...``."""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
