import json
import sys
from pathlib import Path

import numpy as np
import pytest

from cgis_data import manifest as M
from cgis_data import sources as S

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))           # reuse the ORIGINAL ingestion manifest parser (read-only)


def test_entry_parses_with_original_ingestion_parser(make_raster, tmp_path):
    from ingestion.manifest import parse_entry
    src = next(s for s in S.all_sources() if s.source_id == "india-worldpop-unconstrained-2005")
    p = make_raster("ind_ppp_2005.tif", data=np.ones((8, 8)))
    e = M.raster_entry(src, "ind_ppp_2005.tif", p, processing_steps=["synthetic test"])
    assert e["units"] == "people_per_cell" and e["product_level"] == "source" and e["year"] == 2005
    assert e["provenance"]["upstream_url"].endswith("ind_ppp_2005.tif")
    spec = parse_entry(e, tmp_path)
    assert spec.dataset_id == "india-worldpop-unconstrained-2005"


def test_non_epsg_crs_is_refused_with_actionable_message(make_raster):
    src = next(s for s in S.all_sources() if s.source_id == "india-ghsl-built-total-2010")
    p = make_raster("moll.tif", data=np.ones((8, 8)), crs="ESRI:54009")
    with pytest.raises(ValueError, match="no EPSG"):
        M.raster_entry(src, "rasters/x.tif", p, processing_steps=[])


def test_duplicate_dataset_version_rejected(tmp_path):
    e = {"dataset_id": "a", "version": 1}
    with pytest.raises(ValueError):
        M.write_manifest(tmp_path / "m.json", [e, dict(e)])


def test_vector_sample_entry_parses_and_rejects_invalid(tmp_path):
    from cgis_data.vector_sample import make_vector_sample
    from ingestion.manifest import parse_entry
    sq = lambda x: {"type": "Polygon", "coordinates": [[(x, 0), (x + 1, 0), (x + 1, 1), (x, 1), (x, 0)]]}
    src = tmp_path / "a.geojson"
    src.write_text(json.dumps({"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"shapeID": "A1", "shapeName": "A"}, "geometry": sq(0)},
        {"type": "Feature", "properties": {"shapeID": "B1", "shapeName": "B"}, "geometry": sq(10)}]}))
    e = make_vector_sample(str(src), (0.2, 0.2, 0.8, 0.8), tmp_path, "gb-adm2-test", "district", "x" * 64, "u", "l", "gb-adm2-")
    assert e["provenance"]["n_features"] == 1 and e["provenance"]["authoritative_for_census_join"] is False
    assert parse_entry(e, tmp_path).dataset_id == "gb-adm2-test"
    bow = {"type": "Polygon", "coordinates": [[(0, 0), (1, 1), (1, 0), (0, 1), (0, 0)]]}
    src.write_text(json.dumps({"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"shapeID": "C", "shapeName": "C"}, "geometry": bow}]}))
    with pytest.raises(ValueError, match="invalid"):
        make_vector_sample(str(src), (0, 0, 1, 1), tmp_path, "x", "district", "x" * 64, "u", "l", "p-")
