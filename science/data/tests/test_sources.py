import json
import dataclasses

import pytest

from cgis_data import sources as S


def test_exactly_eleven_worldpop_years():
    wp = [s for s in S.all_sources() if s.category == "worldpop"]
    assert sorted(s.year for s in wp) == list(range(2001, 2012)) and len(wp) == 11
    assert all(s.units == "people_per_cell" and s.crs == "EPSG:4326" for s in wp)
    assert all(s.url.endswith(f"ind_ppp_{s.year}.tif") for s in wp)


def test_ghsl_total_and_nres_epochs_and_units():
    tot = [s for s in S.all_sources() if s.category == "ghsl_built_total"]
    nres = [s for s in S.all_sources() if s.category == "ghsl_built_nres"]
    # 3 required flowchart epochs + the approved additional 2015 epoch (2011 only)
    assert sorted(s.year for s in tot) == sorted(s.year for s in nres) == [2000, 2005, 2010, 2015]
    for s in tot + nres:
        assert s.units == "m2_per_cell" and s.crs == "EPSG:4326" and "4326_3ss" in s.url and "GHS_BUILT_S" in s.url and "BUILT_V" not in s.url
        assert "volume" in s.semantics.lower() or "Not GHS-BUILT-V" in s.semantics
    assert all("NRES" in s.url for s in nres) and not any("NRES" in s.url for s in tot)
    assert all(s.tiles and "{row}" in s.tiles["pattern"] for s in tot + nres)


def test_ghsl_pop_is_reference_count():
    pop = [s for s in S.all_sources() if s.category == "ghsl_pop"]
    assert [s.year for s in pop] == [2000, 2005, 2010, 2015] and all(s.units == "people_per_cell" for s in pop)


def test_additional_epoch_is_flagged_and_required_ones_are_not():
    by = {s.source_id: s for s in S.all_sources()}
    assert by["india-ghsl-built-total-2015"].extra["required_by_flowchart"] is False
    assert "2011" in by["india-ghsl-built-total-2015"].semantics
    assert by["india-ghsl-built-total-2010"].extra["required_by_flowchart"] is True
    assert "54009_100" in by["india-ghsl-built-total-2010"].extra["alt_grid_mollweide_100m_url"]


def test_geoboundaries_are_diagnostic_not_census():
    by = {s.source_id: s for s in S.all_sources()}
    for k in ("india-geoboundaries-adm1", "india-geoboundaries-adm2"):
        assert by[k].category == "boundary_diagnostic" and by[k].extra["authoritative"] is False
        assert "NO Census" in by[k].semantics
    assert by["india-admin-boundaries-2011"].access == S.MISSING        # real requirement still open


def test_gated_sources_are_never_marked_available():
    by = {s.source_id: s for s in S.all_sources()}
    assert by["india-bhuvan-lulc-50k-2011-12"].access == S.RESTRICTED
    assert by["india-census-2011-pca"].access == S.MISSING          # village/town level not yet selected/fetched
    assert by["india-census-2011-pca-state-district"].access == S.AVAILABLE
    assert by["india-datameet-census2011-districts"].extra["authoritative"] is False
    assert by["india-admin-boundaries-2011"].access == S.MISSING


def test_inventory_unique_ids_and_state_merge():
    inv = S.build_inventory({"india-worldpop-unconstrained-2005": {"size_bytes": 5, "sha256": "ab"}})
    assert inv["count"] == len(inv["sources"]) == 11 + 4 + 4 + 4 + 2 + 5
    row = next(r for r in inv["sources"] if r["source_id"] == "india-worldpop-unconstrained-2005")
    assert row["size_bytes"] == 5 and row["sha256"] == "ab"
    json.dumps(inv)


def test_inventory_rejects_bad_access_state():
    with pytest.raises(ValueError):
        S.build_inventory({"india-census-2011-pca": {"access": "wide-open"}})
