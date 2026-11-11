# Command line interface: python -m cgis_db [--dsn DSN] migrate|fixtures|raster-demo|explain ...
# Connection: --dsn, else CGIS_DATABASE_URL, else DATABASE_URL, else libpq PG* variables.
# Exit codes: 0 ok, 1 database/migration error, 2 usage error.

from __future__ import annotations

import argparse
import json
import os
import sys

import psycopg

from . import explain, fixtures, migrate

# Fixed test AOIs (the two towns plus a nodata-edge and a disjoint case).
# Used by cmd_raster_demo and tests/test_raster_demo.py.
TOWN_AOIS = {
    "town-a": "SRID=4326;POLYGON((78.010 17.980,78.020 17.980,78.020 17.990,78.010 17.990,78.010 17.980))",
    "town-b": "SRID=4326;POLYGON((78.030 17.950,78.035 17.950,78.035 17.955,78.030 17.955,78.030 17.950))",
    "nodata-edge": "SRID=4326;POLYGON((77.998 17.992,78.008 17.992,78.008 18.002,77.998 18.002,77.998 17.992))",
    "disjoint": "SRID=4326;POLYGON((79.000 19.000,79.010 19.000,79.010 19.010,79.000 19.010,79.000 19.000))",
}
DEMO_RASTERS = (("synthetic-worldpop-2001", 1.0), ("synthetic-worldpop-2011", 2.0))


# Opens a connection from --dsn or the environment (an empty conninfo makes libpq read PG*).
# Used by every cmd_* function.
def connect(dsn: str | None, **kwargs) -> psycopg.Connection:
    dsn = dsn or os.environ.get("CGIS_DATABASE_URL") or os.environ.get("DATABASE_URL") or ""
    return psycopg.connect(dsn, application_name="cgis_db", **kwargs)


# Progress messages go to stderr so stdout stays machine-readable. Passed as log= to migrate.up/down.
def _log(msg: str) -> None:
    print(msg, file=sys.stderr)


# `migrate up|down|status`. Used by main via build_parser.
def cmd_migrate(args) -> int:
    with connect(args.dsn, autocommit=True) as conn:
        if args.action == "up":
            migrate.up(conn, args.track, args.target, log=_log)
        elif args.action == "down":
            if args.target is not None and args.steps is not None:
                raise SystemExit("use either --target or --steps")
            migrate.down(conn, args.track, target=args.target, steps=args.steps, log=_log)
        else:
            rows = migrate.status(conn)
            for r in rows:
                print(f"{r['track']:<12} {r['version']:04d}  {r['name']:<28} {r['state']}")
            if any(r["state"] in ("DRIFT", "MISSING-FILE") for r in rows):
                return 1
    return 0


# `fixtures load`: loads a GeoJSON of units and prints the result as JSON. Used by main.
def cmd_fixtures(args) -> int:
    with connect(args.dsn) as conn:
        result = fixtures.load_units(conn, args.geojson, dataset_id=args.dataset_id,
                                     version=args.dataset_version)
    print(json.dumps(result, indent=2))
    return 0


# `raster-demo`: applies the raster_demo track, loads the synthetic tiles and prints zonal stats
# for every TOWN_AOIS entry as JSON. Used by main.
def cmd_raster_demo(args) -> int:
    with connect(args.dsn, autocommit=True) as conn:
        migrate.up(conn, "raster_demo", log=_log)
        report = {"note": "SYNTHETIC PostGIS Raster demonstration; pixel-centre inclusion (touched=false)",
                  "results": []}
        for dataset_id, value in DEMO_RASTERS:
            tiles = conn.execute("SELECT raster_demo.load_synthetic_fixture(%s, 1, %s, %s)",
                                 (dataset_id, value, args.tile_size)).fetchone()[0]
            for aoi_name, ewkt in TOWN_AOIS.items():
                row = conn.execute(
                    "SELECT * FROM raster_demo.zonal_stats(%s, 1, ST_GeomFromEWKT(%s))",
                    (dataset_id, ewkt)).fetchone()
                report["results"].append({
                    "dataset_id": dataset_id, "tiles": tiles, "aoi": aoi_name,
                    "count_valid": row[0], "sum": row[1], "mean": row[2],
                    "nodata_excluded": row[3], "pixels_selected": row[4]})
    print(json.dumps(report, indent=2))
    return 0


# `explain`: prints the EXPLAIN ANALYZE report of explain.run. Used by main.
def cmd_explain(args) -> int:
    with connect(args.dsn) as conn:
        sys.stdout.write(explain.run(conn, grid=args.grid, datasets=args.datasets))
    return 0


# Defines all subcommands and flags. Used by main.
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m cgis_db", description="CyberGIS database tooling")
    p.add_argument("--dsn", help="libpq connection string/URL (default: env)")
    sub = p.add_subparsers(dest="command", required=True)

    m = sub.add_parser("migrate", help="apply/roll back/inspect schema migrations")
    m.add_argument("action", choices=["up", "down", "status"])
    m.add_argument("--track", default=migrate.CORE, help="migration track (core, raster_demo)")
    m.add_argument("--target", type=int, help="target version (up: apply to; down: roll back to)")
    m.add_argument("--steps", type=int, help="down: number of migrations to roll back (default 1)")
    m.set_defaults(func=cmd_migrate)

    f = sub.add_parser("fixtures", help="load vector test fixtures")
    f.add_argument("action", choices=["load"])
    f.add_argument("--geojson", help="FeatureCollection to load (default: database test stub)")
    f.add_argument("--dataset-id", default="synthetic-towns")
    f.add_argument("--dataset-version", type=int, default=1)
    f.set_defaults(func=cmd_fixtures)

    r = sub.add_parser("raster-demo", help="OPTIONAL PostGIS Raster demonstration")
    r.add_argument("--tile-size", type=int, default=16)
    r.set_defaults(func=cmd_raster_demo)

    e = sub.add_parser("explain", help="print EXPLAIN ANALYZE samples (rolled back)")
    e.add_argument("--grid", type=int, default=150, help="benchmark grid side length")
    e.add_argument("--datasets", type=int, default=5000, help="benchmark catalog dataset ids")
    e.set_defaults(func=cmd_explain)
    return p


# Entry point: runs one subcommand and turns expected errors into a one-line message and exit 1.
# Used by __main__.py and the `cgis-db` console script.
def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (migrate.MigrationError, fixtures.FixtureError, psycopg.Error) as exc:
        print(f"error: {exc}".rstrip(), file=sys.stderr)
        return 1
