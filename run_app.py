"""Entry point used by PyInstaller for the double-click app."""
import multiprocessing
import sys

from ad_splicer.cli import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())
