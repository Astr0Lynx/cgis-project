# Golden tiny-raster tests for the scientific core (subtraction, interpolation, alignment, redistribution).

import math

import numpy as np
import pytest

from cgis_level1 import core

nan = np.nan


# ---------------------------------------------------------------- residential subtraction
def test_residential_golden_values():
    total = np.array([[100, 50], [0, 10]], dtype=float)
    nres = np.array([[30, 60], [0, nan]])
    r, st = core.residential(total, nres)
    assert r[0, 0] == 70
    assert r[0, 1] == 0          # NRES > total -> clipped to 0, never negative
    assert r[1, 0] == 0          # both zero
    assert math.isnan(r[1, 1])   # missing NRES -> missing weight, not silently 0
    assert st.nres_gt_total == 1 and st.nres_gt_total_m2 == 10 and st.missing == 1 and st.cells == 4


# Checks: residential uint16 inputs no wraparound.
def test_residential_uint16_inputs_no_wraparound():
    # uint16 subtraction would wrap to 65526; the core must work in float
    total = np.array([[5]], dtype=np.uint16)
    nres = np.array([[15]], dtype=np.uint16)
    r, _ = core.residential(total, nres)
    assert r[0, 0] == 0


# Checks: residential rejects negative and mismatch.
def test_residential_rejects_negative_and_mismatch():
    with pytest.raises(ValueError):
        core.residential(np.array([[-1.0]]), np.array([[0.0]]))
    with pytest.raises(ValueError):
        core.residential(np.zeros((2, 2)), np.zeros((2, 3)))


# ---------------------------------------------------------------- temporal rules
EP = (2000, 2005, 2010)


# Checks: interpolation weights.
def test_interpolation_weights():
    r = core.temporal_rule(2003, EP)
    assert r.kind == "interpolate"
    assert r.terms[0][0] == 2000 and r.terms[1][0] == 2005
    assert r.terms[0][1] == pytest.approx(0.4) and r.terms[1][1] == pytest.approx(0.6)
    r = core.temporal_rule(2009, EP)
    assert dict(r.terms) == pytest.approx({2005: 0.2, 2010: 0.8})


# Checks: exact epochs.
def test_exact_epochs():
    for e in EP:
        assert core.temporal_rule(e, EP).terms == ((e, 1.0),)


# Checks: every target year has rule weights summing to one.
def test_every_target_year_has_rule_weights_summing_to_one():
    for y in range(2001, 2011):
        r = core.temporal_rule(y, EP)
        assert sum(c for _, c in r.terms) == pytest.approx(1.0)
        assert all(c >= 0 for _, c in r.terms)


# Checks: 2011 requires explicit policy.
def test_2011_requires_explicit_policy():
    with pytest.raises(ValueError, match="approved policy"):
        core.temporal_rule(2011, EP)


# Checks: 2011 policies.
def test_2011_policies():
    assert core.temporal_rule(2011, EP, "hold_2010").terms == ((2010, 1.0),)
    ex = core.temporal_rule(2011, EP, "extrapolate_linear")
    assert dict(ex.terms) == pytest.approx({2005: -0.2, 2010: 1.2})
    it = core.temporal_rule(2011, EP, "interpolate_2015", extra_epochs=(2015,))
    assert dict(it.terms) == pytest.approx({2010: 0.8, 2015: 0.2})
    with pytest.raises(ValueError):
        core.temporal_rule(2011, EP, "interpolate_2015")   # no extra epoch supplied


# Checks: year before first epoch refused.
def test_year_before_first_epoch_refused():
    with pytest.raises(ValueError):
        core.temporal_rule(1999, EP)


# Checks: apply rule interpolation and extrapolation clip.
def test_apply_rule_interpolation_and_extrapolation_clip():
    r = {2005: np.array([[10.0, 0.0, nan]]), 2010: np.array([[20.0, 0.0, 5.0]])}
    out = core.apply_rule(core.temporal_rule(2008, EP), r)
    assert out[0, 0] == pytest.approx(16.0) and out[0, 1] == 0 and math.isnan(out[0, 2])
    r2 = {2005: np.array([[100.0, 10.0]]), 2010: np.array([[10.0, 20.0]])}
    ex = core.apply_rule(core.temporal_rule(2011, EP, "extrapolate_linear"), r2)
    # 1.2*10 - 0.2*100 = -8 -> clipped to 0 ; 1.2*20 - 0.2*10 = 22
    assert ex[0, 0] == 0 and ex[0, 1] == pytest.approx(22.0)


# ---------------------------------------------------------------- sub-pixel alignment
def test_shift_identity():
    s = np.arange(12, dtype=float).reshape(3, 4)
    assert np.array_equal(core.shift_weights(s, 0, 0), s[:2, :3])


# Checks: shift half pixel golden.
def test_shift_half_pixel_golden():
    s = np.array([[0, 4, 8], [4, 8, 0]], dtype=float)
    out = core.shift_weights(s, 0.5, 0.5)     # each target = mean of 2x2 (equal quarter overlaps)
    assert out.tolist() == [[4.0, 5.0]]
    out = core.shift_weights(s, 0.5, 0.0)
    assert out.tolist() == [[2.0, 6.0]]


# Checks: shift conserves interior mass.
def test_shift_conserves_interior_mass():
    rng = np.random.default_rng(1)
    s = np.zeros((52, 52))
    s[1:51, 1:51] = rng.random((50, 50))   # zero border => all mass lands inside target
    out = core.shift_weights(s, 0.3, 0.7)
    assert out.sum() == pytest.approx(s.sum(), rel=1e-12)


# Checks: shift nan propagates.
def test_shift_nan_propagates():
    s = np.array([[1, nan], [1, 1]], dtype=float)
    assert math.isnan(core.shift_weights(s, 0.5, 0.0)[0, 0])
    with pytest.raises(ValueError):
        core.shift_weights(s, 1.0, 0.0)


# ---------------------------------------------------------------- redistribution
def _ok(shape):
    return np.ones(shape, dtype=bool)


# Checks: uniform weights spread evenly.
def test_uniform_weights_spread_evenly():
    pop = np.array([[1, 2], [3, 4]], dtype=float)
    out, st = core.redistribute_blocks(pop, _ok(pop.shape), np.ones((2, 2)), 2)
    assert np.allclose(out, 2.5)
    assert st.parents == 1 and st.parents_zero_weight == 0


# Checks: uneven weights golden.
def test_uneven_weights_golden():
    pop = np.array([[1, 2], [3, 4]], dtype=float)
    w = np.array([[1, 3], [0, 0]], dtype=float)
    out, st = core.redistribute_blocks(pop, _ok(pop.shape), w, 2)
    assert out.tolist() == [[2.5, 7.5], [0.0, 0.0]]
    assert st.sum_out == pytest.approx(10.0)


# Checks: all zero weights fallbacks.
def test_all_zero_weights_fallbacks():
    pop = np.array([[1, 2], [3, 4]], dtype=float)
    out, st = core.redistribute_blocks(pop, _ok(pop.shape), np.zeros((2, 2)), 2, "worldpop")
    assert out.tolist() == pop.tolist()                 # identity keeps WorldPop distribution
    assert st.parents_zero_weight == 1 and st.pop_zero_weight == 10
    out, _ = core.redistribute_blocks(pop, _ok(pop.shape), np.zeros((2, 2)), 2, "uniform")
    assert np.allclose(out, 2.5)


# Checks: nodata cells excluded and stay nodata.
def test_nodata_cells_excluded_and_stay_nodata():
    pop = np.array([[1, 2], [3, -99999]], dtype=float)
    valid = pop != -99999
    w = np.array([[1, 1], [1, 100]], dtype=float)   # big weight on the NoData cell must be ignored
    out, st = core.redistribute_blocks(pop, valid, w, 2)
    assert math.isnan(out[1, 1])
    assert np.allclose(out[valid], 2.0)              # P=6 over three equal weights
    assert st.valid_cells == 3 and st.sum_in == 6


# Checks: missing weight is zero and counted.
def test_missing_weight_is_zero_and_counted():
    pop = np.array([[4, 0], [0, 0]], dtype=float)
    w = np.array([[nan, 1], [1, 2]])
    out, st = core.redistribute_blocks(pop, _ok((2, 2)), w, 2)
    assert out.tolist() == [[0.0, 1.0], [1.0, 2.0]]
    assert st.weight_missing_cells == 1


# Checks: parent boundaries no leakage.
def test_parent_boundaries_no_leakage():
    pop = np.zeros((2, 4))
    pop[:, :2] = 1.0          # parent A: 4 people; parent B: 0 people
    w = np.zeros((2, 4))
    w[:, 2:] = 5.0            # all weight in parent B
    out, st = core.redistribute_blocks(pop, _ok(pop.shape), w, 2)
    assert out[:, :2].sum() == pytest.approx(4.0)   # A keeps its people (zero-weight fallback)
    assert out[:, 2:].sum() == 0.0
    assert st.parents_zero_weight == 1


# Checks: partial parent all invalid block.
def test_partial_parent_all_invalid_block():
    pop = np.full((2, 4), -1.0)
    valid = np.zeros((2, 4), dtype=bool)
    valid[:, :2] = True
    pop[valid] = 3.0
    out, st = core.redistribute_blocks(pop, valid, np.ones((2, 4)), 2)
    assert st.parents == 1 and np.isnan(out[:, 2:]).all()


# Checks: conservation random property.
def test_conservation_random_property():
    rng = np.random.default_rng(42)
    for k in (2, 5, 10):
        pop = rng.gamma(0.5, 50, (40, 60))
        valid = rng.random((40, 60)) > 0.1
        w = rng.random((40, 60)) * (rng.random((40, 60)) > 0.5)
        out, st = core.redistribute_blocks(pop, valid, w, k)
        P = np.where(valid, pop, 0).reshape(40 // k, k, 60 // k, k).sum(axis=(1, 3))
        O = np.where(valid, out, 0).reshape(40 // k, k, 60 // k, k).sum(axis=(1, 3))
        assert np.allclose(O, P, rtol=1e-6, atol=1e-4)
        assert st.max_parent_rel_err < 1e-6
        assert (out[valid] >= 0).all() and st.negative_out == 0


# Checks: shape and sign validation.
def test_shape_and_sign_validation():
    with pytest.raises(ValueError):
        core.redistribute_blocks(np.ones((3, 4)), _ok((3, 4)), np.ones((3, 4)), 2)
    with pytest.raises(ValueError):
        core.redistribute_blocks(-np.ones((2, 2)), _ok((2, 2)), np.ones((2, 2)), 2)
    with pytest.raises(ValueError):
        core.redistribute_blocks(np.ones((2, 2)), _ok((2, 2)), -np.ones((2, 2)), 2)


# Checks: deterministic rerun.
def test_deterministic_rerun():
    rng = np.random.default_rng(7)
    pop, w = rng.random((20, 20)), rng.random((20, 20))
    a, _ = core.redistribute_blocks(pop, _ok(pop.shape), w, 10)
    b, _ = core.redistribute_blocks(pop.copy(), _ok(pop.shape), w.copy(), 10)
    assert a.tobytes() == b.tobytes()


# Checks: zone mode matches block mode.
def test_zone_mode_matches_block_mode():
    rng = np.random.default_rng(3)
    pop = rng.gamma(1, 3, (20, 30))
    valid = rng.random((20, 30)) > 0.05
    w = rng.random((20, 30)) * (rng.random((20, 30)) > 0.3)
    k = 10
    zones = (np.arange(20)[:, None] // k) * 3 + (np.arange(30)[None, :] // k) + 1
    Pz, Sz, Nz = core.zone_sums(pop, valid, w, zones, 7)
    oz = core.redistribute_zones(pop, valid, w, zones, Pz, Sz, Nz)
    ob, _ = core.redistribute_blocks(pop, valid, w, k)
    assert np.allclose(np.nan_to_num(oz), np.nan_to_num(ob), rtol=1e-6)
    # zone 0 (no parent) keeps WorldPop values
    z0 = zones.copy()
    z0[:, :5] = 0
    Pz, Sz, Nz = core.zone_sums(pop, valid, w, z0, 7)
    o0 = core.redistribute_zones(pop, valid, w, z0, Pz, Sz, Nz)
    assert np.allclose(o0[:, :5][valid[:, :5]], pop[:, :5][valid[:, :5]], rtol=1e-6)
