# Command line: generate-fixtures | validate | import | seed (`python -m ingestion.cli --help`).
# Prints one JSON object per dataset on stdout, logs on stderr.
# Exit codes: 0 ok, 1 a dataset failed, 2 bad usage/manifest, 3 database unavailable.

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

EXIT_OK, EXIT_FAILED, EXIT_USAGE, EXIT_DB = 0, 1, 2, 3
DEFAULT_ASSETS_DIR = "/data/assets"
DEFAULT_FIXTURES_DIR = "/data/fixtures"
log = logging.getLogger("ingestion")


# Print one sorted-key JSON line to stdout. Used by every command.
def _emit(obj: dict) -> None:
    print(json.dumps(obj, sort_keys=True, default=str), flush=True)


# --fixtures-dir/--out-dir > $INGESTION_FIXTURES_DIR > /data/fixtures. Used by cmd_generate, cmd_seed.
def _fixtures_dir(arg: str | None) -> Path:
    return Path(arg or os.environ.get("INGESTION_FIXTURES_DIR") or DEFAULT_FIXTURES_DIR)


# --assets-dir > $INGESTION_ASSETS_DIR > /data/assets, made absolute. Used by _run_manifest.
def _assets_dir(arg: str | None) -> Path:
    return Path(arg or os.environ.get("INGESTION_ASSETS_DIR") or DEFAULT_ASSETS_DIR).absolute()


# Write the SYNTHETIC fixtures, mapping write errors to fixture_write_failed. Used by cmd_generate, cmd_seed.
def _generate(out: Path) -> dict:
    from rasterio.errors import RasterioError

    from .fixtures import generate

    try:
        return generate(out)
    except (OSError, RasterioError) as exc:
        raise IngestionError(f"cannot write fixtures to {out}: {exc}", code="fixture_write_failed") from exc


# `generate-fixtures`: write fixtures and print the file list.
def cmd_generate(args: argparse.Namespace) -> int:
    out = _fixtures_dir(args.out_dir)
    written = _generate(out)
    _emit({"command": "generate-fixtures", "out_dir": str(out), "synthetic": True,
           "files": sorted(p.name for p in written.values())})
    return EXIT_OK


# Validate or import every manifest entry, one JSON line each; a bad entry never aborts the others
# (unless --fail-fast). Used by the validate, import and seed commands.
def _run_manifest(manifest_path: Path, args: argparse.Namespace, *, validate_only: bool) -> int:
    from .importer import Importer

    try:
        base_dir, entries = load_manifest(manifest_path)
    except ManifestError as exc:
        _emit({"manifest": str(manifest_path), "outcome": "failed", "error": exc.to_dict()})
        return EXIT_USAGE

    catalog = conn = None
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

    importer = Importer(catalog, _assets_dir(getattr(args, "assets_dir", None)))
    failed = 0
    try:
        for i, raw in enumerate(entries):
            if args.only and raw.get("dataset_id") not in args.only:
                continue
            try:
                spec = parse_entry(raw, base_dir)
                result = importer.validate(spec) if validate_only else importer.import_dataset(spec)
                _emit(result.to_dict())
            except Exception as exc:
                failed += 1
                if isinstance(exc, IngestionError):
                    err = exc.to_dict()
                else:
                    log.exception("unexpected error importing entry %d", i)
                    err = {"code": "internal_error", "message": f"{type(exc).__name__}: {exc}"}
                _emit({"dataset_id": raw.get("dataset_id"), "version": raw.get("version"), "kind": raw.get("kind"),
                       "entry": i, "outcome": "failed", "error": err})
                if args.fail_fast:
                    break
    finally:
        if conn is not None:
            conn.close()
    return EXIT_FAILED if failed else EXIT_OK


# `seed`: generate fixtures, then import their manifest.
def cmd_seed(args: argparse.Namespace) -> int:
    from .fixtures import MANIFEST_FILE

    out = _fixtures_dir(args.fixtures_dir)
    _generate(out)
    _emit({"command": "generate-fixtures", "out_dir": str(out), "synthetic": True})
    return _run_manifest(out / MANIFEST_FILE, args, validate_only=False)


# Argument parser for all subcommands. Used by main.
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m ingestion.cli",
                                description="CyberGIS ingestion (validation, COG, catalog, fixtures)")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    p.add_argument("--log-level", default=os.environ.get("INGESTION_LOG_LEVEL", "INFO"))
    sub = p.add_subparsers(dest="command", required=True)

    # --manifest/--only/--fail-fast shared by validate, import, seed.
    def add_import_opts(sp, with_manifest: bool = True):
        if with_manifest:
            sp.add_argument("--manifest", required=True, help="manifest JSON (single object, list or {datasets})")
        sp.add_argument("--only", action="append", metavar="DATASET_ID", help="restrict to these dataset ids")
        sp.add_argument("--fail-fast", action="store_true", help="stop at the first failing dataset")

    # --dsn/--assets-dir/--wait-db shared by import and seed.
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
    v.set_defaults(func=lambda a: _run_manifest(Path(a.manifest), a, validate_only=True))

    i = sub.add_parser("import", help="validate, convert to COG / normalize, and register datasets")
    add_import_opts(i)
    add_db_opts(i)
    i.set_defaults(func=lambda a: _run_manifest(Path(a.manifest), a, validate_only=False))

    s = sub.add_parser("seed", help="generate SYNTHETIC fixtures then import their manifest")
    s.add_argument("--fixtures-dir", default=None, help="default: $INGESTION_FIXTURES_DIR or /data/fixtures")
    add_import_opts(s, with_manifest=False)
    add_db_opts(s)
    s.set_defaults(func=cmd_seed)
    return p


# CLI entry point: parse, configure stderr logging, run the command; returns the exit code.
# Used by `python -m ingestion.cli` (Dockerfile ENTRYPOINT) and tests.
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
