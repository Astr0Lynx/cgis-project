"""Evaluation figures: national residual maps (coarse support) and representative 100 m zooms."""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.colors import LogNorm, SymLogNorm  # noqa: E402
from rasterio.windows import Window  # noqa: E402

from . import pipeline as P  # noqa: E402


def _extent(target, s: int, shape) -> list[float]:
    t = target.transform
    return [t.c, t.c + shape[1] * s * t.a, t.f + shape[0] * s * t.e, t.f]


def year_figures(cfg, year: int, target, grids: dict, out_dir: Path) -> list[str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    files = []
    s = max(grids) if grids else None
    tag = "SYNTHETIC " if cfg.synthetic else ""
    if s is not None:
        g = grids[s]
        m = g["n"] > 0
        l1 = np.where(m, g["l1"], np.nan)
        ref = np.where(m, g["ref"], np.nan)
        res = l1 - ref
        ext = _extent(target, s, l1.shape)
        fig, ax = plt.subplots(1, 3, figsize=(16, 6), constrained_layout=True)
        vmax = np.nanpercentile(np.where(l1 > 0, l1, np.nan), 99.5) if np.any(l1 > 0) else 1
        for a, arr, title in ((ax[0], l1, f"Level 1 {year}"), (ax[1], ref, "GHS-POP reference")):
            im = a.imshow(np.where(arr > 0, arr, np.nan), extent=ext, norm=LogNorm(vmin=1, vmax=max(vmax, 2)),
                          cmap="magma", interpolation="nearest")
            a.set_title(f"{tag}{title}\n(people per {s}x{s}-cell block, ~{s/10:g} km)")
            fig.colorbar(im, ax=a, shrink=0.7, label="people / block")
        lim = np.nanpercentile(np.abs(res), 99) if np.isfinite(res).any() else 1
        im = ax[2].imshow(res, extent=ext, cmap="RdBu_r", interpolation="nearest",
                          norm=SymLogNorm(linthresh=max(lim / 100, 1), vmin=-lim, vmax=lim))
        ax[2].set_title(f"{tag}Residual Level 1 - GHS-POP {year}\n(red: Level 1 higher)")
        fig.colorbar(im, ax=ax[2], shrink=0.7, label="people / block")
        for a in ax:
            a.set_xlabel("lon")
            a.set_ylabel("lat")
        p = out_dir / f"{cfg.dataset_id(year)}_national_s{s}.png"
        fig.savefig(p, dpi=80)
        plt.close(fig)
        files.append(p.name)

    zoom_years = cfg.provenance_extra.get("zoom_years", [cfg.years[0], cfg.years[-1]])
    if year in zoom_years:
        for name, bbox in cfg.eval_zoom_regions.items():
            p = zoom_figure(cfg, year, target, name, bbox, out_dir)
            if p:
                files.append(p.name)
    return files


def zoom_figure(cfg, year: int, target, name: str, bbox, out_dir: Path) -> Path | None:
    import rasterio
    inv = ~target.transform
    c0, r0 = inv * (bbox[0], bbox[3])
    c1, r1 = inv * (bbox[2], bbox[1])
    c0, r0 = max(0, int(c0)), max(0, int(r0))
    c1, r1 = min(target.width, int(c1)), min(target.height, int(r1))
    if c1 <= c0 or r1 <= r0:
        return None
    h, w = r1 - r0, c1 - c0
    with rasterio.open(cfg.out_dir / "cog" / f"{cfg.dataset_id(year)}.tif") as ds:
        l1 = ds.read(1, window=Window(c0, r0, w, h)).astype(float)
        l1[l1 == ds.nodata] = np.nan
    wp = P.read_worldpop(cfg, year, r0, c0, h, w)
    wp[wp == -99999.0] = np.nan
    ref = P.read_reference(cfg, year, r0, c0, h, w)
    ref = np.where(np.isnan(l1), np.nan, ref)
    t = target.transform
    ext = [t.c + c0 * t.a, t.c + c1 * t.a, t.f + r1 * t.e, t.f + r0 * t.e]
    vmax = np.nanpercentile(np.where(l1 > 0, l1, np.nan), 99.5) if np.nanmax(np.nan_to_num(l1)) > 0 else 1
    fig, ax = plt.subplots(1, 3, figsize=(16, 5.5), constrained_layout=True)
    tag = "SYNTHETIC " if cfg.synthetic else ""
    for a, arr, title in ((ax[0], wp, "WorldPop input (unconstrained)"), (ax[1], l1, "Level 1 (redistributed)"),
                          (ax[2], ref, "GHS-POP reference")):
        im = a.imshow(np.where(arr > 0, arr, np.nan), extent=ext, cmap="magma", interpolation="nearest",
                      norm=LogNorm(vmin=max(vmax / 1e4, 0.01), vmax=max(vmax, 0.02)))
        a.set_title(f"{tag}{title}\n{name}, {year}, ~100 m cells")
    fig.colorbar(im, ax=ax, shrink=0.8, label="people per cell")
    p = out_dir / f"{cfg.dataset_id(year)}_zoom_{name}.png"
    fig.savefig(p, dpi=70)
    plt.close(fig)
    return p
