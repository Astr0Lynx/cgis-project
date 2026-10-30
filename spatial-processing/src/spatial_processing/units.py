"""Unit semantics: decides what a zonal ``sum`` means for a dataset.

* ``count`` units (e.g. ``people_per_cell``): each cell holds a count, so the sum of
  valid cells is a total count (population for population rasters).
* ``density`` units (e.g. ``people_per_km2``): summing cells is meaningless without
  multiplying by cell area, which Phase 2 does not do. ``sum`` is refused when
  explicitly requested and omitted otherwise.
* anything else / missing: the sum is reported but qualified as a plain
  ``sum_of_cell_values`` with a warning, never as a population count.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

COUNT_UNITS = {
    "people_per_cell",
    "persons_per_cell",
    "population_per_cell",
    "count_per_cell",
    "count",
    "counts",
    "people",
    "persons",
    "population_count",
}

_DENSITY_RE = re.compile(
    r"(per[_ ]?(sq[_ ]?)?(km|m|mi|ha|hectare|acre)s?(2|\^2|²)?\b|/\s*(km|m|mi|ha)|density|per[_ ]square)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class UnitSemantics:
    kind: str  # "count" | "density" | "unknown"
    units: str | None
    sum_semantics: str  # "total_count" | "not_applicable_density" | "sum_of_cell_values"


def classify_units(units: str | None) -> UnitSemantics:
    normalized = (units or "").strip().lower()
    if normalized in COUNT_UNITS:
        return UnitSemantics("count", units, "total_count")
    if normalized and _DENSITY_RE.search(normalized):
        return UnitSemantics("density", units, "not_applicable_density")
    return UnitSemantics("unknown", units, "sum_of_cell_values")
