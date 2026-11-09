from dataclasses import replace
import json

from affine import Affine
import numpy as np
import pytest
import rasterio
from rasterio.windows import Window
from rio_cogeo.cogeo import cog_validate
from shapely.geometry import Polygon, box

from ingestion.cli import main as ingest
from ingestion.paths import sha256_file
from science.level2.cgis_level2 import grid
from science.level2.cgis_level2.inputs import LandClass, Source, Town, read_json, read_legend, read_towns
from science.level2.cgis_level2.model import build_town
from science.level2.cgis_level2.pipeline import _method, _write_json, run
from science.level2.cgis_level2.synthetic import _write_raster, make_fixture


@pytest.fixture
def fixture_config(tmp_path):
    path = make_fixture(tmp_path / "inputs")
    return path, read_json(path)


@pytest.fixture
def golden(tmp_path):
    pixel = grid.PIXEL
    # A fine cell straddles the two coarse columns; the first column covers one half fine cell.
    affine = Affine(2 * pixel, 0, -1.5 * pixel, 0, -2 * pixel, pixel)
    _write_raster(tmp_path / "prior.tif", np.array([[4, 12]], dtype="float32"), affine, -99999)
    _write_raster(tmp_path / "classes.tif", np.array([[1, 2]], dtype="int16"),
                  Affine(pixel, 0, 0, 0, -pixel, pixel), -9999)
    legend = {1: LandClass(1, "settlement", "A", 1, 1), 2: LandClass(2, "crop", "B", .5, 0)}
    town = Town("A", "town-a", "A", "west", "urban", 100, box(0, 0, 2 * pixel, pixel))
    source = Source(tmp_path / "classes.tif", {"kind": "raster"})

    def compute(primary="hard", settlement=town, lulc=source, mapping=legend, stage="result"):
        target = tmp_path / stage
        target.mkdir()
        with rasterio.open(tmp_path / "prior.tif") as src:
            report = build_town(target, settlement, [], src, 1, lulc, mapping,
                                {"primary": primary, "minimum_lulc_coverage": 0,
                                 "minimum_level1_coverage": 0}, lambda: None)
        with rasterio.open(target / "population.tif") as src:
            values = src.read(1, masked=True)
        with rasterio.open(target / "comparison.tif") as src:
            comparison = src.read(masked=True)
        return report, values, comparison

    return tmp_path, affine, compute, town, source


@pytest.mark.parametrize("model,expected", [("hard", [100, 0]), ("soft", [400 / 7, 300 / 7])])
def test_golden_weighting_and_common_support(golden, model, expected):
    _, _, compute, _, _ = golden
    report, values, comparison = compute(model)
    assert values[0].tolist() == pytest.approx(expected)
    assert report["stored_level2_people"] == pytest.approx(100)
    assert report["level1_people_on_census_polygon_observed_support"] == pytest.approx(5)
    assert comparison[0, 0].tolist() == pytest.approx([.5, 4.5])
    assert comparison[1, 0].tolist() == pytest.approx([expected[0] / 2, expected[0] / 2 + expected[1]])
    assert comparison[2, 0].tolist() == pytest.approx((comparison[1, 0] - comparison[0, 0]).tolist())
    assert report["sensitivity"]["people_shifted_between_soft_and_hard"] == pytest.approx(300 / 7)


def test_zero_prior_falls_back_only_on_eligible_area(golden):
    folder, affine, compute, _, _ = golden
    _write_raster(folder / "prior.tif", np.array([[0, 0]], dtype="float32"), affine, -99999)
    report, values, _ = compute()
    assert values[0].tolist() == [100, 0]
    assert report["sensitivity"]["area_class_fallback"] == {"soft": True, "hard": True}
    assert report["matched_level1_comparison"]["relative_total_delta"] is None


@pytest.mark.parametrize("code,message", [(2, "no admissible"), (-9999, "no owned or classified"), (7, "unknown LULC")])
def test_no_hard_eligible_or_missing_land_cover(golden, code, message):
    folder, _, compute, _, _ = golden
    _write_raster(folder / "classes.tif", np.array([[code, code]], dtype="int16"),
                  Affine(grid.PIXEL, 0, 0, 0, -grid.PIXEL, grid.PIXEL), -9999)
    with pytest.raises(ValueError, match=message):
        compute()


def test_valid_zero_population_and_nodata(golden):
    folder, affine, compute, town, _ = golden
    _write_raster(folder / "prior.tif", np.array([[-99999, 12]], dtype="float32"), affine, -99999)
    _write_raster(folder / "classes.tif", np.array([[2, -9999]], dtype="int16"),
                  Affine(grid.PIXEL, 0, 0, 0, -grid.PIXEL, grid.PIXEL), -9999)
    report, values, comparison = compute(settlement=replace(town, population=0))
    assert values[0, 0] == 0
    assert bool(values.mask[0, 1])
    assert bool(comparison.mask[0, 0, 0])
    assert report["valid_fine_cells"] == 1
    assert report["level1_coverage_of_census_polygon"] == pytest.approx(.75)
    assert report["common_coverage_of_census_polygon"] == pytest.approx(.25)


def test_all_prior_nodata_cannot_produce_a_comparison(golden):
    folder, affine, compute, _, _ = golden
    _write_raster(folder / "prior.tif", np.array([[-99999, -99999]], dtype="float32"), affine, -99999)
    with pytest.raises(ValueError, match="no common Level 1"):
        compute()


def test_negative_prior_is_rejected(golden):
    folder, affine, compute, _, _ = golden
    _write_raster(folder / "prior.tif", np.array([[-2, 0]], dtype="float32"), affine, -99999)
    with pytest.raises(ValueError, match="negative or nonfinite"):
        compute()


def test_fractional_edges_holes_and_deterministic_ownership():
    p = grid.PIXEL
    area = grid.CELL_AREA
    a = box(0, 0, 2.5 * p, 2 * p).difference(box(0.25 * p, 0.25 * p, 0.75 * p, .75 * p))
    b = box(2.5 * p, 0, 4 * p, 2 * p)
    gr = grid.FineGrid(Affine(p, 0, 0, 0, -p, 2 * p), 4, 2)
    window = Window(0, 0, 4, 2)
    aa = grid.owned_cells(gr, window, "A", a, [("B", b)])
    bb = grid.owned_cells(gr, window, "B", b, [("A", a)])
    assert set(zip(aa.rows, aa.cols)).isdisjoint(zip(bb.rows, bb.cols))
    assert aa.area.tolist() == pytest.approx([area, area, area / 2, 0.75 * area, area, area / 2])
    assert bb.area.tolist() == [area, area]
    no_cell = grid.owned_cells(gr, window, "B", box(2.5 * p, 0, 3 * p, 2 * p), [("A", a)])
    assert len(no_cell.area) == 0


def test_invalid_polygon_and_crs(golden):
    folder, _, compute, _, _ = golden
    with pytest.raises(ValueError, match="invalid"):
        grid.polygon_in_grid(Polygon([(0, 0), (1, 1), (0, 1), (1, 0), (0, 0)]), grid.CRS)
    with rasterio.open(folder / "prior.tif", "r+") as src:
        src.crs = "EPSG:3857"
    with pytest.raises(ValueError, match="requires EPSG:4326 or EPSG:6933"):
        compute()


@pytest.mark.parametrize("defect,match", [
    ("duplicate", "duplicate census_id"),
    ("missing", "missing PCA total"),
    ("name", "disagrees with PCA"),
    ("overlap", "overlapping Census"),
    ("missing-polygon", "missing PCA total or polygon"),
])
def test_census_join_failures(fixture_config, defect, match):
    config_path, raw = fixture_config
    sources = raw["sources"]
    base = config_path.parent
    pca = base / "pca.csv"
    selection = base / "selected.csv"
    boundaries = base / "study_areas.geojson"
    if defect in ("duplicate", "missing", "name"):
        rows = pca.read_text().splitlines()
        updates = {"duplicate": rows + [rows[-1]], "missing": rows[:2],
                   "name": [s.replace("Fixture Beta", "Wrong name") for s in rows]}
        pca.write_text("\n".join(updates[defect]) + "\n")
    elif defect == "overlap":
        document = read_json(boundaries)
        document["features"][1]["geometry"] = document["features"][0]["geometry"]
        _write_json(boundaries, document)
    else:
        document = read_json(boundaries)
        document["features"].pop()
        _write_json(boundaries, document)
    assert sources["pca"]["path"] == pca.name
    with pytest.raises(ValueError, match=match):
        read_towns(Source(selection, {}), Source(pca, {}), Source(boundaries, {}), True)


def test_legend_codes_and_approved_mode(fixture_config):
    path, raw = fixture_config
    legend = Source(path.parent / "legend.json", {})
    assert read_legend(legend, raw["method"])[2].label == "Agriculture"
    raw["method"]["class_weights"]["Wrong name"] = raw["method"]["class_weights"].pop("Agriculture")
    with pytest.raises(ValueError, match="legend code/label"):
        read_legend(legend, raw["method"])
    with pytest.raises(ValueError, match="D-L2-01"):
        _method(raw, False)
    raw["method"].update(approval_status="approved", approval_reference="test", primary="soft")
    with pytest.raises(ValueError, match="D-L2-01"):
        _method(raw, False)


def test_vector_land_cover_categorical(golden):
    import fiona

    folder, _, compute, _, _ = golden
    p = grid.PIXEL
    path = folder / "classes.gpkg"
    with fiona.open(path, "w", driver="GPKG", crs=grid.CRS, layer="land",
                    schema={"geometry": "Polygon", "properties": {"CLASS": "str"}}) as dst:
        for left, right, code in [(0, p, "A"), (p, 2 * p, "B")]:
            dst.write({"geometry": box(left, 0, right, p).__geo_interface__, "properties": {"CLASS": code}})
    result, values, _ = compute(lulc=Source(path, {"kind": "vector", "layer": "land", "class_field": "CLASS"}))
    assert values[0].tolist() == [100, 0]
    assert result["lulc_coverage_of_census_polygon"] == 1


def test_manifest_cog_retry_and_checkpoint_integrity(fixture_config, capsys):
    path, raw = fixture_config
    manifest = run(path)
    entries = read_json(manifest)["datasets"]
    assert len(entries) == 2
    assert ingest(["validate", "--manifest", str(manifest)]) == 0
    logged = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [entry["outcome"] for entry in logged] == ["validated", "validated"]
    for entry in entries:
        cog = manifest.parent / entry["file"]
        assert entry["dataset_id"].startswith("synthetic-india-l2-pop-2011-")
        assert entry["provenance"]["group_id"] == "synthetic-india-level2-2011"
        assert entry["expect"]["resolution"] == [23.5, 23.5]
        assert entry["checksum_sha256"] == sha256_file(cog)
        assert cog_validate(cog, strict=True, quiet=True)[0]
    old = manifest.read_bytes()
    assert run(path).read_bytes() == old
    assert all(t["resumed"] for t in read_json(manifest.parent / "run.json")["towns"])
    with (manifest.parent / entries[0]["file"]).open("ab") as stream:
        stream.write(b"corruption")
    with pytest.raises(ValueError, match="checkpoint integrity failure"):
        run(path)
    assert manifest.read_bytes() == old


def test_checksummed_source_changes_fail_before_publication(fixture_config):
    path, _ = fixture_config
    with (path.parent / "lulc.tif").open("ab") as stream:
        stream.write(b"corruption")
    with pytest.raises(ValueError, match="checksum mismatch"):
        run(path)
    assert not (path.parent / "outputs/manifest.json").exists()


def test_tiling_independence(fixture_config, monkeypatch):
    path, raw = fixture_config
    first = run(path)
    raw["output_dir"] = "different-blocks"
    _write_json(path, raw)
    monkeypatch.setattr(grid, "BLOCK", 79)
    second = run(path)
    for a, b in zip(read_json(first)["datasets"], read_json(second)["datasets"]):
        with rasterio.open(first.parent / a["file"]) as aa, rasterio.open(second.parent / b["file"]) as bb:
            assert np.array_equal(aa.read_masks(1), bb.read_masks(1))
            assert np.allclose(aa.read(1), bb.read(1), atol=1e-7, rtol=1e-6)
