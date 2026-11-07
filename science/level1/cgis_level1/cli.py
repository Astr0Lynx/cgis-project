"""Command line: ``python -m cgis_level1 <command> --config <file.toml>``.

Commands
  make-synthetic   write deterministic SYNTHETIC fixtures
  run              produce Level 1 COGs + run reports + v1 manifest (restartable)
  evaluate         GHS-POP evaluation bundles (+ figures)
  manifest         rebuild manifest.json from per-year entries
  summary          print a compact table of run/eval results
  inventory        list every input with size, SHA-256, grid and licence
  evidence         copy eval bundles, figures, run reports, benchmarks into science/level1 (committable)
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from .config import load_config
from .grid import ensure_proj_data


def main(argv: list[str] | None = None) -> int:
    ensure_proj_data()
    ap = argparse.ArgumentParser(prog="python -m cgis_level1")
    ap.add_argument("--log-level", default="INFO")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("make-synthetic")
    s.add_argument("--out", required=True)
    for name in ("run", "evaluate", "manifest", "summary", "inventory", "evidence"):
        p = sub.add_parser(name)
        p.add_argument("--config", required=True)
        p.add_argument("--years", type=int, nargs="*")
        if name == "run":
            p.add_argument("--force", action="store_true", help="discard checkpoints and recompute")
            p.add_argument("--keep-chunks", action="store_true")
            p.add_argument("--max-tiles", type=int, help="debug: only the first N tiles (never a PASS)")
        if name == "evidence":
            p.add_argument("--label", required=True, help="e.g. india-full, sample-delhi, synthetic")
        if name == "inventory":
            p.add_argument("--out", default=None, help="default: <out_dir>/inputs_inventory.json")
        if name == "evaluate":
            p.add_argument("--figures", default=None, help="directory for PNG figures")
    a = ap.parse_args(argv)
    logging.basicConfig(level=a.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    if a.cmd == "make-synthetic":
        from .synthetic import make_synthetic
        print(json.dumps(make_synthetic(Path(a.out)), indent=1))
        return 0
    cfg = load_config(a.config)
    years = a.years or list(cfg.years)
    bad = [y for y in years if y not in cfg.years]
    if bad:
        ap.error(f"years {bad} not in config run.years")
    if a.cmd == "run":
        from .pipeline import RunError, run_year
        rc = 0
        for y in years:
            try:
                rep = run_year(cfg, y, force=a.force, keep_chunks=a.keep_chunks, max_tiles=a.max_tiles)
                print(json.dumps({"year": y, "status": rep["status"], "sha256": rep["output"]["sha256"],
                                  "rel_mass_error": rep["mass_balance"]["rel_error"],
                                  "peak_rss_gib": rep["resources"]["compute"]["peak_rss_gib"],
                                  "elapsed_s": rep["resources"]["compute"]["elapsed_s"]}))
            except RunError as exc:
                logging.error("%s", exc)
                rc = 1
        return rc
    if a.cmd == "evaluate":
        from .evaluate import evaluate_year
        figs = Path(a.figures) if a.figures else cfg.out_dir / "eval" / "figures"
        for y in years:
            b = evaluate_year(cfg, y, figs)
            s1 = b["supports"]["s1"]["level1"]
            print(json.dumps({"year": y, "pearson_100m": s1.get("pearson"),
                              "baseline_pearson_100m": b["supports"]["s1"]["worldpop_baseline"].get("pearson"),
                              "mae_100m": s1.get("mae")}))
        return 0
    if a.cmd == "evidence":
        from .evidence import collect
        b = collect(cfg, a.label)
        print(json.dumps(b["totals"]))
        return 0
    if a.cmd == "inventory":
        from .inventory import build_inventory
        inv = build_inventory(cfg, Path(a.out) if a.out else cfg.out_dir / "inputs_inventory.json")
        print(json.dumps({k: inv[k] for k in ("n_files", "total_bytes", "missing", "free_disk_bytes_at_data_root")}))
        return 0 if not inv["missing"] else 1
    if a.cmd == "manifest":
        from .pipeline import write_manifest
        print(write_manifest(cfg))
        return 0
    if a.cmd == "summary":
        for y in years:
            rp = cfg.out_dir / "reports" / f"{cfg.dataset_id(y)}.run.json"
            ep = cfg.out_dir / "eval" / f"{cfg.dataset_id(y)}.eval.json"
            row = {"year": y}
            if rp.exists():
                r = json.loads(rp.read_text())
                row.update(status=r["status"], sum=r["mass_balance"]["sum_level1_out"],
                           rel_err=r["mass_balance"]["rel_error"], sha=r["output"]["sha256"][:12])
            if ep.exists():
                e = json.loads(ep.read_text())
                row.update(p100=e["supports"]["s1"]["level1"].get("pearson"))
            print(json.dumps(row))
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
