# Per-town evaluation figure: Level 2 population, Level 2 minus Level 1 residual and class sensitivity bars.

from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
import numpy as np
import rasterio


# Reads `band` downsampled to at most 768 px on the long side (masked); used by plot_town for both panels.
def _preview(src, band):
    scale = min(1, 768 / max(src.width, src.height))
    return src.read(band, masked=True, out_shape=(max(1, round(src.height * scale)),
                                                   max(1, round(src.width * scale))))


# Writes <stage>/evaluation.png from population.tif, comparison.tif and the town report; used by pipeline.run.
def plot_town(stage, report, synthetic):
    fig = Figure(figsize=(12, 4.4), layout="constrained")
    FigureCanvasAgg(fig)
    fine, residual, classes = fig.subplots(1, 3, gridspec_kw={"width_ratios": [1, 1, 1.45]})
    with rasterio.open(stage / "population.tif") as src:
        values = _preview(src, 1)
    pop = fine.imshow(values, cmap="viridis", interpolation="nearest")
    fig.colorbar(pop, ax=fine, shrink=.7, label="people / 23.5 m cell")
    fine.set_title("Level 2 population")
    with rasterio.open(stage / "comparison.tif") as src:
        values = _preview(src, 3)
        bounds = src.bounds
        coords = (bounds.left, bounds.right, bounds.bottom, bounds.top)
    observed = values.compressed()
    limit = float(np.max(np.abs(observed))) if observed.size else 0
    limit = limit or 1
    difference = residual.imshow(values, extent=coords, cmap="RdBu_r", vmin=-limit, vmax=limit,
                                 interpolation="nearest")
    fig.colorbar(difference, ax=residual, shrink=.7, label="people / native cell intersection")
    residual.set_title("Level 2 minus Level 1")
    for axis in (fine, residual):
        axis.set_xticks([])
        axis.set_yticks([])
    records = [item for item in report["sensitivity"]["classes"] if item["area_m2"] > 0]
    records.sort(key=lambda item: -item["area_m2"])
    shown = records[:8]
    y = np.arange(len(shown))
    classes.barh(y - .17, [item["people_by_model"]["soft"] or 0 for item in shown],
                 height=.32, color="#225ea8", label="Soft weights")
    hard = report["sensitivity"]["model_status"]["hard"] == "available"
    if hard:
        classes.barh(y + .17, [item["people_by_model"]["hard"] or 0 for item in shown],
                     height=.32, color="#e6550d", label="Hard mask")
    classes.set_yticks(y, [item["label"][:25] for item in shown], fontsize=8)
    classes.invert_yaxis()
    classes.set_title("Largest LULC classes by area" if len(records) > len(shown) else "Class sensitivity")
    classes.set_xlabel("Allocated people" if hard else "Allocated people (hard mask infeasible)")
    classes.legend(fontsize=8)
    prefix = "SYNTHETIC  |  " if synthetic else ""
    fig.suptitle(f"{prefix}{report['name']}  ·  2011  ·  {report['primary_method']} method")
    fig.savefig(stage / "evaluation.png", dpi=125)
