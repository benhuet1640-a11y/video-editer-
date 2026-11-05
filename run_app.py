"""Entry point used by PyInstaller for the double-click app."""
import multiprocessing
import os
import sys

# A windowed app has no console: give libraries that print progress somewhere safe to write.
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w")

from ad_splicer.cli import main  # noqa: E402

if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())
