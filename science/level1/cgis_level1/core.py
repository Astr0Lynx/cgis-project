"""Pure-NumPy scientific core (no I/O). Every function here has golden-value tests in ``tests/test_core.py``.

Conventions
-----------
* Arrays are 2-D, north-up, row-major. Counts and areas are extensive quantities: values per *cell*.
* ``NaN`` marks missing weight inputs inside the core; integer/float source NoData is converted to NaN by
  the I/O layer before the core sees it.
* All arithmetic is float64; outputs are cast to float32 only when written.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


# --------------------------------------------------------------------------------------------------
# Step 1 — residential built-up surface per GHSL epoch
# --------------------------------------------------------------------------------------------------
@dataclass
class ResidentialStats:
    cells: int = 0
    nres_gt_total: int = 0            # cells where NRES > total (clipped to 0)
    nres_gt_total_m2: float = 0.0     # surplus NRES area removed by the clip
    missing: int = 0                  # cells where total or NRES is NaN/NoData

    def add(self, o: "ResidentialStats") -> None:
        self.cells += o.cells
        self.nres_gt_total += o.nres_gt_total
        self.nres_gt_total_m2 += o.nres_gt_total_m2
        self.missing += o.missing


def residential(total: np.ndarray, nres: np.ndarray) -> tuple[np.ndarray, ResidentialStats]:
    """``R = max(total - NRES, 0)`` on one grid, both in m² of built-up *surface* per cell.

    NaN in either input propagates to NaN (missing weight), never silently to zero. Both inputs must be
    surfaces from the same GHSL release and grid; mixing GHS-BUILT-V (volume) is a caller error.
    """
    if total.shape != nres.shape:
        raise ValueError(f"total {total.shape} and NRES {nres.shape} grids differ")
    t = total.astype(np.float64, copy=False)
    n = nres.astype(np.float64, copy=False)
    if np.any(t < 0) or np.any(n < 0):
        raise ValueError("negative built-up surface in GHSL input")
    diff = t - n
    missing = np.isnan(diff)
    neg = (diff < 0) & ~missing
    st = ResidentialStats(cells=int(diff.size), nres_gt_total=int(neg.sum()),
                          nres_gt_total_m2=float(-diff[neg].sum()), missing=int(missing.sum()))
    r = np.where(neg, 0.0, diff)
    return r, st


# --------------------------------------------------------------------------------------------------
# Step 2 — temporal weights
# --------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class TemporalRule:
    """``R_year = sum_k coef_k * R_epoch_k`` followed by ``max(., 0)`` (only extrapolation can go negative)."""

    year: int
    terms: tuple[tuple[int, float], ...]
    kind: str           # exact | interpolate | hold | extrapolate
    description: str

    @property
    def epochs(self) -> tuple[int, ...]:
        return tuple(e for e, _ in self.terms)


def temporal_rule(year: int, epochs: tuple[int, ...], policy_2011: str | None = None,
                  extra_epochs: tuple[int, ...] = ()) -> TemporalRule:
    """Linear interpolation between bracketing epochs; explicit, approved policy beyond the last epoch.

    ``epochs`` are the required anchor epochs (2000/2005/2010). ``extra_epochs`` (e.g. 2015) are used *only*
    by ``policy_2011='interpolate_2015'``. Years before the first epoch are refused.
    """
    ep = tuple(sorted(epochs))
    if year in ep:
        return TemporalRule(year, ((year, 1.0),), "exact", f"GHSL epoch {year} used directly")
    if ep[0] < year < ep[-1]:
        e0 = max(e for e in ep if e < year)
        e1 = min(e for e in ep if e > year)
        w = (year - e0) / (e1 - e0)
        return TemporalRule(year, ((e0, 1.0 - w), (e1, w)), "interpolate",
                            f"linear interpolation {e0}->{e1}, w={w:.4f}")
    if year < ep[0]:
        raise ValueError(f"year {year} precedes the first GHSL epoch {ep[0]}; no policy defined")
    # year > last epoch
    if policy_2011 is None:
        raise ValueError(f"year {year} is after the last GHSL epoch {ep[-1]}: an explicit, approved policy "
                         f"(one of hold_2010 | extrapolate_linear | interpolate_2015) is required — "
                         "see METHODOLOGY_DECISION.md")
    last, prev = ep[-1], ep[-2]
    if policy_2011 == "hold_2010":
        return TemporalRule(year, ((last, 1.0),), "hold", f"hold last epoch {last} constant")
    if policy_2011 == "extrapolate_linear":
        s = (year - last) / (last - prev)
        return TemporalRule(year, ((prev, -s), (last, 1.0 + s)), "extrapolate",
                            f"linear extrapolation of trend {prev}->{last}, clipped at 0")
    if policy_2011 == "interpolate_2015":
        nxt = [e for e in extra_epochs if e > year]
        if not nxt:
            raise ValueError("interpolate_2015 requires an additional epoch after the target year")
        e1 = min(nxt)
        w = (year - last) / (e1 - last)
        return TemporalRule(year, ((last, 1.0 - w), (e1, w)), "interpolate",
                            f"linear interpolation {last}->{e1} (additional authorized epoch), w={w:.4f}")
    raise ValueError(f"unknown policy {policy_2011!r}")


def apply_rule(rule: TemporalRule, r_by_epoch: dict[int, np.ndarray]) -> np.ndarray:
    out = None
    for e, c in rule.terms:
        term = c * r_by_epoch[e]
        out = term if out is None else out + term
    assert out is not None
    if rule.kind == "extrapolate":
        out = np.where(out < 0, 0.0, out)    # NaN preserved
    return out


# --------------------------------------------------------------------------------------------------
# Step 3 — grid alignment of weights (same-resolution sub-pixel shift, area-overlap exact)
# --------------------------------------------------------------------------------------------------
def shift_weights(src: np.ndarray, fx: float, fy: float) -> np.ndarray:
    """Area-overlap transfer of an extensive per-cell quantity between two grids of identical cell size.

    ``src`` has shape (h+1, w+1); target cell (r, c) covers source pixel coordinates
    ``[c+fx, c+fx+1] x [r+fy, r+fy+1]`` with ``0 <= fx, fy < 1``. Each source cell's quantity is split by
    overlap area (uniform-within-cell assumption), so sum is conserved in the interior.
    NaN in any contributing source cell with non-zero overlap yields NaN.
    """
    if not (0.0 <= fx < 1.0 and 0.0 <= fy < 1.0):
        raise ValueError("fractional offsets must be in [0, 1)")
    s = src.astype(np.float64, copy=False)
    h, w = s.shape[0] - 1, s.shape[1] - 1
    if fx == 0.0 and fy == 0.0:
        return s[:h, :w].copy()
    out = np.zeros((h, w), dtype=np.float64)
    for dy, wy in ((0, 1.0 - fy), (1, fy)):
        if wy == 0.0:
            continue
        for dx, wx in ((0, 1.0 - fx), (1, fx)):
            if wx == 0.0:
                continue
            out += (wy * wx) * s[dy:dy + h, dx:dx + w]
    return out


# --------------------------------------------------------------------------------------------------
# Step 4 — parent-support redistribution  p_ai = P_a * R_ai / sum_j R_aj
# --------------------------------------------------------------------------------------------------
@dataclass
class RedistStats:
    parents: int = 0                 # parents (blocks) with >=1 valid WorldPop cell
    parents_zero_weight: int = 0     # parents where sum R == 0 -> fallback
    pop_zero_weight: float = 0.0     # population in fallback parents
    parents_populated: int = 0
    valid_cells: int = 0
    weight_missing_cells: int = 0    # valid WorldPop cells whose weight was NaN (treated as 0)
    sum_in: float = 0.0
    sum_out: float = 0.0
    max_parent_abs_err: float = 0.0
    max_parent_rel_err: float = 0.0
    negative_out: int = 0

    def add(self, o: "RedistStats") -> None:
        for k in ("parents", "parents_zero_weight", "parents_populated", "valid_cells",
                  "weight_missing_cells", "negative_out"):
            setattr(self, k, getattr(self, k) + getattr(o, k))
        self.pop_zero_weight += o.pop_zero_weight
        self.sum_in += o.sum_in
        self.sum_out += o.sum_out
        self.max_parent_abs_err = max(self.max_parent_abs_err, o.max_parent_abs_err)
        self.max_parent_rel_err = max(self.max_parent_rel_err, o.max_parent_rel_err)


def _blocksum(a: np.ndarray, k: int) -> np.ndarray:
    h, w = a.shape
    return a.reshape(h // k, k, w // k, k).sum(axis=(1, 3))


def _expand(a: np.ndarray, k: int) -> np.ndarray:
    return np.repeat(np.repeat(a, k, axis=0), k, axis=1)


def redistribute_blocks(pop: np.ndarray, valid: np.ndarray, weight: np.ndarray, k: int,
                        fallback: str = "worldpop", out_dtype=np.float32
                        ) -> tuple[np.ndarray, RedistStats]:
    """Redistribute WorldPop counts within k x k parent blocks proportionally to residential weight.

    * ``pop``: WorldPop counts (float); ``valid``: True where WorldPop has data. Only valid cells are
      eligible targets and only valid cells contribute to ``P_a`` — population never moves into water /
      out-of-coverage cells, and every eligible cell belongs to exactly one parent.
    * ``weight``: residential surface R on the same grid (NaN = missing -> treated as 0 and counted).
    * Zero-weight parents (sum R == 0): ``fallback='worldpop'`` keeps WorldPop's own distribution
      (identity), ``'uniform'`` spreads ``P_a`` evenly over the valid cells.
    * Shape must be a multiple of k (the I/O layer pads partial edge blocks with invalid cells).

    Returns float array (NaN where invalid) and statistics including per-parent mass balance computed
    on the *cast* output (what is actually written).
    """
    if pop.shape != valid.shape or pop.shape != weight.shape:
        raise ValueError("pop/valid/weight shapes differ")
    h, w = pop.shape
    if h % k or w % k:
        raise ValueError(f"array shape {pop.shape} is not a multiple of block {k}")
    if fallback not in ("worldpop", "uniform"):
        raise ValueError(f"unknown fallback {fallback!r}")
    p = np.where(valid, pop.astype(np.float64), 0.0)
    if np.any(p < 0):
        raise ValueError("negative WorldPop count in a valid cell")
    wmiss = valid & np.isnan(weight)
    r = np.where(valid & ~np.isnan(weight), weight.astype(np.float64), 0.0)
    if np.any(r < 0):
        raise ValueError("negative residential weight")

    P = _blocksum(p, k)
    S = _blocksum(r, k)
    N = _blocksum(valid.astype(np.int64), k)
    Pe, Se, Ne = _expand(P, k), _expand(S, k), _expand(N, k)
    pos = Se > 0
    with np.errstate(invalid="ignore", divide="ignore"):
        dasy = Pe * (r / np.where(pos, Se, 1.0))
        if fallback == "worldpop":
            fb = p
        else:
            fb = Pe / np.where(Ne > 0, Ne, 1)
    out = np.where(pos, dasy, fb)
    out = np.where(valid, out, np.nan)
    out_cast = out.astype(out_dtype)

    # mass balance on the cast values that will be written
    oc = np.where(valid, out_cast.astype(np.float64), 0.0)
    Pout = _blocksum(oc, k)
    has = N > 0
    abs_err = np.abs(Pout - P)
    rel_err = np.where(P > 0, abs_err / np.where(P > 0, P, 1.0), 0.0)
    zw = has & (S == 0)
    st = RedistStats(
        parents=int(has.sum()), parents_zero_weight=int(zw.sum()), pop_zero_weight=float(P[zw].sum()),
        parents_populated=int((P > 0).sum()), valid_cells=int(valid.sum()),
        weight_missing_cells=int(wmiss.sum()), sum_in=float(P.sum()), sum_out=float(Pout.sum()),
        max_parent_abs_err=float(abs_err.max(initial=0.0)), max_parent_rel_err=float(rel_err.max(initial=0.0)),
        negative_out=int((out_cast[valid] < 0).sum()),
    )
    return out_cast, st


def redistribute_zones(pop: np.ndarray, valid: np.ndarray, weight: np.ndarray, zones: np.ndarray,
                       P_zone: np.ndarray, S_zone: np.ndarray, N_zone: np.ndarray,
                       fallback: str = "worldpop", out_dtype=np.float32) -> np.ndarray:
    """Second pass of the two-pass *zone-parent* mode (e.g. rasterized district IDs as parents).

    ``P_zone``, ``S_zone``, ``N_zone`` are national per-zone sums of WorldPop, residential weight and valid
    cell counts from pass 1 (:func:`zone_sums`). Zone id 0 (or negative) = no parent -> output keeps
    WorldPop value (identity), so no population is lost.
    """
    p = np.where(valid, pop.astype(np.float64), 0.0)
    r = np.where(valid & ~np.isnan(weight), weight.astype(np.float64), 0.0)
    z = np.where(valid & (zones > 0), zones, 0).astype(np.int64)
    Pz, Sz, Nz = P_zone[z], S_zone[z], N_zone[z]
    pos = (Sz > 0) & (z > 0)
    with np.errstate(invalid="ignore", divide="ignore"):
        dasy = Pz * r / np.where(pos, Sz, 1.0)
        fb = p if fallback == "worldpop" else np.where(z > 0, Pz / np.where(Nz > 0, Nz, 1), p)
    out = np.where(pos, dasy, fb)
    return np.where(valid, out, np.nan).astype(out_dtype)


def zone_sums(pop: np.ndarray, valid: np.ndarray, weight: np.ndarray, zones: np.ndarray, nzones: int
              ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Pass-1 accumulation for zone-parent mode (bincount; bounded memory: O(nzones))."""
    p = np.where(valid, pop.astype(np.float64), 0.0).ravel()
    r = np.where(valid & ~np.isnan(weight), weight.astype(np.float64), 0.0).ravel()
    z = np.where(valid & (zones > 0), zones, 0).astype(np.int64).ravel()
    if z.size and z.max() >= nzones:
        raise ValueError("zone id exceeds nzones")
    return (np.bincount(z, weights=p, minlength=nzones), np.bincount(z, weights=r, minlength=nzones),
            np.bincount(z, weights=valid.ravel().astype(np.float64), minlength=nzones))
