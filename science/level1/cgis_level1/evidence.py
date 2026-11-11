# Copies the small, committable results of a Level 1 run (eval bundles, figures, run reports, manifest,
# spot checks, input inventory, benchmark) into science/results/level1/<label>/. Rasters stay in data/.
from __future__ import annotations

import json
import shutil
from pathlib import Path

from .config import Config
from .pipeline import atomic_json

RESULTS = Path(__file__).resolve().parents[2] / "results" / "level1"      # science/results/level1


# Collects one run's results into <dest>/<label>/ and returns the benchmark summary; used by `cli evidence`.
def collect(cfg: Config, label: str, dest: Path = RESULTS) -> dict:
    eval_dst = dest / label
    (eval_dst / "figures").mkdir(parents=True, exist_ok=True)
    rows = []
    cmp2011 = None
    for y in cfg.years:
        did = cfg.dataset_id(y)
        rp = cfg.out_dir / "reports" / f"{did}.run.json"
        ep = cfg.out_dir / "eval" / f"{did}.eval.json"
        row = {"year": y, "dataset_id": did}
        if rp.exists():
            r = json.loads(rp.read_text())
            atomic_json(eval_dst / f"{did}.run.json", r)
            rc, ra = r["resources"]["compute"], r["resources"]["assemble"]
            row.update({
                "status": r["status"], "checks": r["checks"], "sha256": r["output"]["sha256"],
                "cog_bytes": r["output"]["size_bytes"], "sum_people": r["mass_balance"]["sum_level1_out"],
                "rel_mass_error": r["mass_balance"]["rel_error"],
                "max_parent_rel_error": r["mass_balance"]["max_parent_rel_error"],
                "zero_weight_parents": r["mass_balance"]["parents_zero_weight"],
                "parents": r["mass_balance"]["parents"],
                "population_in_zero_weight_parents": r["mass_balance"]["population_in_zero_weight_parents"],
                "valid_cells": r["output"]["stats"]["valid_cells"],
                "extent_is_full_worldpop_grid": r["grid"].get("extent_is_full_worldpop_grid"),
                "compute_elapsed_s": rc["elapsed_s"], "assemble_elapsed_s": ra["elapsed_s"],
                "peak_rss_gib": max(rc["peak_rss_gib"], ra["peak_rss_gib"]), "workers": r["resources"]["workers"],
                "min_free_disk_gib": rc["min_free_disk_gib"], "chunk_bytes": r["resources"]["chunk_bytes"],
                "model_commit": r["software"]["commit"], "dirty_worktree": r["software"]["dirty_worktree"],
                "main_policy": r["main_policy"], "policy_2011_approval": r["policy_2011_approval"],
            })
            if r.get("policy_comparison_2011"):
                cmp2011 = {"year": y, "main_policy": r["main_policy"],
                           "approval": r["policy_2011_approval"], "temporal": r["temporal"],
                           "comparison": r["policy_comparison_2011"]}
        if ep.exists():
            e = json.loads(ep.read_text())
            atomic_json(eval_dst / f"{did}.eval.json", e)
            row["eval_elapsed_s"] = e["resources"]["elapsed_s"]
            row["eval_peak_rss_gib"] = e["resources"]["peak_rss_gib"]
            for f in e.get("figures", []):
                src = cfg.out_dir / "eval" / "figures" / f
                if src.exists():
                    shutil.copy2(src, eval_dst / "figures" / f)
        rows.append(row)
    if cmp2011:
        atomic_json(eval_dst / "policy_2011_comparison.json", cmp2011)
    inv = cfg.out_dir / "inputs_inventory.json"
    if inv.exists():
        shutil.copy2(inv, eval_dst / "inputs_inventory.json")
    spots = sorted((cfg.out_dir / "spot_checks").glob("*.json"))
    if spots:
        (eval_dst / "spot_checks").mkdir(exist_ok=True)
        for f in spots:
            shutil.copy2(f, eval_dst / "spot_checks" / f.name)
    man = cfg.out_dir / "manifest.json"
    if man.exists():
        shutil.copy2(man, eval_dst / "manifest.json")
    bench = {"label": label, "config": cfg.path.name, "config_sha256": cfg.sha256, "synthetic": cfg.synthetic,
             "years": rows,
             "totals": {"compute_s": sum(r.get("compute_elapsed_s", 0) for r in rows),
                        "assemble_s": sum(r.get("assemble_elapsed_s", 0) for r in rows),
                        "eval_s": sum(r.get("eval_elapsed_s", 0) for r in rows),
                        "max_peak_rss_gib": max([r.get("peak_rss_gib", 0) for r in rows] +
                                                [r.get("eval_peak_rss_gib", 0) for r in rows]),
                        "cog_bytes": sum(r.get("cog_bytes", 0) for r in rows)}}
    atomic_json(eval_dst / "benchmark.json", bench)
    return bench
