# Input inventory: every input file with size, SHA-256, grid, units and licence (machine-readable).

from __future__ import annotations

import datetime as dt
import glob
import shutil
from pathlib import Path

import rasterio

from .config import Config
from .pipeline import atomic_json, cached_sha256


# CRS, size, dtype, NoData, transform and bounds of a raster input (build_inventory).
def _raster_meta(p: Path) -> dict:
    with rasterio.open(p) as ds:
        return {"crs": ds.crs.to_string() if ds.crs else None, "width": ds.width, "height": ds.height,
                "dtype": ds.dtypes[0], "nodata": ds.nodata, "transform": list(ds.transform)[:6],
                "bounds": list(ds.bounds)}


# Lists every configured input with size, SHA-256, grid and licence and writes it to `out`.
# Used by cli `inventory` and tests.
def build_inventory(cfg: Config, out: Path) -> dict:
    cache = cfg.work_dir / "sha256_cache.json"
    items = []

    # Appends one file record.
    def add(kind, p: Path, units, extra=None):
        rec = {"kind": kind, "file": p.as_posix().replace(cfg.root.as_posix() + "/", ""),
               "size_bytes": p.stat().st_size, "sha256": cached_sha256(p, cache), "units": units}
        if p.suffix.lower() == ".tif":
            rec.update(_raster_meta(p))
        rec.update(extra or {})
        items.append(rec)

    missing = []
    for y in cfg.years:
        p = cfg.worldpop_path(y)
        if p.exists():
            add("worldpop_ppp", p, "people_per_cell", {"year": y, "dataset_id": f"india-worldpop-unconstrained-{y}"})
        else:
            missing.append(str(p))
    epochs = list(cfg.epochs) + ([2015] if cfg.policy_2011 == "interpolate_2015" or cfg.compare_2011_policies else [])
    for kind, units in (("total", "m2_per_cell"), ("nres", "m2_per_cell")):
        for e in epochs:
            files = sorted(glob.glob(cfg.ghsl_glob(kind, e)))
            if not files:
                missing.append(cfg.ghsl_glob(kind, e))
            for f in files:
                add(f"ghsl_built_s_{kind}", Path(f), units, {"epoch": e})
    for e in cfg.ghs_pop_epochs:
        files = sorted(glob.glob(cfg.ghsl_glob("pop", e)))
        if not files:
            missing.append(cfg.ghsl_glob("pop", e))
        for f in files:
            add("ghs_pop", Path(f), "people_per_cell", {"epoch": e})
    for name, spec in cfg.eval_zones.items():
        p = cfg.resolve(spec["path"])
        if p.exists():
            add("zones", p, "polygons", {"layer": name, "citation": spec.get("citation")})
        else:
            missing.append(str(p))
    total = sum(i["size_bytes"] for i in items)
    inv = {"generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
           "config": cfg.path.name, "config_sha256": cfg.sha256, "synthetic": cfg.synthetic,
           "n_files": len(items), "total_bytes": total, "missing": missing,
           "free_disk_bytes_at_data_root": shutil.disk_usage(cfg.root).free,
           "licenses": cfg.provenance_extra.get("licenses", {}),
           "upstream": cfg.provenance_extra.get("upstream", {}), "files": items}
    atomic_json(out, inv)
    return inv
