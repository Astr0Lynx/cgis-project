# `python -m cgis_app <command>`: same as the `cgis-app` console script (cli.main).
import sys

from .cli import main

sys.exit(main())
