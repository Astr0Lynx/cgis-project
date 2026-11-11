# Builds a real regional sample delivery (WorldPop + GHSL windows + v1 manifest) from downloaded originals.
# Originals stay in data/incoming; outputs go to data/outputs/<sample>/ (git-ignored). Run from Python, e.g.
# build_sample(Path('data'), state, 'hyderabad-region', (77.9, 16.9, 79.1, 17.9), 2011, [(8, 26)]).
from __future__ import annotations

import json
import time
import zipfile
from pathlib import Path

import rasterio

from . import harmonize as H
from . import manifest as M
from . import sources as S
from . import validate as V
from .national import peak_rss_mb

NODATA = -99999.0


# Extracts the .tif of a GHSL tile zip once; used by build_sample.
def _extract_tile(zip_path: Path, outdir: Path) -> Path:
    outdir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as z:
        name = next(n for n in z.namelist() if n.lower().endswith(".tif"))
        dest = outdir / name
        if not dest.exists():
            z.extract(name, outdir)
    return dest


# Crops WorldPop, copies aligned GHSL windows, checks units and writes manifest + sample_report.json.
def build_sample(root: Path, state: dict, name: str, bbox: tuple, worldpop_year: int, tiles: list[tuple[int, int]],
                 out_manifest: bool = True) -> dict:
    t0 = time.time()
    by = {s.source_id: s for s in S.all_sources()}
    work, deliv = root / "work" / name, root / "outputs" / name
    (deliv / "rasters").mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)
    entries, report = [], {"sample": name, "bbox_wgs84": bbox, "steps": []}

    # 1) WorldPop window (native EPSG:4326 grid; counts untouched)
    wp_id = f"india-worldpop-unconstrained-{worldpop_year}"
    wp_src = Path(state[wp_id]["local_path"])
    wp_out = deliv / "rasters" / f"{wp_id}-{name}.tif"
    info = H.crop_to_bbox(str(wp_src), str(wp_out), bbox)
    with rasterio.open(wp_out) as ds:
        grid = dict(transform=ds.transform, width=ds.width, height=ds.height)
        wp_nodata = ds.nodata
    V.check_units("people_per_cell", str(wp_out))
    total, nv, nn = H.raster_sum(str(wp_out))
    report["worldpop"] = {"file": wp_out.name, "sum_people": total, "valid_cells": nv, "nodata_cells": nn, **info}
    entries.append(M.raster_entry(
        by[wp_id], f"rasters/{wp_out.name}", wp_out,
        processing_steps=[f"windowed crop (no resampling) to bbox {bbox}; counts, mask and NoData unchanged"],
        upstream_checksums={Path(state[wp_id]["local_path"]).name: state[wp_id]["sha256"]},
        geographic_target=f"sample:{name}", dataset_id=f"{wp_id}-{name}"))

    # 2) GHSL 4326_3ss tiles: same lattice as WorldPop -> aligned window copy, NO resampling (values/mask unchanged)
    cell_max = float(H.cell_area_m2_rows(grid["transform"], grid["height"]).max())
    for sid in [f"india-ghsl-built-{k}-{e}" for k in ("total", "nres") for e in S.GHSL_EPOCHS]:
        src = by[sid]
        tdir = root / "incoming" / sid / "tiles"
        tifs = [_extract_tile(next(tdir.glob(f"*_R{r}_C{c}.zip")), work / "ghsl_native" / sid) for r, c in tiles]
        if len(tifs) != 1:
            raise NotImplementedError("sample builder handles a single-tile sample; use a VRT for multi-tile")
        out = deliv / "rasters" / f"{sid}-{name}.tif"
        rep = H.extract_aligned_window(str(tifs[0]), str(out), grid["transform"], grid["width"], grid["height"])
        V.check_units("m2_per_cell", str(out), cell_area_m2=cell_max)
        total_m2, nvalid, _ = H.raster_sum(str(out))
        report.setdefault("ghsl", {})[sid] = {**rep, "sum_m2": total_m2, "valid_cells": nvalid,
                                             "max_cell_area_m2": cell_max}
        entries.append(M.raster_entry(
            src, f"rasters/{out.name}", out,
            processing_steps=[
                "extract GHSL 4326_3ss tile zip (tile checksums in upstream_checksums)",
                f"lattice_offset(WorldPop window, tile) = ({rep['offset_cols']},{rep['offset_rows']}) whole pixels; "
                f"aligned window copy onto the WorldPop window {grid['width']}x{grid['height']} - no resampling, "
                "uint16 values and NoData 65535 unchanged"],
            upstream_checksums={k: v["sha256"] for k, v in state[sid].get("tiles", {}).items() if "sha256" in v and "4326_3ss" in k},
            geographic_target=f"sample:{name}", dataset_id=f"{sid}-{name}"))

    report["elapsed_s"] = round(time.time() - t0, 1)
    report["peak_rss_mb"] = peak_rss_mb()
    if out_manifest:
        M.write_manifest(deliv / "manifest.json", entries)
    (deliv / "sample_report.json").write_text(json.dumps(report, indent=2, default=str) + "\n")
    return report
