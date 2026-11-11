# Level 2 run: validates the config and inputs, builds each town into a checksummed checkpoint folder, enforces
# the memory budget and writes run.json plus the v1 ingestion manifest.

import hashlib
import json
import os
import platform
import re
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter

import psutil
import rasterio

from ingestion.importer import sha256_file

from .figures import plot_town
from .inputs import MODELS, Source, read_json, read_legend, read_level1, read_towns
from .model import build_town

ARTIFACTS = ("population.tif", "comparison.tif", "evaluation.png", "report.json")
GIB = 1024 ** 3


# Canonical JSON text (sorted keys, no NaN); used for output files and fingerprints.
def _encode(data):
    return json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n"


# Atomically writes canonical JSON; used by run, synthetic.make_fixture and tests.
def _write_json(path, data):
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, encoding="utf-8", newline="\n",
                                     delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(_encode(data))
        except BaseException:
            stream.close()
            temporary.unlink(missing_ok=True)
            raise
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


# (commit, dirty) from CGIS_LEVEL2_BUILD_COMMIT or git; used by run for provenance and the real-mode check.
def _model_revision():
    build_commit = os.environ.get("CGIS_LEVEL2_BUILD_COMMIT", "")
    if re.fullmatch(r"[0-9a-f]{40}", build_commit):
        return build_commit, False
    source = Path(__file__).resolve().parent.parent
    git = ["git", "-C", str(source)]
    try:
        commit = subprocess.check_output([*git, "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL).strip()
        changes = subprocess.check_output(
            [*git, "status", "--porcelain", "--", "."], text=True).strip()
        return commit, bool(changes)
    except (OSError, subprocess.CalledProcessError):
        return None, True


# Peak resident memory of this process in bytes (Windows peak_wset or getrusage); used by run.
def _peak_rss(proc):
    memory = proc.memory_info()
    if hasattr(memory, "peak_wset"):
        return int(memory.peak_wset)
    import resource
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(peak * 1024 if platform.system() == "Linux" else peak)


# Validates the method block (real mode: approved hard mask, non-zero coverage cut-offs); used by run and tests.
def _method(cfg, synthetic):
    method = cfg["method"]
    if method["primary"] not in MODELS:
        raise ValueError("primary method must be soft or hard")
    if not synthetic and (method["primary"] != "hard" or method.get("approval_status") != "approved"
                          or not isinstance(method.get("approval_reference"), str)
                          or not method["approval_reference"].strip()):
        raise ValueError("real mode requires recorded D-L2-01 approval of the method and exact legend weights")
    for key in ("minimum_lulc_coverage", "minimum_level1_coverage"):
        value = method[key]
        if type(value) not in (float, int) or not 0 <= value <= 1 or (not synthetic and value <= 0):
            raise ValueError(f"invalid {key}")
    return method


# Verifies a town folder against its checkpoint.json and returns (report, checksums); used by run.
def _checkpoint(path, fingerprint):
    marker = read_json(path / "checkpoint.json")
    if (marker["fingerprint"] != fingerprint or set(marker["sha256"]) != set(ARTIFACTS)
            or any(sha256_file(path / name) != marker["sha256"][name] for name in ARTIFACTS)):
        raise ValueError(f"checkpoint integrity failure in {path.name}; preserve it and use a fresh output directory")
    return read_json(path / "report.json"), marker["sha256"]


# Builds the v1 manifest entry for one town; used by run.
def _manifest_entry(folder, output, report, checksums, cfg, provenance):
    synthetic = cfg["mode"] == "synthetic"
    prefix = "synthetic-" if synthetic else ""
    grid = report["grid"]
    return {
        "dataset_id": f"{prefix}india-l2-pop-2011-{report['slug']}", "version": cfg["version"],
        "title": f"{'SYNTHETIC ' if synthetic else ''}Level 2 population 2011 — {report['name']}",
        "description": "Census-constrained population on a 23.5 m equal-area target grid. "
                       "Spatial detail depends on the underlying LULC, Census boundaries and Level 1 inputs.",
        "kind": "raster", "product_level": "level2", "year": 2011, "units": "people_per_cell",
        "source": "SYNTHETIC Level 2 fixture" if synthetic else "Census 2011, Bhuvan LULC 2011–12 and Level 1",
        "file": (folder / "population.tif").relative_to(output).as_posix(),
        "checksum_sha256": checksums["population.tif"], "band": 1,
        "expect": {**{k: grid[k] for k in ("crs", "nodata", "resolution", "bounds", "width", "height")},
                   "dtype": "float32", "band_count": 1},
        "provenance": {**provenance, "synthetic": synthetic,
                       "group_id": f"{prefix}india-level2-2011", "census_id": report["census_id"],
                       "settlement": report["name"], "morphology": report["morphology"],
                       "region": report["region"], "grid": grid,
                       "evaluation_report": (folder / "report.json").relative_to(output).as_posix(),
                       "mass_balance": {k: report[k] for k in ("census_people", "stored_level2_people",
                                                              "absolute_census_error_people", "mass_tolerance_people")}},
    }


# Runs every selected town (or only one as a pilot) and returns the manifest or pilot report path; used by the
# CLI and tests.
def run(config_path, *, only=None):
    started = perf_counter()
    config_path = Path(config_path).resolve()
    root = config_path.parent
    cfg = read_json(config_path)
    if cfg["mode"] not in ("real", "synthetic") or type(cfg["version"]) is not int or cfg["version"] < 1:
        raise ValueError("configuration requires mode real/synthetic and positive integer version")
    synthetic = cfg["mode"] == "synthetic"
    method = _method(cfg, synthetic)
    model_commit, dirty = _model_revision()
    if not synthetic and (model_commit is None or dirty):
        raise ValueError("real run requires committed Level 2 code")
    limit = cfg.get("memory_limit_gib", 4)
    if type(limit) not in (float, int) or not 0.25 <= limit <= 8:
        raise ValueError("memory_limit_gib must be between 0.25 and 8 GiB")
    proc = psutil.Process()

    # Raises MemoryError once peak RSS exceeds the configured budget; called between blocks.
    def guard():
        if _peak_rss(proc) > limit * GIB:
            raise MemoryError(f"process peak RSS exceeds configured {limit} GiB")

    source = cfg["sources"]
    selection, pca, boundaries, legend_source = (
        Source.read(root, source[key], synthetic) for key in ("selection", "pca", "boundaries", "legend"))
    towns = read_towns(selection, pca, boundaries, synthetic)
    if only is not None and only not in {town.slug for town in towns}:
        raise ValueError(f"unknown selected town slug: {only}")
    targets = towns if only is None else [town for town in towns if town.slug == only]
    legend = read_legend(legend_source, method)
    l1 = read_level1(root, source["level1"], synthetic)
    if set(source["lulc"]) != {town.slug for town in towns}:
        raise ValueError("exactly one LULC source entry is required for every selected settlement")
    land = {slug: Source.read(root, entry, synthetic) for slug, entry in source["lulc"].items()}
    if any(item.metadata.get("kind") not in ("raster", "vector") for item in land.values()):
        raise ValueError("LULC source kind must be raster or vector")
    if not synthetic and any(item.metadata.get("release") != "2011–12 1:50K"
                             or item.metadata.get("producer") != "NRSC Bhuvan"
                             or not item.metadata.get("source_accuracy")
                             or not item.metadata.get("preparation") or not item.metadata.get("scale_use_reference")
                             for item in land.values()):
        raise ValueError("real run requires Bhuvan 2011–12 1:50K source, preparation, source accuracy and permitted scale/use record")
    config_sha = sha256_file(config_path)
    provenance = {
        "model_commit": model_commit, "dirty_worktree": dirty, "config_sha256": config_sha,
        "upstream": {
            "selection": selection.metadata, "census_pca": pca.metadata, "census_geometry": boundaries.metadata,
            "lulc_legend": legend_source.metadata,
            "level1": {"dataset_id": l1.dataset_id, "version": l1.version, "checksum_sha256": l1.checksum_sha256,
                       "source": l1.source, "provenance": l1.provenance},
        },
        "model": method,
        "resampling": {"lulc": "vector cell-center or nearest-neighbor categorical raster",
                       "level1": "native cell intersections in EPSG:6933, population allocated by area"},
    }
    code_hashes = {p.name: sha256_file(p) for p in Path(__file__).parent.glob("*.py")}
    fingerprint = hashlib.sha256(_encode([provenance, code_hashes, rasterio.__version__,
                                           rasterio.__gdal_version__]).encode()).hexdigest()

    output = (root / cfg["output_dir"]).resolve()
    town_root = output / "towns"
    town_root.mkdir(parents=True, exist_ok=True)
    entries, summary = [], []
    guard()
    # The input hashes cover self-contained TIFFs. Ignore undeclared sidecar masks and metadata.
    with rasterio.Env(GDAL_CACHEMAX=128 * 1024 ** 2, GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",
                      GDAL_PAM_ENABLED="NO"), rasterio.open(l1.file) as src:
        for town in targets:
            town_started = perf_counter()
            town_fingerprint = hashlib.sha256(_encode([fingerprint, land[town.slug].metadata, town.slug])
                                               .encode()).hexdigest()
            folder = town_root / f"{town.slug}-{town_fingerprint[:20]}"
            resumed = folder.is_dir()
            if not resumed:
                with tempfile.TemporaryDirectory(prefix=".level2-", dir=town_root) as temp:
                    stage = Path(temp) / "result"
                    stage.mkdir()
                    neighbors = [(other.census_id, other.polygon) for other in towns if other != town]
                    try:
                        report = build_town(stage, town, neighbors, src, l1.band, land[town.slug], legend, method, guard)
                    except ValueError as exc:
                        raise ValueError(f"{town.census_id} ({town.slug}): {exc}") from exc
                    plot_town(stage, report, synthetic)
                    guard()
                    report.update({"synthetic": synthetic, "model_commit": model_commit, "fingerprint": town_fingerprint,
                                   "generated_at": datetime.now(timezone.utc).isoformat(),
                                   "elapsed_seconds_town": perf_counter() - town_started,
                                   "process_peak_rss_bytes_through_town": _peak_rss(proc),
                                   "population_cog_bytes": (stage / "population.tif").stat().st_size})
                    _write_json(stage / "report.json", report)
                    _write_json(stage / "checkpoint.json",
                                {"fingerprint": town_fingerprint, "sha256": {n: sha256_file(stage / n) for n in ARTIFACTS}})
                    try:
                        stage.rename(folder)
                    except OSError:
                        if not folder.is_dir():
                            raise
                        resumed = True
            report, checksums = _checkpoint(folder, town_fingerprint)
            guard()
            town_prov = {**provenance, "lulc": land[town.slug].metadata, "generated_at": report["generated_at"]}
            entries.append(_manifest_entry(folder, output, report, checksums, cfg, town_prov))
            summary.append({"slug": town.slug, "census_id": town.census_id, "resumed": resumed,
                            "report": (folder / "report.json").relative_to(output).as_posix(),
                            "outputs_sha256": checksums,
                            "outputs_bytes": sum((folder / name).stat().st_size for name in ARTIFACTS)})
    run_report = output / ("run.json" if only is None else f"pilot-{only}.json")
    _write_json(run_report, {"synthetic": synthetic, "model_commit": model_commit,
                                     "config_sha256": config_sha, "towns": summary,
                                     "elapsed_seconds": perf_counter() - started, "peak_rss_bytes": _peak_rss(proc),
                                     "memory_limit_gib": limit, "host_ram_bytes": psutil.virtual_memory().total,
                                     "platform": platform.platform(), "gdal": rasterio.__gdal_version__})
    if only is not None:
        return run_report
    _write_json(output / "manifest.json", {"manifest_version": 1, "datasets": entries})
    return output / "manifest.json"
