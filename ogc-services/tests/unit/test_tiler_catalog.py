# Unit tests: tile-service catalog rows -> public discovery metadata (cgis_tiler.catalog); no database.

from cgis_tiler.catalog import _row_to_record

BASE_ROW = ("india-l2-pop-2011-warangal", 2, "Level 2 Warangal", "rasters/x/v2/x_v2.tif", "EPSG:32644",
            -99999.0, "people_per_cell", 2011, 79.5, 17.9, 79.7, 18.1,
            "level2", 23.5, 23.5, "india-level2-2011", False, "CC BY 4.0", None)
RENDER = ("level2-people-per-cell", "people_per_cell", "viridis", 0.0, 2.76125,
          "Population per ~23.5 m cell", "Shared fixed scale")


# A full row yields product level, resolution, public provenance facts and the render profile.
def test_record_carries_public_metadata_and_render_profile():
    rec = _row_to_record(BASE_ROW + RENDER, with_render=True)
    assert rec.product_level == "level2" and rec.resolution == (23.5, 23.5)
    assert rec.public == {"group_id": "india-level2-2011", "synthetic": False, "license": "CC BY 4.0",
                          "citation": None}
    assert rec.render["profile_id"] == "level2-people-per-cell" and rec.render["rescale"] == [0.0, 2.76125]
    # never a path in the public part
    assert all("rasters/" not in str(v) for v in rec.public.values())


# No matching profile, or no render support, gives render None.
def test_record_without_profile_or_render_support():
    assert _row_to_record(BASE_ROW + (None,) * 7, with_render=True).render is None
    assert _row_to_record(BASE_ROW, with_render=False).render is None


# Blank strings and a non-boolean synthetic flag become None.
def test_non_boolean_synthetic_and_blank_strings_are_dropped():
    row = list(BASE_ROW)
    row[15], row[16], row[17] = "  ", "yes", ""
    rec = _row_to_record(tuple(row), with_render=False)
    assert rec.public == {"group_id": None, "synthetic": None, "license": None, "citation": None}
