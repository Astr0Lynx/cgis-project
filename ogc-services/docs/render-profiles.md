# Shared raster display scales ("render profiles")

Completion requirement P3: map tiles of different years must be visually comparable. Before this
change OGC API - Tiles drew every raster with its own `min..max`, so 2001 and 2011 of the same product
used different colour scales.

## Mechanism

`catalog.render_profiles` (database migration `core/0008`) holds fixed, documented display scales.
`catalog.render_profile_for(dataset_id, version)` returns the profile of one raster dataset version:

* the dataset's catalog `units` must **equal** the profile's `units` (a count scale is never applied to a
  density or to built-up area);
* the remaining matchers (`match_product_level`, `match_dataset_regex`, `match_group_id` against
  `provenance.group_id`) must all match where set;
* the lowest `priority`, then `profile_id`, wins.

The OGC publisher (`cgis_ogc.publish`) applies a matching profile to every raster collection. Precedence:
**render profile > the dataset's own `provenance.ogc_publication.render.rescale` hint > per-dataset
automatic range**. The automatic range (min(0, minimum)..maximum from TiTiler statistics) is labelled as a
per-layer *diagnostic* scale that is not comparable across datasets.

Profiles are display only: they never change pixel values, zonal statistics or exports. NoData is always
transparent; values outside the range are clamped to the end colours.

## Seeded profiles

| profile_id | matches | units | range | why |
|---|---|---|---|---|
| `level1-people-per-cell` | `product_level = level1` | `people_per_cell` | 0..50 | 50 people per ~1 ha (3 arc-second) cell ≈ 5 000 people/km², above the GHSL Degree of Urbanisation urban-centre threshold (1 500/km²). Dense urban cores saturate; village and peri-urban densities (1..20 per cell) stay distinguishable. The national Level 1 rasters have mean ≈ 2.6 and a heavy urban tail (Agent 2 evaluation). |
| `worldpop-people-per-cell` | `india-worldpop-unconstrained-YYYY[-region]` | `people_per_cell` | 0..50 | Same scale as Level 1, so input and redistributed output compare directly. |
| `ghsl-pop-people-per-cell` | `india-ghsl-pop-YYYY[-region]` | `people_per_cell` | 0..50 | Same scale for the evaluation reference. |
| `ghsl-built-m2-per-cell` | `india-ghsl-built-(total\|nres)-YYYY[-region]` | `m2_per_cell` | 0..10 000 | The physical maximum of a 100 m cell; a 3 arc-second cell is ≈ 8 500 m² at 23° N. |
| `level2-people-per-cell` | `product_level = level2` | `people_per_cell` | 0..2.76125 | The same density range as Level 1, expressed per 23.5 m cell: 50 × (23.5/100)². Equal colours mean similar *density*; counts per cell differ because the supports differ. |

Synthetic Phase 2 fixtures (`synthetic-worldpop-*`, `product_level = source`) match no profile and keep the
automatic scale, so existing tests and visuals are unchanged.

Operators may add or edit rows (owner role); `ON CONFLICT DO NOTHING` in the migration never overwrites an
edited profile. Changes reach OGC on the next publisher refresh (`OGC_REFRESH_INTERVAL`, default 30 s) and
the tile service within 60 s.

## Where clients see it

* **OGC API - Tiles** tileset metadata, `style` (the standard place for the portrayal rule):
  `title` = `"<colormap> linear <lo>..<hi>"` (parsed by `demo-client/js/legend.js`), `description`
  contains `configured shared range from render profile '<id>' (...; units <units>)` for shared scales,
  or `per-layer automatic scale for diagnostics; not comparable across datasets` otherwise.
* **OGC collection description** gains the fact `display scale <profile_id> <lo>..<hi>` (and `group <id>`
  for grouped Level 2 towns) before `source ...`; keywords are unchanged.
* **Vendor TiTiler** `GET /tiles/datasets/{id}` (linked from the collection as `rel=describedby`, labelled
  vendor-specific) returns `render: {mode: "shared", profile_id, title, description, units, colormap,
  rescale, clamped, nodata: "transparent", titiler_params}` or `{mode: "per_dataset_autoscale", ...}`, plus
  `product_level`, `resolution`, `group_id`, `synthetic`, `license`, `citation`. `titiler_params` reproduce
  the OGC rendering on `/tiles/` routes. Per-layer statistics stay available at `./statistics`.
