"""Allow ``python -m duplicate_detector``."""

import sys

from duplicate_detector.cli import main

if __name__ == "__main__":
    sys.exit(main())
