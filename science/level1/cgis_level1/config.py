# Deterministic TOML run configuration.
# Relative paths in a config resolve against ``paths.root`` (itself relative to the config file's directory),
# so the same config works from any working directory. The SHA-256 of the config file is recorded in every
# output's provenance.

from __future__ import annotations

import hashlib
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

POLICIES_2011 = ("hold_2010", "extrapolate_linear", "interpolate_2015")
FALLBACKS = ("worldpop", "uniform")


# Raised by load_config for invalid TOML or values.
class ConfigError(ValueError):
    pass


# Validated run configuration; created by load_config, passed to every pipeline/evaluate function.
@dataclass(frozen=True)
class Config:
    path: Path
    sha256: str
    root: Path
    name: str
    synthetic: bool
    years: tuple[int, ...]
    work_dir: Path
    out_dir: Path
    tile_size: int
    tile_cols: int
    workers: int
    max_rss_gb: float
    bbox: tuple[float, float, float, float] | None
    block: int
    zero_weight_fallback: str
    worldpop_pattern: str
    ghsl_total_glob: str
    ghsl_nres_glob: str
    ghsl_nodata: float | None
    epochs: tuple[int, ...]
    policy_2011: str | None
    policy_2011_approval: str
    compare_2011_policies: bool
    ghs_pop_glob: str | None
    ghs_pop_epochs: tuple[int, ...]
    output_nodata: float
    dataset_prefix: str
    dataset_version: int
    eval_supports: tuple[int, ...]
    eval_sample_step: int
    eval_zoom_regions: dict[str, tuple[float, float, float, float]]
    eval_zones: dict[str, dict]
    mass_rel_tol: float
    mass_abs_tol: float
    provenance_extra: dict[str, Any] = field(default_factory=dict)

    # Resolves a config path against paths.root.
    def resolve(self, p: str) -> Path:
        q = Path(p)
        return q if q.is_absolute() else (self.root / q)

    # WorldPop file of one year.
    def worldpop_path(self, year: int) -> Path:
        return self.resolve(self.worldpop_pattern.format(year=year))

    # Resolved glob of GHSL tiles for kind total|nres|pop and one epoch.
    def ghsl_glob(self, kind: str, epoch: int) -> str:
        g = {"total": self.ghsl_total_glob, "nres": self.ghsl_nres_glob, "pop": self.ghs_pop_glob}[kind]
        if g is None:
            raise ConfigError(f"no glob configured for GHSL {kind}")
        return str(self.resolve(g.format(epoch=epoch)))

    # Output dataset id of one year (prefix + year).
    def dataset_id(self, year: int) -> str:
        return f"{self.dataset_prefix}{year}"


# Dotted-key lookup with default and type check (load_config).
def _get(d: dict, dotted: str, default: Any = ..., typ: type | tuple | None = None) -> Any:
    cur: Any = d
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            if default is ...:
                raise ConfigError(f"missing config key '{dotted}'")
            return default
        cur = cur[part]
    if typ is not None and cur is not None and not isinstance(cur, typ):
        raise ConfigError(f"config key '{dotted}' must be {typ}, got {type(cur).__name__}")
    return cur


# Parses and validates a TOML config. Used by cli.main, pipeline._worker_init and tests.
def load_config(path: str | Path) -> Config:
    p = Path(path).resolve()
    raw_bytes = p.read_bytes()
    try:
        d = tomllib.loads(raw_bytes.decode("utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{p.name}: invalid TOML: {exc}") from exc

    root = (p.parent / _get(d, "paths.root", ".", str)).resolve()
    years = tuple(int(y) for y in _get(d, "run.years", typ=list))
    if not years or any(y < 1900 or y > 2100 for y in years) or len(set(years)) != len(years):
        raise ConfigError("run.years must be a non-empty list of distinct years")
    block = int(_get(d, "parent.block", 10, int))
    tile = int(_get(d, "run.tile_size", 2000, int))
    tile_cols = int(_get(d, "run.tile_cols", tile, int))
    for name, val in (("run.tile_size", tile), ("run.tile_cols", tile_cols)):
        if block < 1 or val < block or val % block:
            raise ConfigError(f"{name} ({val}) must be a positive multiple of parent.block ({block}) "
                              "so that no parent block straddles a tile seam")
    fallback = _get(d, "parent.zero_weight_fallback", "worldpop", str)
    if fallback not in FALLBACKS:
        raise ConfigError(f"parent.zero_weight_fallback must be one of {FALLBACKS}")
    epochs = tuple(sorted(int(e) for e in _get(d, "inputs.ghsl.epochs", [2000, 2005, 2010], list)))
    if len(epochs) < 2:
        raise ConfigError("at least two GHSL epochs are required")
    policy = _get(d, "temporal.policy_2011", None, str)
    if policy is not None and policy not in POLICIES_2011:
        raise ConfigError(f"temporal.policy_2011 must be one of {POLICIES_2011}")
    bbox = _get(d, "run.bbox", None, list)
    if bbox is not None:
        if len(bbox) != 4 or not (bbox[0] < bbox[2] and bbox[1] < bbox[3]):
            raise ConfigError("run.bbox must be [minx, miny, maxx, maxy]")
        bbox = tuple(float(v) for v in bbox)
    zooms = {k: tuple(float(x) for x in v) for k, v in _get(d, "evaluation.zoom_regions", {}, dict).items()}

    return Config(
        path=p, sha256=hashlib.sha256(raw_bytes).hexdigest(), root=root,
        name=_get(d, "run.name", typ=str),
        synthetic=bool(_get(d, "run.synthetic", False, bool)),
        years=years,
        work_dir=(root / _get(d, "paths.work_dir", typ=str)).resolve(),
        out_dir=(root / _get(d, "paths.out_dir", typ=str)).resolve(),
        tile_size=tile, tile_cols=tile_cols,
        workers=max(1, int(_get(d, "run.workers", 1, int))),
        max_rss_gb=float(_get(d, "run.max_rss_gb", 12.0, (int, float))),
        bbox=bbox, block=block, zero_weight_fallback=fallback,
        worldpop_pattern=_get(d, "inputs.worldpop.pattern", typ=str),
        ghsl_total_glob=_get(d, "inputs.ghsl.total_glob", typ=str),
        ghsl_nres_glob=_get(d, "inputs.ghsl.nres_glob", typ=str),
        ghsl_nodata=_get(d, "inputs.ghsl.nodata", None, (int, float)),
        epochs=epochs,
        policy_2011=policy,
        policy_2011_approval=_get(d, "temporal.policy_2011_approval", "pending", str),
        compare_2011_policies=bool(_get(d, "temporal.compare_2011_policies", True, bool)),
        ghs_pop_glob=_get(d, "inputs.ghs_pop.glob", None, str),
        ghs_pop_epochs=tuple(sorted(int(e) for e in _get(d, "inputs.ghs_pop.epochs", [], list))),
        output_nodata=float(_get(d, "output.nodata", -99999.0, (int, float))),
        dataset_prefix=_get(d, "output.dataset_prefix", "india-l1-pop-", str),
        dataset_version=int(_get(d, "output.version", 1, int)),
        eval_supports=tuple(int(s) for s in _get(d, "evaluation.supports", [1, 10, 100], list)),
        eval_sample_step=int(_get(d, "evaluation.sample_step", 17, int)),
        eval_zoom_regions=zooms,
        eval_zones=_get(d, "evaluation.zones", {}, dict),
        mass_rel_tol=float(_get(d, "qa.mass_rel_tol", 1e-5, (int, float))),
        mass_abs_tol=float(_get(d, "qa.mass_abs_tol", 1e-3, (int, float))),
        provenance_extra=_get(d, "provenance", {}, dict),
    )
