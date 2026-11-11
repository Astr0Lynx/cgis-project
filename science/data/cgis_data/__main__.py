# CLI: python -m cgis_data {inventory|probe|fetch|tiles|national|census-join|grids}.
# Writes provenance records to science/results/data/ and downloads to the git-ignored data/incoming/.
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import fetch as F
from . import sources as S

REPO = Path(__file__).resolve().parents[3]
RESULTS = REPO / "science" / "results" / "data"   # committed provenance records and integrity reports
STATE = RESULTS / "source_state.json"
DEFAULT_ROOT = REPO / "data"      # git-ignored


# Reads the shared download state (sizes, sha256, local paths); used by every subcommand in main().
def _state():
    return F.read_state(STATE)


# Parses the subcommand and runs it; entry point of `python -m cgis_data`.
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="cgis_data")
    sub = ap.add_subparsers(dest="cmd", required=True)
    inv = sub.add_parser("inventory", help="write source_inventory.json from registry + state")
    inv.add_argument("--out", default=str(RESULTS / "source_inventory.json"))
    pr = sub.add_parser("probe", help="HEAD every available source URL and record sizes (no downloads)")
    fe = sub.add_parser("fetch", help="resumable download of unrestricted sources")
    fe.add_argument("source_ids", nargs="+")
    fe.add_argument("--root", default=str(DEFAULT_ROOT))
    nat = sub.add_parser("national", help="national integrity report -> science/results/data/national_validation.json")
    nat.add_argument("--root", default=str(DEFAULT_ROOT))
    nat.add_argument("--step", type=int, default=16)
    sub.add_parser("census-join", help="real Census PCA district totals <-> Census-coded polygons -> science/results/data/census_district_join.json")
    sub.add_parser("grids", help="lattice/shape report for the downloaded WorldPop years")
    ti = sub.add_parser("tiles", help="fetch only the GHSL tiles intersecting a bbox (or India)")
    ti.add_argument("source_ids", nargs="+")
    ti.add_argument("--bbox", nargs=4, type=float, metavar=("W", "S", "E", "N"))
    ti.add_argument("--india", action="store_true")
    ti.add_argument("--root", default=str(DEFAULT_ROOT))
    a = ap.parse_args(argv)

    if a.cmd == "probe":
        state = _state()
        for s in S.all_sources():
            if s.access != S.AVAILABLE or s.url.startswith("("):
                continue
            try:
                n = F.head_size(s.url)
            except Exception as e:           # record, don't hide
                print(f"{s.source_id}: PROBE FAILED {e}", file=sys.stderr); continue
            state.setdefault(s.source_id, {})["size_bytes"] = n
            print(f"{s.source_id}: {n}")
        with F._locked_state(STATE):
            cur = F.read_state(STATE)
            for k, v in state.items():
                cur.setdefault(k, {}).update({"size_bytes": v.get("size_bytes")} if v.get("size_bytes") else {})
            F.write_state_atomic(STATE, cur)
    elif a.cmd == "inventory":
        inv = S.build_inventory(_state())
        Path(a.out).write_text(json.dumps(inv, indent=2) + "\n")
        Path(a.out).with_name("SOURCE_INVENTORY.md").write_text(S.render_markdown(inv) + "\n")
        print("wrote", a.out)
    elif a.cmd == "fetch":
        by = {s.source_id: s for s in S.all_sources()}
        for sid in a.source_ids:
            res = F.fetch_source(by[sid], a.root, STATE,
                                 progress=None)
            print(sid, res)
    elif a.cmd == "national":
        import time as _t, rasterio
        from . import national as N
        from .validate import worldpop_grid_report
        st, root, t0 = _state(), Path(a.root), _t.time()
        wp = {y: st[f"india-worldpop-unconstrained-{y}"]["local_path"] for y in S.WORLDPOP_YEARS
              if st.get(f"india-worldpop-unconstrained-{y}", {}).get("local_path")}
        fp = N.load_footprint(str(root / "incoming/india-geoboundaries-adm1/geoBoundaries-IND-ADM1.geojson"))
        with rasterio.open(wp[2011]) as ds:
            ref = ds.transform
        rep = {"footprint": "geoBoundaries ADM1 union (DIAGNOSTIC, claimed extent; not authoritative)",
               "worldpop_years_present": sorted(wp), "worldpop_years_missing": sorted(set(S.WORLDPOP_YEARS) - set(wp)),
               "grids": worldpop_grid_report(wp), "duplicates": N.duplicate_year_check(st, S.WORLDPOP_YEARS),
               "coverage": {y: N.coverage_vs_footprint(p, fp, a.step) for y, p in sorted(wp.items())},
               "ghsl_tiles": N.check_ghsl_tiles(st, root, ref, [s.source_id for s in S.all_sources() if s.tiles])}
        rep["elapsed_s"], rep["peak_rss_mb"] = round(_t.time() - t0, 1), N.peak_rss_mb()
        (RESULTS / "national_validation.json").write_text(json.dumps(rep, indent=1, default=str) + "\n")
        print("wrote national_validation.json", rep["elapsed_s"], "s", rep["peak_rss_mb"], "MB")
    elif a.cmd == "census-join":
        import pyogrio
        from shapely.validation import explain_validity
        from . import census as Cn
        st = _state()
        pca = Cn.read_pca_districts(st["india-census-2011-pca-state-district"]["local_path"])
        g = pyogrio.read_dataframe(st["india-datameet-census2011-districts"]["local_path"])
        bad = [(str(g.DISTRICT[i]), explain_validity(geom)) for i, geom in enumerate(g.geometry) if not geom.is_valid]
        rep = Cn.join_report(pca, list(g.censuscode), list(g.ST_CEN_CD), [b[0] for b in bad], official_total=1_210_854_977)
        rep["invalid_geometry_reasons"] = bad
        rep["inputs"] = {k: {"sha256": st[k]["sha256"]} for k in ("india-census-2011-pca-state-district",
                                                                 "india-datameet-census2011-districts")}
        rep["polygon_only_names"] = [str(g.DISTRICT[i]) for i, c in enumerate(g.censuscode) if str(int(c)) in rep["polygon_only"]]
        (RESULTS / "census_district_join.json").write_text(json.dumps(rep, indent=1, default=str) + "\n")
        print(json.dumps({k: rep[k] for k in ("census_districts", "polygons", "matched", "census_only", "polygon_only", "polygon_only_names", "state_code_mismatches", "sum_tot_p", "official_total_matches", "invalid_geometries")}, default=str))
    elif a.cmd == "grids":
        from .validate import worldpop_grid_report
        st = _state()
        paths = {y: st[f"india-worldpop-unconstrained-{y}"]["local_path"] for y in S.WORLDPOP_YEARS
                 if st.get(f"india-worldpop-unconstrained-{y}", {}).get("local_path")}
        print(json.dumps(worldpop_grid_report(paths), indent=1))
    elif a.cmd == "tiles":
        from .ghsl_tiles import INDIA_BBOX, tiles_for_bbox, tiles_for_bbox_4326
        bbox = INDIA_BBOX if a.india else tuple(a.bbox)
        by = {s.source_id: s for s in S.all_sources()}
        for sid in a.source_ids:
            tl = (tiles_for_bbox_4326 if "4326" in by[sid].tiles["pattern"] else tiles_for_bbox)(bbox)
            for r in F.fetch_tiles(by[sid], tl, a.root, STATE):
                print(sid, r["row"], r["col"], r.get("size"), r.get("status") or ("skipped" if r["skipped"] else "downloaded"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
