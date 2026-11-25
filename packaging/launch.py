"""PyInstaller entry point: the GUI with no arguments, the command line with any."""
import sys

from wheecal.__main__ import main

if __name__ == '__main__':
    sys.exit(main())
