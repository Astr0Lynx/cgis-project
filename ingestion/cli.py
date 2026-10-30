"""Command line interface.

    python -m ingestion.cli generate-fixtures [--out-dir /data/fixtures]
    python -m ingestion.cli validate --manifest PATH
    python -m ingestion.cli import   --manifest PATH [--assets-dir /data/assets] [--dsn DSN] [--only ID]...
    python -m ingestion.cli seed     [--fixtures-dir /data/fixtures]          # generate + import

One JSON object per dataset is printed on stdout; logs go to stderr.
Exit codes: 0 success, 1 at least one dataset failed, 2 bad usage/manifest, 3 database unavailable.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Sequence

from . import __version__
from .errors import CatalogError, IngestionError, ManifestError
from .manifest import load_manifest, parse_entry
from .paths import DEFAULT_FIXTURES_DIR, assets_root

EXIT_OK, EXIT_FAILED, EXIT_USAGE, EXIT_DB = 0, 1, 2, 3
log = logging.getLogger("ingestion")


def _emit(obj: dict) -> None:
    print(json.dumps(obj, sort_keys=True, default=str), flush=True)


def _fixtures_dir(arg: str | None) -> Path:
    return Path(arg or os.environ.get("INGESTION_FIXTURES_DIR") or DEFAULT_FIXTURES_DIR)


def _generate(out: Path) -> dict:
    from rasterio.errors import RasterioError

    from .fixtures import generate

    try:
        return generate(out)
    except (OSError, RasterioError) as exc:
        raise IngestionError(f"cannot write fixtures to {out}: {exc}", code="fixture_write_failed") from exc


def cmd_generate(args: argparse.Namespace) -> int:
    out = _fixtures_dir(args.out_dir)
    written = _generate(out)
    _emit({"command": "generate-fixtures", "out_dir": str(out), "synthetic": True,
           "files": sorted(p.name for p in written.values())})
    return EXIT_OK


def _run_manifest(manifest_path: Path, args: argparse.Namespace, *, validate_only: bool) -> int:
    from .importer import ImportResult, Importer

    try:
        base_dir, entries = load_manifest(manifest_path)
    except ManifestError as exc:
        _emit({"manifest": str(manifest_path), "outcome": "failed", "error": exc.to_dict()})
        return EXIT_USAGE

    catalog = None
    conn = None
    if not validate_only:
        from .catalog import Catalog, connect
        try:
            conn = connect(args.dsn, wait_seconds=args.wait_db)
            catalog = Catalog(conn)
        except CatalogError as exc:
            _emit({"manifest": str(manifest_path), "outcome": "failed", "error": exc.to_dict()})
            if conn is not None:
                conn.close()
            return EXIT_DB

    importer = Importer(catalog, assets_root(getattr(args, "assets_dir", None)))
    failed = 0
    try:
        for i, raw in enumerate(entries):
            ident = {"dataset_id": raw.get("dataset_id"), "version": raw.get("version"), "kind": raw.get("kind")}
            if args.only and raw.get("dataset_id") not in args.only:
                continue
            try:
                spec = parse_entry(raw, base_dir)
                result = importer.validate(spec) if validate_only else importer.import_dataset(spec)
                _emit(result.to_dict())
            except Exception as exc:  # one bad dataset must not abort the others
                failed += 1
                if isinstance(exc, IngestionError):
                    err = exc.to_dict()
                else:
                    log.exception("unexpected error importing entry %d", i)
                    err = {"code": "internal_error", "message": f"{type(exc).__name__}: {exc}"}
                _emit({**ident, "entry": i, "outcome": "failed", "error": err})
                if args.fail_fast:
                    break
    finally:
        if conn is not None:
            conn.close()
    return EXIT_FAILED if failed else EXIT_OK


def cmd_validate(args: argparse.Namespace) -> int:
    return _run_manifest(Path(args.manifest), args, validate_only=True)


def cmd_import(args: argparse.Namespace) -> int:
    return _run_manifest(Path(args.manifest), args, validate_only=False)


def cmd_seed(args: argparse.Namespace) -> int:
    from .fixtures import MANIFEST_FILE

    out = _fixtures_dir(args.fixtures_dir)
    _generate(out)
    _emit({"command": "generate-fixtures", "out_dir": str(out), "synthetic": True})
    return _run_manifest(out / MANIFEST_FILE, args, validate_only=False)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m ingestion.cli",
                                description="CyberGIS Phase 2 ingestion (validation, COG, catalog, fixtures)")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    p.add_argument("--log-level", default=os.environ.get("INGESTION_LOG_LEVEL", "INFO"))
    sub = p.add_subparsers(dest="command", required=True)

    def add_import_opts(sp, with_manifest: bool = True):
        if with_manifest:
            sp.add_argument("--manifest", required=True, help="manifest JSON (single object, list or {datasets})")
        sp.add_argument("--only", action="append", metavar="DATASET_ID", help="restrict to these dataset ids")
        sp.add_argument("--fail-fast", action="store_true", help="stop at the first failing dataset")

    def add_db_opts(sp):
        sp.add_argument("--dsn", default=None,
                        help="PostgreSQL DSN (default: $INGESTION_DATABASE_URL, $DATABASE_URL or libpq PG* vars)")
        sp.add_argument("--assets-dir", default=None, help="assets root (default: $INGESTION_ASSETS_DIR or "
                                                           "/data/assets)")
        sp.add_argument("--wait-db", type=float, default=float(os.environ.get("INGESTION_DB_WAIT_SECONDS", 0)),
                        metavar="SECONDS", help="retry the DB connection for up to SECONDS")

    g = sub.add_parser("generate-fixtures", help="write the deterministic SYNTHETIC fixtures")
    g.add_argument("--out-dir", default=None, help="default: $INGESTION_FIXTURES_DIR or /data/fixtures")
    g.set_defaults(func=cmd_generate)

    v = sub.add_parser("validate", help="validate a manifest and its files without writing anything")
    add_import_opts(v)
    v.set_defaults(func=cmd_validate)

    i = sub.add_parser("import", help="validate, convert to COG / normalize, and register datasets")
    add_import_opts(i)
    add_db_opts(i)
    i.set_defaults(func=cmd_import)

    s = sub.add_parser("seed", help="generate SYNTHETIC fixtures then import their manifest")
    s.add_argument("--fixtures-dir", default=None, help="default: $INGESTION_FIXTURES_DIR or /data/fixtures")
    add_import_opts(s, with_manifest=False)
    add_db_opts(s)
    s.set_defaults(func=cmd_seed)
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=args.log_level.upper(), stream=sys.stderr,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        return args.func(args)
    except IngestionError as exc:
        log.error("[%s] %s", exc.code, exc.message)
        _emit({"outcome": "failed", "error": exc.to_dict()})
        return EXIT_FAILED


if __name__ == "__main__":
    sys.exit(main())
