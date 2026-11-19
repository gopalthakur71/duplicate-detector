"""Run the app from a source checkout without installing it: ``py app.py``.

Also used as the PyInstaller entry point for the Windows executable.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

from duplicate_detector.gui import main

if __name__ == "__main__":
    main()
