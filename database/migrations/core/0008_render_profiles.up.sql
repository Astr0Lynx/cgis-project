-- 0008: shared raster portrayal ("render") profiles (completion P3 / acceptance row P3).
--
-- Problem: OGC API - Tiles rendered each raster with its own min..max, so two years of the same
-- product were drawn with different colour scales and could not be compared by eye.
--
-- A render profile is a FIXED, documented display scale shared by every raster it matches:
-- colormap + linear range [rescale_min, rescale_max] in the profile's units (values outside are
-- clamped to the end colours; NoData is always transparent). A profile only applies when the
-- dataset's catalog `units` EQUAL the profile's `units`, so a count scale is never applied to a
-- density or to a built-up area raster. Matching is by product_level, dataset_id regex and/or
-- provenance.group_id; the lowest priority number wins, ties broken by profile_id.
--
-- Profiles are DISPLAY ONLY. They never change data values, statistics or exports.
-- Datasets without a matching profile keep per-dataset automatic scaling, which is labelled as
-- such (diagnostic, not comparable across datasets).

CREATE TABLE IF NOT EXISTS catalog.render_profiles (
    profile_id           text              NOT NULL,
    priority             integer           NOT NULL DEFAULT 100,
    match_product_level  text,
    match_dataset_regex  text,
    match_group_id       text,
    units                text              NOT NULL,
    colormap             text              NOT NULL DEFAULT 'viridis',
    rescale_min          double precision  NOT NULL,
    rescale_max          double precision  NOT NULL,
    title                text              NOT NULL,
    description          text              NOT NULL,
    legend               jsonb             NOT NULL DEFAULT '{}'::jsonb,
    created_at           timestamptz       NOT NULL DEFAULT now(),
    updated_at           timestamptz       NOT NULL DEFAULT now(),

    CONSTRAINT render_profiles_pkey PRIMARY KEY (profile_id),
    CONSTRAINT render_profiles_id_slug CHECK (profile_id ~ '^[a-z0-9]([a-z0-9._-]{0,62}[a-z0-9])?$'),
    CONSTRAINT render_profiles_has_matcher CHECK (
        match_product_level IS NOT NULL OR match_dataset_regex IS NOT NULL OR match_group_id IS NOT NULL),
    CONSTRAINT render_profiles_level_valid CHECK (
        match_product_level IS NULL OR match_product_level IN ('source', 'level1', 'level2', 'other')),
    CONSTRAINT render_profiles_units_nonblank CHECK (btrim(units) <> ''),
    CONSTRAINT render_profiles_colormap_name CHECK (colormap ~ '^[a-z][a-z0-9_]{0,31}$'),
    CONSTRAINT render_profiles_range_valid CHECK (
        rescale_max > rescale_min
        AND rescale_min > '-Infinity'::float8 AND rescale_max < 'Infinity'::float8),
    CONSTRAINT render_profiles_legend_object CHECK (jsonb_typeof(legend) = 'object')
);

COMMENT ON TABLE catalog.render_profiles IS
    'Fixed shared display scales for raster tiles (display only). Applies only when catalog units equal profile units.';

CREATE OR REPLACE FUNCTION catalog.render_profiles_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.match_dataset_regex IS NOT NULL THEN
        BEGIN
            PERFORM '' ~ NEW.match_dataset_regex;
        EXCEPTION WHEN invalid_regular_expression THEN
            RAISE EXCEPTION 'render profile %: invalid match_dataset_regex', NEW.profile_id
                USING ERRCODE = 'invalid_parameter_value';
        END;
    END IF;
    NEW.updated_at := now();
    IF TG_OP = 'INSERT' THEN
        NEW.created_at := now();
    END IF;
    RETURN NEW;
END
$$;

CREATE OR REPLACE TRIGGER render_profiles_guard
    BEFORE INSERT OR UPDATE ON catalog.render_profiles
    FOR EACH ROW EXECUTE FUNCTION catalog.render_profiles_guard();

-- The profile for one dataset version (default: its current ready version); 0 or 1 rows.
CREATE OR REPLACE FUNCTION catalog.render_profile_for(p_dataset_id text, p_version integer DEFAULT NULL)
RETURNS SETOF catalog.render_profiles
LANGUAGE sql STABLE PARALLEL SAFE
AS $$
    SELECT p.*
      FROM catalog.datasets d
      JOIN catalog.render_profiles p
        ON p.units = d.units
       AND (p.match_product_level IS NULL OR p.match_product_level = d.product_level)
       AND (p.match_dataset_regex IS NULL OR d.dataset_id ~ p.match_dataset_regex)
       AND (p.match_group_id IS NULL OR d.provenance ->> 'group_id' = p.match_group_id)
     WHERE d.dataset_id = $1
       AND d.kind = 'raster'
       AND d.version = coalesce($2, (SELECT c.version FROM catalog.current_datasets c
                                      WHERE c.dataset_id = $1))
     ORDER BY p.priority, p.profile_id
     LIMIT 1
$$;

-- Seed profiles. ON CONFLICT DO NOTHING: an operator's later edits are never overwritten.
--
-- Choice of range (documented in ogc-services/docs/render-profiles.md):
--  * people_per_cell on the ~100 m (3 arc-second) grids (WorldPop, GHS-POP, Level 1): 0..50.
--    50 people per ~1 ha cell is ~5 000 people/km^2, above the GHSL Degree-of-Urbanisation
--    urban-centre density threshold (1 500/km^2), so dense urban cores saturate at the top colour
--    while village/peri-urban densities (1..20 per cell) remain distinguishable. India-wide Level 1
--    values have mean ~2.6 and RMSE ~19 people per cell (Agent 2 evaluation), heavy-tailed.
--  * people_per_cell on the ~23.5 m Level 2 target grid: the SAME density range expressed per
--    23.5 m cell: 50 x (23.5/100)^2 = 2.76125 people per cell, so equal colours mean ~equal density
--    across Level 1 and Level 2 (they are NOT equal counts per cell; supports differ).
--  * GHSL built-up surface, m2_per_cell: 0..10 000 m^2, the physical maximum of a 100 m cell
--    (the 3 arc-second cell is smaller, ~8 500 m^2 at 23 N, so 100 % built-up is near the top).
INSERT INTO catalog.render_profiles AS p
    (profile_id, priority, match_product_level, match_dataset_regex, match_group_id, units,
     colormap, rescale_min, rescale_max, title, description, legend)
VALUES
    ('level1-people-per-cell', 100, 'level1', NULL, NULL, 'people_per_cell', 'viridis', 0, 50,
     'Population per ~100 m cell (shared Level 1 scale)',
     'Shared fixed scale for all Level 1 years: 0..50 people per ~100 m cell (~0..5 000 people/km2); '
     'values above 50 are drawn with the top colour.',
     '{"density_equivalent_people_per_km2": [0, 5000], "clamped_above": true, "grid": "~100 m (3 arc-second)"}'),
    ('worldpop-people-per-cell', 100, 'source', '^india-worldpop-unconstrained-[0-9]{4}(-[a-z0-9-]+)?$', NULL,
     'people_per_cell', 'viridis', 0, 50,
     'Population per ~100 m cell (WorldPop input, Level 1 scale)',
     'Same fixed scale as Level 1 so the WorldPop input and the redistributed Level 1 output compare directly.',
     '{"density_equivalent_people_per_km2": [0, 5000], "clamped_above": true, "grid": "~100 m (3 arc-second)"}'),
    ('ghsl-pop-people-per-cell', 100, 'source', '^india-ghsl-pop-[0-9]{4}(-[a-z0-9-]+)?$', NULL,
     'people_per_cell', 'viridis', 0, 50,
     'Population per ~100 m cell (GHS-POP reference, Level 1 scale)',
     'Same fixed scale as Level 1 for evaluation views.',
     '{"density_equivalent_people_per_km2": [0, 5000], "clamped_above": true, "grid": "~100 m (3 arc-second)"}'),
    ('ghsl-built-m2-per-cell', 100, 'source', '^india-ghsl-built-(total|nres)-[0-9]{4}(-[a-z0-9-]+)?$', NULL,
     'm2_per_cell', 'viridis', 0, 10000,
     'Built-up surface per cell (GHSL, shared scale)',
     'Shared fixed scale for GHSL built-up surface (total and non-residential, all epochs): 0..10 000 m2 per cell.',
     '{"clamped_above": true, "grid": "~100 m (3 arc-second)"}'),
    ('level2-people-per-cell', 100, 'level2', NULL, NULL, 'people_per_cell', 'viridis', 0, 2.76125,
     'Population per ~23.5 m cell (shared Level 2 scale)',
     'Shared fixed scale for all Level 2 towns: 0..2.76125 people per 23.5 m cell, the same density range '
     '(~0..5 000 people/km2) as the Level 1 scale. Equal colours mean similar density, not equal counts.',
     '{"density_equivalent_people_per_km2": [0, 5000], "clamped_above": true, "grid": "~23.5 m target grid"}')
ON CONFLICT (profile_id) DO NOTHING;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'cgis_reader') THEN
        GRANT SELECT ON catalog.render_profiles TO cgis_reader;
        GRANT EXECUTE ON FUNCTION catalog.render_profile_for(text, integer) TO cgis_reader;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'cgis_processing') THEN
        GRANT SELECT ON catalog.render_profiles TO cgis_processing;
        GRANT EXECUTE ON FUNCTION catalog.render_profile_for(text, integer) TO cgis_processing;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'cgis_ingest') THEN
        GRANT SELECT ON catalog.render_profiles TO cgis_ingest;
    END IF;
END
$$;
