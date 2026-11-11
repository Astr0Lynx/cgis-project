# Tests for census.py (PCA district parsing and the district join report).
import pytest
import openpyxl

from cgis_data import census as C
from cgis_data import validate as V


# Writes a minimal PCA workbook with the given rows.
def _xlsx(tmp_path, rows):
    p = tmp_path / "pca.xlsx"
    wb = openpyxl.Workbook(); ws = wb.active
    ws.append(["State", "District", "Subdistt", "Town/Village", "Ward", "EB", "Level", "Name", "TRU", "No_HH", "TOT_P"])
    for r in rows:
        ws.append(r)
    wb.save(p)
    return str(p)


# District Total rows are read and joined 1:1 with polygons; totals and invalid names reported.
def test_pca_district_rows_and_join(tmp_path):
    rows = [["00", "000", "00000", "000000", "0000", "000000", "India", "India", "Total", 1, 100],
            ["28", "532", "00000", "000000", "0000", "000000", "DISTRICT", "Adilabad", "Total", 1, 60],
            ["28", "532", "00000", "000000", "0000", "000000", "DISTRICT", "Adilabad", "Rural", 1, 40],
            ["09", "146", "00000", "000000", "0000", "000000", "DISTRICT", "Agra", "Total", 1, 40]]
    pca = C.read_pca_districts(_xlsx(tmp_path, rows))
    assert set(pca) == {"532", "146"} and pca["532"]["tot_p"] == 60           # Rural row ignored
    rep = C.join_report(pca, [532, 146, 0], [28, 9, 0], ["Cuddalore"], official_total=100)
    assert rep["matched"] == 2 and rep["polygon_only"] == ["0"] and rep["census_only"] == [] and rep["state_code_mismatches"] == 0
    assert rep["official_total_matches"] is True and rep["invalid_geometries"] == ["Cuddalore"]


# State-code mismatches, duplicate polygon codes and census-only codes are detected.
def test_join_detects_state_mismatch_duplicates_and_missing(tmp_path):
    pca = {"1": {"state": 5, "name": "A", "tot_p": 1}, "2": {"state": 5, "name": "B", "tot_p": 1}}
    assert C.join_report(pca, [1, 2], [5, 6], [])["state_code_mismatches"] == 1
    with pytest.raises(V.ValidationIssue):
        C.join_report(pca, [1, 1, 2], [5, 5, 5], [])                       # duplicate polygon code
    assert C.join_report(pca, [1], [5], [])["census_only"] == ["2"]


# A duplicated PCA district code is rejected.
def test_duplicate_pca_district_rejected(tmp_path):
    r = ["28", "532", "00000", "000000", "0000", "000000", "DISTRICT", "X", "Total", 1, 1]
    with pytest.raises(ValueError, match="duplicate"):
        C.read_pca_districts(_xlsx(tmp_path, [r, r]))
