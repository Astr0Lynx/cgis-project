"""Single source of truth for every required external input.

Nothing here downloads data. `build_inventory()` renders the registry to the machine-readable
`source_inventory.json`; access status and checksums are filled from `science/inputs/source_state.json`
(written by the fetch tooling) so the registry itself stays declarative and deterministic.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any

WORLDPOP_YEARS = tuple(range(2001, 2012))   # exactly 11 annual rasters
GHSL_EPOCHS = (2000, 2005, 2010)          # required by the original flowchart
GHSL_EXTRA_EPOCHS = (2015,)               # added for 2011 only: D-L1-01 option C, approved 2026-11-07 (see Agent 2 METHODOLOGY_DECISION.md)

WORLDPOP_BASE = "https://data.worldpop.org/GIS/Population/Global_2000_2020"
GHSL_BASE = "https://jeodpp.jrc.ec.europa.eu/ftp/jrc-opendata/GHSL"

# Access states (only these are allowed)
AVAILABLE, REQUESTED, RESTRICTED, MISSING = "available", "requested", "restricted", "missing"
ACCESS_STATES = (AVAILABLE, REQUESTED, RESTRICTED, MISSING)


@dataclass(frozen=True)
class Source:
    source_id: str            # also the stable dataset-id family (see SHARED_CONTRACTS section 1)
    title: str
    category: str             # worldpop | ghsl_built_total | ghsl_built_nres | ghsl_pop | census | boundary | lulc
    year: int | None
    url: str                  # official download URL (or portal page for gated sources)
    landing_page: str
    edition: str
    license: str
    crs: str
    units: str
    semantics: str            # what the numbers mean; guards against count/density/volume confusion
    resolution: str
    size_bytes: int | None = None     # filled by probe (HEAD) – never guessed
    access: str = MISSING
    download_mechanism: str = ""
    notes: str = ""
    tiles: dict[str, Any] | None = None
    extra: dict[str, Any] = field(default_factory=dict)


def _worldpop(year: int) -> Source:
    return Source(
        source_id=f"india-worldpop-unconstrained-{year}",
        title=f"WorldPop India unconstrained individual-countries population count {year} (100 m)",
        category="worldpop", year=year,
        url=f"{WORLDPOP_BASE}/{year}/IND/ind_ppp_{year}.tif",
        landing_page="https://hub.worldpop.org/Global1_2000-2020",
        edition="WorldPop Global 2000-2020 (individual countries, unconstrained, 1 km/100 m release 2018-11)",
        license="CC BY 4.0 (WorldPop; cite Tatem 2017 / WorldPop hub DOI per country page)",
        crs="EPSG:4326", units="people_per_cell",
        semantics="Estimated PEOPLE PER GRID CELL (counts), NOT density. Never reproject/resample with averaging; "
                  "aggregate by summation only.",
        resolution="3 arc-seconds (~100 m at the equator)",
        access=AVAILABLE,
        download_mechanism="HTTPS GET, public, no login; server does not support HTTP Range for /vsicurl/ windows, "
                           "so the whole file must be downloaded (resumable fetch uses Range only if honoured).",
    )


def _ghsl_url(product: str, epoch: int, grid: str) -> str:
    fam = "GHS_BUILT_S_GLOBE_R2023A" if product.startswith("GHS_BUILT_S") else "GHS_POP_GLOBE_R2023A"
    pid = f"{product}_E{epoch}_GLOBE_R2023A_{grid}"
    return f"{GHSL_BASE}/{fam}/{pid}/V1-0/{pid}_V1_0.zip"


def _ghsl_tiles(product: str, epoch: int, grid: str) -> dict[str, Any]:
    fam = "GHS_BUILT_S_GLOBE_R2023A" if product.startswith("GHS_BUILT_S") else "GHS_POP_GLOBE_R2023A"
    pid = f"{product}_E{epoch}_GLOBE_R2023A_{grid}"
    scheme = ("GHSL EPSG:4326 3ss tile grid, 12000x12000 px (~10 deg) tiles, rows R1..R18 from north, cols C1..C36 "
              "from west; India = R6-R9 x C25-C28 (some are open sea and return 404)") if "4326" in grid else \
             "GHSL Mollweide 100 m tile grid, 10000x10000 px (1000 km) tiles"
    return {"pattern": f"{GHSL_BASE}/{fam}/{pid}/V1-0/tiles/{pid}_V1_0_R{{row}}_C{{col}}.zip", "scheme": scheme}


_EXTRA_NOTE = "Additional epoch authorised only for the 2011 Level 1 weights (D-L1-01 option C)."


def _ghsl_built(epoch: int, nres: bool) -> Source:
    product = "GHS_BUILT_S_NRES" if nres else "GHS_BUILT_S"
    kind = "NRES" if nres else "total"
    return Source(
        source_id=f"india-ghsl-built-{'nres' if nres else 'total'}-{epoch}",
        title=f"GHSL GHS-BUILT-S R2023A {kind} built-up SURFACE {epoch} (3 arc-second, EPSG:4326)",
        category="ghsl_built_nres" if nres else "ghsl_built_total", year=epoch,
        url=_ghsl_url(product, epoch, "4326_3ss"),
        landing_page="https://human-settlement.emergency.copernicus.eu/ghs_buS2023.php",
        edition="GHS-BUILT-S R2023A, V1.0 (Pesaresi & Politis 2023, doi:10.2905/9F06F36F-4B11-47EC-ABB0-4F8B7B1D72EA)",
        license="CC BY 4.0 (European Union, JRC; attribution required)",
        crs="EPSG:4326", units="m2_per_cell",
        semantics="Built-up SURFACE area in square metres per 3 arc-second cell (uint16, NoData 65535; bounded by the cell's "
                  "ground area ~8,600-9,300 m2 over India). Not GHS-BUILT-V volume. "
                  + ("Non-residential share of that surface." if nres else "All built-up surface.")
                  + " Lies exactly on WorldPop's 3 arc-second lattice (offset 0 px verified), so no weight regridding."
                  + (" " + _EXTRA_NOTE if epoch in GHSL_EXTRA_EPOCHS else ""),
        resolution="3 arc-seconds (JRC-resampled from the native 100 m Mollweide product)",
        access=AVAILABLE,
        download_mechanism="HTTPS GET public JRC open-data FTP (global zip or per-tile zips)",
        tiles=_ghsl_tiles(product, epoch, "4326_3ss"),
        extra={"primary_grid": "4326_3ss (requested by Agent 2: docs/change-requests/agent2-to-agent1-inputs.md)",
               "alt_grid_mollweide_100m_url": _ghsl_url(product, epoch, "54009_100"),
               "required_by_flowchart": epoch in GHSL_EPOCHS},
    )


def _ghsl_pop(epoch: int) -> Source:
    return Source(
        source_id=f"india-ghsl-pop-{epoch}",
        title=f"GHSL GHS-POP R2023A population {epoch} (3 arc-second, EPSG:4326) – evaluation reference only",
        category="ghsl_pop", year=epoch, url=_ghsl_url("GHS_POP", epoch, "4326_3ss"),
        landing_page="https://human-settlement.emergency.copernicus.eu/ghs_pop2023.php",
        edition="GHS-POP R2023A, V1.0 (Schiavina et al. 2023, doi:10.2905/2FF68A52-5B5B-4A22-8F40-C41DA8332CFE)",
        license="CC BY 4.0 (European Union, JRC)",
        crs="EPSG:4326", units="people_per_cell",
        semantics="Modelled residential population COUNT per 3 arc-second cell (float), not density; a model product, "
                  "not census ground truth." + (" Reference for the 2015 epoch only if needed." if epoch in GHSL_EXTRA_EPOCHS else ""),
        resolution="3 arc-seconds", access=AVAILABLE,
        download_mechanism="HTTPS GET public JRC open-data FTP",
        tiles=_ghsl_tiles("GHS_POP", epoch, "4326_3ss"),
        extra={"alt_grid_mollweide_100m_url": _ghsl_url("GHS_POP", epoch, "54009_100")},
    )


def _geoboundaries(level: str, n_expected: int, lic: str, year: int) -> Source:
    return Source(
        source_id=f"india-geoboundaries-{level.lower()}",
        title=f"geoBoundaries gbOpen IND {level} (DIAGNOSTIC ONLY – not Census 2011 codes)",
        category="boundary_diagnostic", year=year,
        url=f"https://github.com/wmgeolab/geoBoundaries/raw/9469f09/releaseData/gbOpen/IND/{level}/geoBoundaries-IND-{level}.geojson",
        landing_page="https://www.geoboundaries.org/",
        edition=f"geoBoundaries gbOpen, commit 9469f09 (same pin as Agent 2); {n_expected} units expected",
        license=lic, crs="EPSG:4326 (CRS84)", units="n/a (polygons)",
        semantics="Generic ADM boundaries, claimed-extent India; carries NO Census 2011 geocodes, so it cannot satisfy the "
                  "Census join (D3). Usable only as evaluation zones.",
        resolution="vector", access=AVAILABLE,
        download_mechanism="HTTPS GET public GitHub raw file",
        extra={"expected_units": n_expected, "authoritative": False},
    )


STATIC: tuple[Source, ...] = (
    Source(
        source_id="india-census-2011-pca", title="Census of India 2011 Primary Census Abstract (town/village)",
        category="census", year=2011,
        url="https://censusindia.gov.in/census.website/data/census-tables",
        landing_page="https://www.censusindia.gov.in/census.website/en/data/population-finder",
        edition="Census of India 2011 PCA (state/district/sub-district/village-town level)",
        license="Government of India open data; terms on censusindia.gov.in (verify per table before redistribution). "
                "Commit acquisition steps + checksums only.",
        crs="n/a (tabular)", units="persons (census counts)",
        semantics="Census COUNTS with state/district/sub-district/town-village codes. Not a raster.",
        resolution="village/town", access=MISSING,
        download_mechanism="Manual/portal download of state-wise PCA tables (human step; no scripted bypass).",
        notes="Village/town-level PCA is openly downloadable WITHOUT login per district via the Census NADA catalog "
              "(District Census Handbook Part B / PCA workbooks, e.g. https://censusindia.gov.in/nada/index.php/catalog/<id>), "
              "but no districts are selected yet (needs Agent 3's town list). State/district level is registered separately. See BLOCKER_REQUEST.md B2.",
    ),
    Source(
        source_id="india-census-2011-pca-state-district", title="Census of India 2011 PCA, India/State/District totals (DDW_PCA0000_2011_Indiastatedist.xlsx)",
        category="census", year=2011,
        url="https://censusindia.gov.in/nada/index.php/catalog/6191/download/9268/DDW_PCA0000_2011_Indiastatedist.xlsx",
        landing_page="https://censusindia.gov.in/nada/index.php/catalog/6191",
        edition="Census 2011 PCA (state & district level), NADA catalog 6191",
        license="Census of India / Government of India. No licence text was found programmatically; verify the portal terms before redistribution. "
                "Local file git-ignored; commit checksum only.",
        crs="n/a (tabular)", units="persons (census counts)",
        semantics="Census COUNTS with State/District codes (district code is NATIONALLY sequential, e.g. 532). Level 'DISTRICT', TRU Total/Rural/Urban.",
        resolution="district", access=AVAILABLE,
        download_mechanism="Public HTTPS GET, no login (HTTP 200 verified 2026-11-10).",
    ),
    Source(
        source_id="india-datameet-census2011-districts", title="DataMeet Census-2011 district polygons (641 features, Census codes)",
        category="boundary", year=2011,
        url="https://raw.githubusercontent.com/datameet/maps/master/Districts/Census_2011/2011_Dist.shp",
        landing_page="https://github.com/datameet/maps/tree/master/Districts/Census_2011",
        edition="DataMeet maps repository, Districts/Census_2011 (community-digitised; NOT an official Survey of India/Census product)",
        license="CC BY 4.0 (DataMeet repository LICENSE; attribution required)",
        crs="EPSG:4326", units="n/a (polygons)",
        semantics="District polygons with Census 2011 codes: ST_CEN_CD (state), DT_CEN_CD (within-state), censuscode (NATIONAL sequential "
                  "district code = Census PCA 'District' column). District level only: no towns or villages.",
        resolution="vector (district)", access=AVAILABLE,
        download_mechanism="Public HTTPS GET of .shp/.dbf/.shx/.prj",
        extra={"companion_urls": [f"https://raw.githubusercontent.com/datameet/maps/master/Districts/Census_2011/2011_Dist.{e}"
                                  for e in ("dbf", "shx", "prj")], "authoritative": False, "district_level_only": True},
    ),
    Source(
        source_id="india-admin-boundaries-2011", title="India 2011 administrative boundaries aligned to Census codes",
        category="boundary", year=2011, url="(town/village polygons: authoritative source to be confirmed by human; see BLOCKER_REQUEST.md B3)",
        landing_page="https://bhuvan.nrsc.gov.in/ or https://onlinemaps.surveyofindia.gov.in/",
        edition="unconfirmed", license="UNCONFIRMED – Survey of India / Census restrictions apply; do not substitute silently",
        crs="EPSG:4326 expected", units="n/a (polygons)",
        semantics="Polygons carrying 2011 Census state/district/town/village geocodes; join integrity must be tested.",
        resolution="vector", access=MISSING,
        download_mechanism="Not yet determined. Candidate open sources listed in BLOCKER_REQUEST.md.",
    ),
    Source(
        source_id="india-bhuvan-lulc-50k-2011-12", title="Bhuvan LULC 1:50,000, 2011-12 (shapefile, by AOI/district)",
        category="lulc", year=2011, url="https://bhuvan-app1.nrsc.gov.in/thematic/thematic/index.php",
        landing_page="https://bhuvan.nrsc.gov.in/updates/bhuvan_apr2026.html",
        edition="NRSC LULC 50K, agricultural year 2011-12 (Bhuvan update 2026-04-16)",
        license="National Geospatial Policy-2022 access; per-request approval, terms set by NRSC/Bhuvan",
        crs="vector shapefile (CRS to be read from delivered .prj)", units="categorical LULC class codes",
        semantics="Categorical VECTOR LULC polygons at 1:50,000 map scale. Regridding to 23.5 m adds no detail. "
                  "Use nearest-neighbour/rasterise-by-class, never averaging.",
        resolution="1:50,000 vector", access=RESTRICTED,
        download_mechanism="Portal request (<=5 districts or AOI, purpose statement); link emailed after approval.",
        notes="Human action required – see BLOCKER_REQUEST.md B1. 2011-12 is a different period from Census 2011; document.",
    ),
)


def all_sources() -> list[Source]:
    out = [_worldpop(y) for y in WORLDPOP_YEARS]
    eps = GHSL_EPOCHS + GHSL_EXTRA_EPOCHS
    out += [_ghsl_built(e, False) for e in eps]
    out += [_ghsl_built(e, True) for e in eps]
    out += [_ghsl_pop(e) for e in eps]
    out += [_geoboundaries("ADM1", 36, "CC BY 2.5 IN (DataMeet / Election Commission of India)", 2011),
            _geoboundaries("ADM2", 735, "ODbL 1.0 (Pathways Data / LGD)", 2021)]
    out += list(STATIC)
    ids = [s.source_id for s in out]
    assert len(ids) == len(set(ids)), "duplicate source ids"
    return out


def build_inventory(state: dict[str, Any] | None = None) -> dict[str, Any]:
    """Render the registry; `state` maps source_id -> {size_bytes, sha256, access, local_path, retrieved}."""
    state = state or {}
    items = []
    for s in all_sources():
        d = asdict(s)
        st = state.get(s.source_id, {})
        for k in ("size_bytes", "sha256", "retrieved_utc", "local_path"):
            d[k] = st.get(k, d.get(k))
        d.setdefault("sha256", None)
        if st.get("tiles"):
            d["fetched_tiles"] = st["tiles"]      # per-tile url/size/sha256, or status=absent (open-sea tile, HTTP 404)
        d["access"] = st.get("access", s.access)
        if d["access"] not in ACCESS_STATES:
            raise ValueError(f"bad access state {d['access']!r}")
        items.append(d)
    return {"inventory_version": 1, "count": len(items), "sources": items}


def render_markdown(inv: dict) -> str:
    rows = ["# Source inventory (generated – do not edit by hand)", "",
            "Regenerate: `cd science/data && PYTHONPATH=. python -m cgis_data inventory`. Sizes for GHSL rows are the "
            "GLOBAL zip; India needs only the tiles listed in `tiles` (see HARMONIZATION.md). `sha256` is blank until fetched.", "",
            "| source_id | year | access | units | CRS | size (bytes) | sha256 | license |", "|---|---|---|---|---|---|---|---|"]
    for r in inv["sources"]:
        sha = (r.get("sha256") or "")[:12]
        rows.append(f"| {r['source_id']} | {r['year']} | **{r['access']}** | {r['units']} | {r['crs']} | "
                    f"{r.get('size_bytes') or ''} | {sha} | {r['license'].split(';')[0][:48]} |")
    rows += ["", "## Per-source details", ""]
    for r in inv["sources"]:
        rows += [f"### {r['source_id']}", f"- Title: {r['title']}", f"- URL: {r['url']}", f"- Landing page: {r['landing_page']}",
                 f"- Edition: {r['edition']}", f"- Licence: {r['license']}", f"- Semantics: {r['semantics']}",
                 f"- Mechanism: {r['download_mechanism']}"] + ([f"- Notes: {r['notes']}"] if r["notes"] else []) + [""]
    return "\n".join(rows)
