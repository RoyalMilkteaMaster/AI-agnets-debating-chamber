"""Entrypoint for ``python -m hoya_market_agents``."""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
