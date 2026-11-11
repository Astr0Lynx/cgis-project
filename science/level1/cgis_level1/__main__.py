# `python -m cgis_level1` entry point; delegates to cli.main.
import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
