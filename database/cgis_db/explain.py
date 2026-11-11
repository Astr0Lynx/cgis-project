# Reproducible EXPLAIN ANALYZE samples that show which spatial indexes the key queries use.
# Seeds a synthetic benchmark grid inside one transaction that is always rolled back.

from __future__ import annotations

import platform

import psycopg

# (title, SQL) pairs; tests/test_explain_and_cli.py looks plans up by these titles.
QUERIES = [
    (
        "geo.admin_units bbox filter (&& + ST_Intersects)",
        "SELECT unit_id FROM geo.admin_units u "
        "WHERE u.geom && ST_MakeEnvelope(78.010, 17.980, 78.020, 17.990, 4326) "
        "AND ST_Intersects(u.geom, ST_MakeEnvelope(78.010, 17.980, 78.020, 17.990, 4326))",
    ),
    (
        "geo.units_in_bbox() primitive, bbox given in EPSG:3857",
        "SELECT unit_id FROM geo.units_in_bbox("
        "8683365, 2034290, 8684478, 2035460, 3857)",
    ),
    (
        "ST_Intersects with an EPSG:32644 (UTM 44N) AOI transformed to 4326 (query side only)",
        "SELECT unit_id FROM geo.admin_units u, "
        "LATERAL (SELECT ST_Transform(ST_Buffer(ST_SetSRID(ST_MakePoint(184300, 1990700), 32644), 500), 4326) AS q) t "
        "WHERE ST_Intersects(u.geom, t.q)",
    ),
    (
        "ANTI-PATTERN: transforming the indexed column (index cannot be used)",
        "SELECT unit_id FROM geo.admin_units u "
        "WHERE ST_Intersects(ST_Transform(u.geom, 3857), "
        "ST_MakeEnvelope(8683365, 2034290, 8684478, 2035460, 3857))",
    ),
    (
        "catalog.current_datasets with bbox predicate (pushes down to GiST + PK anti-join)",
        "SELECT dataset_id, version FROM catalog.current_datasets "
        "WHERE bbox && ST_MakeEnvelope(78.010, 17.980, 78.020, 17.990, 4326)",
    ),
    (
        "catalog.resolve_dataset() for one id",
        "SELECT dataset_id, version FROM catalog.resolve_dataset('bench-ds-00042')",
    ),
]

# Benchmark rows: an n x n grid of 0.01 deg units and m dataset ids x 3 versions.
SEED_SQL = """
INSERT INTO geo.admin_units (unit_id, name, level, geom, source, provenance)
SELECT format('bench-%s-%s', i, j), format('Bench %s/%s', i, j), 'bench_cell',
       ST_Multi(ST_MakeEnvelope(77.0 + i * 0.01, 17.0 + j * 0.01,
                                77.0 + (i + 1) * 0.01, 17.0 + (j + 1) * 0.01, 4326)),
       'explain-benchmark', '{"synthetic": true}'
  FROM generate_series(0, %(n)s - 1) i, generate_series(0, %(n)s - 1) j;

INSERT INTO catalog.datasets (dataset_id, version, title, kind, product_level, crs, bbox,
                              checksum_sha256, source, status)
SELECT format('bench-ds-%s', lpad(i::text, 5, '0')), v, 'SYNTHETIC benchmark row', 'vector', 'other',
       'EPSG:4326',
       ST_MakeEnvelope(70 + (i % 100) * 0.1, 10 + (i / 100) * 0.1,
                       70 + (i % 100) * 0.1 + 0.1, 10 + (i / 100) * 0.1 + 0.1, 4326),
       md5(i::text || '-' || v) || md5(v::text || '-' || i), 'explain-benchmark',
       CASE WHEN v = 3 THEN 'staged' ELSE 'ready' END
  FROM generate_series(0, %(m)s - 1) i, generate_series(1, 3) v;

ANALYZE geo.admin_units;
ANALYZE catalog.datasets;
"""


# Builds the Markdown report: section A on the existing (tiny) data, section B on the seeded grid.
# Used by cli.cmd_explain and tests/test_explain_and_cli.py.
def run(conn: psycopg.Connection, grid: int = 150, datasets: int = 5000) -> str:
    version, postgis = conn.execute("SELECT version(), postgis_lib_version()").fetchone()
    out = [f"- PostgreSQL: `{version}`",
           f"- PostGIS: `{postgis}`; client platform: `{platform.platform()}`"]
    with conn.transaction(force_rollback=True):
        n_units = conn.execute("SELECT count(*) FROM geo.admin_units").fetchone()[0]
        out += ["", "## A. Tiny fixture only (expect sequential scans; no speed claims)", "",
                f"Rows in `geo.admin_units`: {n_units}"]
        conn.execute("ANALYZE geo.admin_units; ANALYZE catalog.datasets")
        out += _explain(conn, QUERIES[:1])

        conn.execute(SEED_SQL.replace("%(n)s", str(int(grid))).replace("%(m)s", str(int(datasets))))
        n_units = conn.execute("SELECT count(*) FROM geo.admin_units").fetchone()[0]
        n_ds = conn.execute("SELECT count(*) FROM catalog.datasets").fetchone()[0]
        out += ["", f"## B. Benchmark grid (rolled back): {n_units} units, {n_ds} catalog rows"]
        out += _explain(conn, QUERIES)
    return "\n".join(out) + "\n"


# Runs EXPLAIN ANALYZE for each query and formats title, SQL and plan as Markdown. Used by run.
def _explain(conn: psycopg.Connection, queries) -> list[str]:
    out = []
    for title, sql in queries:
        plan = conn.execute("EXPLAIN (ANALYZE, BUFFERS, COSTS OFF, SUMMARY ON) " + sql).fetchall()
        out += ["", f"### {title}", "", "```sql", sql, "```", "", "```text", *(r[0] for r in plan), "```"]
    return out
