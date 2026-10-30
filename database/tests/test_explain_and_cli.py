"""Index usage evidence (EXPLAIN) and CLI behaviour."""

import json
import subprocess
import sys
from pathlib import Path

import psycopg

from cgis_db import explain

DB_DIR = Path(__file__).resolve().parent.parent


def test_explain_shows_gist_usage_on_benchmark_grid(migrated_dsn):
    with psycopg.connect(migrated_dsn) as c:
        report = explain.run(c, grid=60, datasets=1000)
        assert c.execute("SELECT count(*) FROM geo.admin_units").fetchone()[0] == 0, "benchmark rolled back"
    section_b = report.split("## B.")[1]
    blocks = section_b.split("### ")[1:]
    plans = {b.splitlines()[0]: b for b in blocks}
    assert "admin_units_geom_gist" in plans["geo.admin_units bbox filter (&& + ST_Intersects)"]
    assert "admin_units_geom_gist" in plans["geo.units_in_bbox() primitive, bbox given in EPSG:3857"] or \
        "Function Scan" in plans["geo.units_in_bbox() primitive, bbox given in EPSG:3857"]
    anti = plans["ANTI-PATTERN: transforming the indexed column (index cannot be used)"]
    assert "Seq Scan" in anti and "admin_units_geom_gist" not in anti
    assert "datasets_bbox_gist" in plans[
        "catalog.current_datasets with bbox predicate (pushes down to GiST + PK anti-join)"]


def _cli(dsn, *args):
    return subprocess.run([sys.executable, "-m", "cgis_db", "--dsn", dsn, *args], cwd=DB_DIR,
                          capture_output=True, text=True, timeout=300)


def test_cli_end_to_end(fresh_dsn):
    r = _cli(fresh_dsn, "migrate", "up")
    assert r.returncode == 0, r.stderr
    assert _cli(fresh_dsn, "migrate", "up").returncode == 0
    r = _cli(fresh_dsn, "migrate", "status")
    assert r.returncode == 0 and "pending" in r.stdout  # optional raster track not applied
    r = _cli(fresh_dsn, "fixtures", "load")
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["units"] == 2
    r = _cli(fresh_dsn, "migrate", "down", "--steps", "1", "--target", "2")
    assert r.returncode != 0


def test_cli_errors_are_clean_and_redacted(fresh_dsn):
    r = _cli("postgresql://someone:s3cret@127.0.0.1:1/nope", "migrate", "status")
    assert r.returncode == 1
    assert r.stderr.startswith("error:") and "Traceback" not in r.stderr
    assert "s3cret" not in r.stderr
