"""OPTIONAL PostGIS Raster track: tiled synthetic fixture, ST_Clip + ST_SummaryStatsAgg."""

import psycopg
import pytest

from cgis_db import migrate

AOIS = {
    "town-a": "SRID=4326;POLYGON((78.010 17.980,78.020 17.980,78.020 17.990,78.010 17.990,78.010 17.980))",
    "town-b": "SRID=4326;POLYGON((78.030 17.950,78.035 17.950,78.035 17.955,78.030 17.955,78.030 17.950))",
    "nodata-edge": "SRID=4326;POLYGON((77.998 17.992,78.008 17.992,78.008 18.002,77.998 18.002,77.998 17.992))",
    "disjoint": "SRID=4326;POLYGON((79.000 19.000,79.010 19.000,79.010 19.010,79.000 19.010,79.000 19.000))",
}


def _quiet(_):
    pass


@pytest.fixture(scope="module")
def raster_dsn(db_factory):
    dsn = db_factory.create()
    with psycopg.connect(dsn, autocommit=True) as c:
        if not c.execute("SELECT 1 FROM pg_available_extensions WHERE name = 'postgis_raster'").fetchone():
            pytest.skip("postgis_raster extension not installed on this server (optional track)")
        migrate.up(c, log=_quiet)
        migrate.up(c, "raster_demo", log=_quiet)
        c.execute("SELECT raster_demo.load_synthetic_fixture('synthetic-worldpop-2001', 1, 1)")
        c.execute("SELECT raster_demo.load_synthetic_fixture('synthetic-worldpop-2011', 1, 2)")
    return dsn


def stats(c, dataset, aoi, touched=False):
    return c.execute("SELECT count_valid, sum, mean, nodata_excluded, pixels_selected"
                     " FROM raster_demo.zonal_stats(%s, 1, ST_GeomFromEWKT(%s), %s)",
                     (dataset, aoi, touched)).fetchone()


@pytest.mark.parametrize("dataset, aoi, expected", [
    ("synthetic-worldpop-2001", "town-a", (100, 100.0, 1.0, 0, 100)),
    ("synthetic-worldpop-2011", "town-a", (100, 200.0, 2.0, 0, 100)),
    ("synthetic-worldpop-2001", "town-b", (25, 25.0, 1.0, 0, 25)),
    ("synthetic-worldpop-2011", "town-b", (25, 50.0, 2.0, 0, 25)),
    # 8x8 pixel centres inside the raster, of which the NoData strip covers 48
    ("synthetic-worldpop-2001", "nodata-edge", (16, 16.0, 1.0, 48, 64)),
    ("synthetic-worldpop-2001", "disjoint", (0, None, None, 0, 0)),
])
def test_zonal_stats_match_contract(raster_dsn, dataset, aoi, expected):
    with psycopg.connect(raster_dsn) as c:
        assert stats(c, dataset, AOIS[aoi]) == expected


def test_fixture_grid_matches_spec(raster_dsn):
    with psycopg.connect(raster_dsn) as c:
        r = c.execute(
            "SELECT ST_Width(u), ST_Height(u), ST_UpperLeftX(u), ST_UpperLeftY(u), ST_ScaleX(u), ST_ScaleY(u),"
            " ST_SRID(u), ST_BandNoDataValue(u, 1), ST_Count(u, 1, true)"
            " FROM (SELECT ST_Union(rast) u FROM raster_demo.tiles WHERE dataset_id='synthetic-worldpop-2001') s"
        ).fetchone()
        assert r == pytest.approx((64, 64, 78.0, 18.0, 0.001, -0.001, 4326, -9999, 60 * 60))
        assert c.execute("SELECT count(*) FROM raster_demo.tiles WHERE dataset_id='synthetic-worldpop-2001'"
                         ).fetchone()[0] == 16


def test_projected_aoi_is_transformed(raster_dsn):
    with psycopg.connect(raster_dsn) as c:
        utm = c.execute("SELECT ST_AsEWKT(ST_Transform(ST_GeomFromEWKT(%s), 32644))", (AOIS["town-a"],)
                        ).fetchone()[0]
        # Reprojected edges are no longer exactly on pixel boundaries; centres stay well inside.
        assert stats(c, "synthetic-worldpop-2001", utm)[:3] == (100, 100.0, 1.0)


def test_pixel_centre_vs_all_touched(raster_dsn):
    """Inset AOI: centre rule selects 8x8, all-touched selects 10x10 (on an untiled raster)."""
    inset = "SRID=4326;POLYGON((78.0107 17.9807,78.0193 17.9807,78.0193 17.9893,78.0107 17.9893,78.0107 17.9807))"
    with psycopg.connect(raster_dsn) as c:
        assert stats(c, "synthetic-worldpop-2001", inset, False)[0] == 64
        whole = c.execute(
            "SELECT ST_Count(ST_Clip(ST_Union(rast), 1, ST_GeomFromEWKT(%s), true, true), 1, true)"
            " FROM raster_demo.tiles WHERE dataset_id = 'synthetic-worldpop-2001'", (inset,)).fetchone()[0]
        assert whole == 100
        # Known limitation, documented in docs/RASTER_DEMO.md: per-tile all-touched clipping
        # drops pixels on tile seams, so the tiled result is below the geometric 100.
        assert stats(c, "synthetic-worldpop-2001", inset, True)[0] < 100


def test_errors(raster_dsn):
    with psycopg.connect(raster_dsn) as c:
        with pytest.raises(psycopg.errors.NoDataFound):
            stats(c, "no-such-raster", AOIS["town-a"])
        c.rollback()
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            stats(c, "synthetic-worldpop-2001", "POLYGON((78.01 17.98,78.02 17.98,78.02 17.99,78.01 17.98))")
        c.rollback()
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            stats(c, "synthetic-worldpop-2001", "SRID=4326;LINESTRING(78.01 17.98,78.02 17.99)")


def test_reload_is_idempotent(raster_dsn):
    with psycopg.connect(raster_dsn) as c:
        c.execute("SELECT raster_demo.load_synthetic_fixture('synthetic-worldpop-2001', 1, 1)")
        assert c.execute("SELECT count(*) FROM raster_demo.tiles WHERE dataset_id='synthetic-worldpop-2001'"
                         ).fetchone()[0] == 16


def test_optional_track_rolls_back_cleanly(db_factory):
    dsn = db_factory.create()
    with psycopg.connect(dsn, autocommit=True) as c:
        if not c.execute("SELECT 1 FROM pg_available_extensions WHERE name = 'postgis_raster'").fetchone():
            pytest.skip("postgis_raster not installed")
        migrate.up(c, log=_quiet)
        migrate.up(c, "raster_demo", log=_quiet)
        with pytest.raises(migrate.MigrationError, match="roll back raster_demo first"):
            migrate.down(c, target=1, log=_quiet)
        migrate.down(c, "raster_demo", target=0, log=_quiet)
        assert c.execute("SELECT to_regnamespace('raster_demo'),"
                         " (SELECT count(*) FROM pg_extension WHERE extname='postgis_raster')").fetchone() == (None, 0)
        assert c.execute("SELECT to_regclass('catalog.datasets') IS NOT NULL").fetchone()[0]
