"""Render Markdown tables for HANDOFF_REPORT.md / eval/README.md from committed evidence JSON.

    python science/level1/scripts/handoff_tables.py [label=india-full]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]


def f(v, nd=4):
    if v is None:
        return "—"
    if isinstance(v, float):
        if abs(v) >= 1e6:
            return f"{v:,.0f}"
        if v != 0 and abs(v) < 1e-3:
            return f"{v:.1e}"
        return f"{v:.{nd}f}"
    return str(v)


def main() -> None:
    label = sys.argv[1] if len(sys.argv) > 1 else "india-full"
    b = json.loads((PKG / "benchmarks" / f"{label}.json").read_text())
    ev = PKG / "eval" / label
    print("### Outputs\n")
    print("| Year | dataset_id | status | SHA-256 (COG) | size GB | people (sum) | rel. mass err | max parent rel. err | full grid |")
    print("|---|---|---|---|---|---|---|---|---|")
    for r in b["years"]:
        print(f"| {r['year']} | `{r['dataset_id']}` | {r.get('status')} | `{r.get('sha256')}` | "
              f"{r.get('cog_bytes', 0) / 1e9:.2f} | {r.get('sum_people', 0):,.0f} | {f(r.get('rel_mass_error'))} | "
              f"{f(r.get('max_parent_rel_error'))} | {r.get('extent_is_full_worldpop_grid')} |")
    print("\n### Resources (16 GB target; budget 12 GiB for the process tree)\n")
    print("| Year | compute s | COG assembly s | eval s | peak RSS GiB (run) | peak RSS GiB (eval) | workers | min free disk GiB |")
    print("|---|---|---|---|---|---|---|---|")
    for r in b["years"]:
        print(f"| {r['year']} | {f(r.get('compute_elapsed_s'),1)} | {f(r.get('assemble_elapsed_s'),1)} | "
              f"{f(r.get('eval_elapsed_s'),1)} | {f(r.get('peak_rss_gib'),2)} | {f(r.get('eval_peak_rss_gib'),2)} | "
              f"{r.get('workers')} | {f(r.get('min_free_disk_gib'),1)} |")
    t = b["totals"]
    print(f"\nTotals: compute {t['compute_s']/60:.1f} min, assembly {t['assemble_s']/60:.1f} min, "
          f"evaluation {t['eval_s']/60:.1f} min, max peak RSS {t['max_peak_rss_gib']:.2f} GiB, "
          f"COGs {t['cog_bytes']/1e9:.2f} GB.\n")
    print("### Redistribution diagnostics\n")
    print("| Year | parents | zero-weight parents | % parents | population in zero-weight parents | % population |")
    print("|---|---|---|---|---|---|")
    for r in b["years"]:
        if "parents" in r:
            print(f"| {r['year']} | {r['parents']:,} | {r['zero_weight_parents']:,} | "
                  f"{100 * r['zero_weight_parents'] / r['parents']:.1f} | {r['population_in_zero_weight_parents']:,.0f} | "
                  f"{100 * r['population_in_zero_weight_parents'] / r['sum_people']:.1f} |")
    print("\n### Evaluation vs GHS-POP (Level 1 | WorldPop baseline)\n")
    print("| Year | ref | 100 m Pearson | 100 m Spearman* | 100 m MAE | 100 m RMSE | 1 km Pearson | 10 km Pearson | district Pearson | district Spearman | rel. total bias |")
    print("|---|---|---|---|---|---|---|---|---|---|---|")
    for r in b["years"]:
        p = ev / f"{r['dataset_id']}.eval.json"
        if not p.exists():
            continue
        e = json.loads(p.read_text())
        s = e["supports"]
        s1, smp = s["s1"], s["s1"].get("sample", {})
        d = s.get("zone_district_adm2", {})

        def pair(a, b_, k, nd=3):
            return f"{f(a.get(k), nd)} \\| {f(b_.get(k), nd)}"
        print(f"| {r['year']} | {e['reference']['reference_temporal']} | "
              f"{pair(s1['level1'], s1['worldpop_baseline'], 'pearson')} | "
              f"{pair(smp.get('level1', {}), smp.get('worldpop_baseline', {}), 'spearman')} | "
              f"{pair(s1['level1'], s1['worldpop_baseline'], 'mae')} | "
              f"{pair(s1['level1'], s1['worldpop_baseline'], 'rmse')} | "
              f"{f(s['s10']['level1'].get('pearson'), 3)} | {f(s['s100']['level1'].get('pearson'), 3)} | "
              f"{f(d.get('level1', {}).get('pearson'), 3)} | {f(d.get('level1', {}).get('spearman'), 3)} | "
              f"{f(s1['level1'].get('rel_bias_total'), 3)} |")
    print("\n\\* Spearman at 100 m on the deterministic systematic 1/289 sample. At 1 km and 10 km Level 1 equals "
          "WorldPop by construction, so only the Level 1 value is shown.\n")
    print("### Coverage inside India (ADM1 union)\n")
    print("| Year | GHS-POP inside India | ... outside Level 1 mask | fraction | Level 1 outside all ADM1 |")
    print("|---|---|---|---|---|")
    for r in b["years"]:
        p = ev / f"{r['dataset_id']}.eval.json"
        if p.exists():
            c = json.loads(p.read_text())["coverage"].get("within_state_adm1", {})
            if c:
                print(f"| {r['year']} | {c['reference_total_inside_zones']:,.0f} | "
                      f"{c['reference_inside_zones_outside_level1_mask']:,.0f} | {c['fraction_outside_mask']:.4f} | "
                      f"{c['level1_outside_all_zones']:,.0f} |")


if __name__ == "__main__":
    main()
