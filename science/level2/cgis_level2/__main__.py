# Command line for Level 2: `run` builds and evaluates town COGs, `make-synthetic` writes test inputs.
import argparse
from pathlib import Path
import sys

import fiona
import rasterio
from pyproj.exceptions import ProjError
from shapely.errors import ShapelyError

from ingestion.errors import IngestionError

from .pipeline import run
from .synthetic import make_fixture


# Parses the CLI, dispatches to pipeline.run or synthetic.make_fixture and maps expected errors to exit 1.
def main():
    parser = argparse.ArgumentParser(prog="python -m science.level2.cgis_level2")
    commands = parser.add_subparsers(dest="command", required=True)
    compute = commands.add_parser("run", help="produce and evaluate Level 2 town COGs")
    compute.add_argument("--config", type=Path, required=True)
    compute.add_argument("--only", metavar="TOWN_SLUG", help="profile one town without publishing the set")
    fixture = commands.add_parser("make-synthetic", help="generate reproducible synthetic inputs")
    fixture.add_argument("--out", type=Path, required=True)
    fixture.add_argument("--level1-manifest", type=Path, help="use a generated synthetic 2011 Level 1 manifest (cgis_level1 synthetic run)")
    args = parser.parse_args()
    try:
        result = (run(args.config, only=args.only) if args.command == "run"
                  else make_fixture(args.out, args.level1_manifest))
    except (OSError, ValueError, TypeError, KeyError, MemoryError, IngestionError,
            rasterio.errors.RasterioError, fiona.errors.FionaError, ShapelyError, ProjError) as exc:
        print(f"Level 2 failed: {exc}", file=sys.stderr)
        return 1
    print(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
