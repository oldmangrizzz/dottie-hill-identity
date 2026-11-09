"""Entry point for `python3 -m dottie.continuity` — identical behaviour to the former module."""
import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
