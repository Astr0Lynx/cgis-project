import numpy as np
import rasterio
from rasterio.transform import from_origin
from shapely.geometry import box

from cgis_data import national as N


def _wp(tmp_path, d, nodata=-99999.0):
    p = tmp_path / "w.tif"
    with rasterio.open(p, "w", driver="GTiff", height=d.shape[0], width=d.shape[1], count=1, dtype="float32",
                       crs="EPSG:4326", transform=from_origin(70.0, 30.0, 0.1, 0.1), nodata=nodata) as ds:
        ds.write(d.astype("float32"), 1)
    return str(p)


def test_coverage_reports_holes_and_outside_data(tmp_path):
    d = np.ones((40, 40)); d[5:10, 5:10] = -99999                  # hole inside; data also exists outside footprint
    fp = box(70.0, 26.0, 74.0, 30.0)                                 # = top 40 rows x 40 cols grid is 70-74 / 26-30
    r = N.coverage_vs_footprint(_wp(tmp_path, d), fp, step=1)
    assert r["cells_in_footprint"] == 1600 and r["nodata_in_footprint"] == 25 and r["valid_outside_footprint"] == 0
    r2 = N.coverage_vs_footprint(_wp(tmp_path, d), box(70.0, 28.0, 72.0, 30.0), step=1)
    assert r2["populated_outside_footprint"] > 0


def test_duplicate_year_checksums():
    st = {f"india-worldpop-unconstrained-{y}": {"sha256": "a" if y in (2003, 2004) else str(y)} for y in range(2001, 2012)}
    st["india-worldpop-unconstrained-2001"]["sha256"] = None
    rep = N.duplicate_year_check(st, range(2001, 2012))
    assert rep["duplicate_year_pairs"] == [(2003, 2004)] and rep["missing_checksum_years"] == [2001]
