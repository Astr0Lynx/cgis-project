# Allows `python -m cgis_db ...` (see cli.py).
import sys

from .cli import main

sys.exit(main())
