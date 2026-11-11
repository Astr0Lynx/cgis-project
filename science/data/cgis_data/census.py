# Census 2011 PCA (district level) <-> Census-coded polygon join on real files; used by the `census-join` CLI.
from __future__ import annotations

import openpyxl

from .validate import check_census_join


# District 'Total' rows of the PCA workbook keyed by the national district code; used by the `census-join` CLI.
def read_pca_districts(xlsx_path: str) -> dict[str, dict]:
    ws = openpyxl.load_workbook(xlsx_path, read_only=True).active
    hdr = [str(c) for c in next(ws.iter_rows(min_row=1, max_row=1, values_only=True))]
    ix = {h: i for i, h in enumerate(hdr)}
    out = {}
    for r in ws.iter_rows(min_row=2, values_only=True):
        if r[ix["Level"]] == "DISTRICT" and r[ix["TRU"]] == "Total":
            code = str(int(r[ix["District"]]))
            if code in out:
                raise ValueError(f"duplicate PCA district code {code}")
            out[code] = {"state": int(r[ix["State"]]), "name": r[ix["Name"]], "tot_p": int(r[ix["TOT_P"]])}
    return out


# 1:1 join report on the national district code (unmatched, state mismatches, population total); used by `census-join`.
def join_report(pca: dict[str, dict], polygon_codes: list, polygon_states: list, invalid_geometry_names: list[str],
                official_total: int | None = None) -> dict:
    codes = [str(int(c)) for c in polygon_codes]
    j = check_census_join(pca.keys(), codes)              # raises on duplicates within either side (no silent de-duplication)
    st_mismatch = [c for c, s in zip(codes, polygon_states) if c in pca and pca[c]["state"] != int(s)]
    total = sum(v["tot_p"] for v in pca.values())
    return {"census_districts": len(pca), "polygons": len(codes), "matched": j["matched"],
            "census_only": j["census_only"], "polygon_only": j["polygon_only"],
            "state_code_mismatches": len(st_mismatch), "sum_tot_p": total,
            "official_total_matches": (total == official_total) if official_total is not None else None,
            "invalid_geometries": invalid_geometry_names,
            "note": "Join key = Census PCA 'District' == DataMeet 'censuscode' (national sequential). District level only."}
