# EXPLAIN ANALYZE samples

Generated with `python -m cgis_db explain` (default `--grid 150 --datasets 5000`) against a
database with core migrations and the synthetic towns loaded. The command seeds a benchmark
grid **inside a transaction that is rolled back**, so it never leaves data behind.

How to read this honestly:

- **Section A (2 towns)**: the planner happened to pick the GiST index, but on 2 rows any plan is
  instantaneous. This section shows the query is *index-eligible*; it is **not** a performance claim.
- **Section B (≈22.5k units, 15k catalog rows, synthetic)** shows the indexes actually used at a
  size where the choice matters, and the cost of the anti-pattern (transforming the indexed column:
  seq scan over every row and a reprojection per row).
- Timings come from one developer laptop (Windows, PostgreSQL 18.4, PostGIS 3.6.2) and vary between
  runs. Compare plan *shapes* (Index Scan vs Seq Scan, rows removed), not milliseconds.
- `tests/test_explain_and_cli.py` asserts these plan shapes on a smaller grid, so a regression
  (for example, a view rewrite that stops predicate push-down) fails the test suite.

- PostgreSQL: `PostgreSQL 18.4 on x86_64-windows, compiled by msvc-19.44.35227, 64-bit`
- PostGIS: `3.6.2`; client platform: `Windows-11-10.0.26200-SP0`

## A. Tiny fixture only (expect sequential scans; no speed claims)

Rows in `geo.admin_units`: 2

### geo.admin_units bbox filter (&& + ST_Intersects)

```sql
SELECT unit_id FROM geo.admin_units u WHERE u.geom && ST_MakeEnvelope(78.010, 17.980, 78.020, 17.990, 4326) AND ST_Intersects(u.geom, ST_MakeEnvelope(78.010, 17.980, 78.020, 17.990, 4326))
```

```text
Index Scan using admin_units_geom_gist on admin_units u (actual time=0.470..0.471 rows=1.00 loops=1)
  Index Cond: ((geom && '0103000020E61000000100000005000000713D0AD7A38053407B14AE47E1FA3140713D0AD7A38053403D0AD7A370FD3140E17A14AE478153403D0AD7A370FD3140E17A14AE478153407B14AE47E1FA3140713D0AD7A38053407B14AE47E1FA3140'::geometry) AND (geom && '0103000020E61000000100000005000000713D0AD7A38053407B14AE47E1FA3140713D0AD7A38053403D0AD7A370FD3140E17A14AE478153403D0AD7A370FD3140E17A14AE478153407B14AE47E1FA3140713D0AD7A38053407B14AE47E1FA3140'::geometry))
  Filter: st_intersects(geom, '0103000020E61000000100000005000000713D0AD7A38053407B14AE47E1FA3140713D0AD7A38053403D0AD7A370FD3140E17A14AE478153403D0AD7A370FD3140E17A14AE478153407B14AE47E1FA3140713D0AD7A38053407B14AE47E1FA3140'::geometry)
  Index Searches: 1
  Buffers: shared hit=26
Planning:
  Buffers: shared hit=106
Planning Time: 2.225 ms
Execution Time: 0.577 ms
```

## B. Benchmark grid (rolled back): 22502 units, 15001 catalog rows

### geo.admin_units bbox filter (&& + ST_Intersects)

```sql
SELECT unit_id FROM geo.admin_units u WHERE u.geom && ST_MakeEnvelope(78.010, 17.980, 78.020, 17.990, 4326) AND ST_Intersects(u.geom, ST_MakeEnvelope(78.010, 17.980, 78.020, 17.990, 4326))
```

```text
Index Scan using admin_units_geom_gist on admin_units u (actual time=0.046..0.083 rows=10.00 loops=1)
  Index Cond: ((geom && '0103000020E61000000100000005000000713D0AD7A38053407B14AE47E1FA3140713D0AD7A38053403D0AD7A370FD3140E17A14AE478153403D0AD7A370FD3140E17A14AE478153407B14AE47E1FA3140713D0AD7A38053407B14AE47E1FA3140'::geometry) AND (geom && '0103000020E61000000100000005000000713D0AD7A38053407B14AE47E1FA3140713D0AD7A38053403D0AD7A370FD3140E17A14AE478153403D0AD7A370FD3140E17A14AE478153407B14AE47E1FA3140713D0AD7A38053407B14AE47E1FA3140'::geometry))
  Filter: st_intersects(geom, '0103000020E61000000100000005000000713D0AD7A38053407B14AE47E1FA3140713D0AD7A38053403D0AD7A370FD3140E17A14AE478153403D0AD7A370FD3140E17A14AE478153407B14AE47E1FA3140713D0AD7A38053407B14AE47E1FA3140'::geometry)
  Index Searches: 1
  Buffers: shared hit=8
Planning:
  Buffers: shared hit=15
Planning Time: 0.220 ms
Execution Time: 0.091 ms
```

### geo.units_in_bbox() primitive, bbox given in EPSG:3857

```sql
SELECT unit_id FROM geo.units_in_bbox(8683365, 2034290, 8684478, 2035460, 3857)
```

```text
Subquery Scan on units_in_bbox (actual time=0.293..0.294 rows=5.00 loops=1)
  Buffers: shared hit=27
  ->  Sort (actual time=0.291..0.291 rows=5.00 loops=1)
        Sort Key: u.unit_id
        Sort Method: quicksort  Memory: 25kB
        Buffers: shared hit=27
        ->  Index Scan using admin_units_geom_gist on admin_units u (actual time=0.159..0.277 rows=5.00 loops=1)
              Index Cond: ((geom && cgis.envelope_4326('8683365'::double precision, '2034290'::double precision, '8684478'::double precision, '2035460'::double precision, 3857)) AND (geom && cgis.envelope_4326('8683365'::double precision, '2034290'::double precision, '8684478'::double precision, '2035460'::double precision, 3857)))
              Filter: st_intersects(geom, cgis.envelope_4326('8683365'::double precision, '2034290'::double precision, '8684478'::double precision, '2035460'::double precision, 3857))
              Index Searches: 1
              Buffers: shared hit=27
Planning:
  Buffers: shared hit=180
Planning Time: 9.393 ms
Execution Time: 0.304 ms
```

### ST_Intersects with an EPSG:32644 (UTM 44N) AOI transformed to 4326 (query side only)

```sql
SELECT unit_id FROM geo.admin_units u, LATERAL (SELECT ST_Transform(ST_Buffer(ST_SetSRID(ST_MakePoint(184300, 1990700), 32644), 500), 4326) AS q) t WHERE ST_Intersects(u.geom, t.q)
```

```text
Index Scan using admin_units_geom_gist on admin_units u (actual time=3.405..3.454 rows=5.00 loops=1)
  Index Cond: (geom && '0103000020E61000000100000021000000D0707070888153407D5D2FCF56FB31404D7B493287815340E8EDCD031DFB3140E4F62F088381534039DB4C42E5FA3140E6C51E1B7C815340408B30AFB1FA3140A9403BAF7281534060F1D84584FA31408C7C3621678153402D8E02C55EFA3140E03CBDE259815340AD369C9D42FA3140E5A019764B81534056649AE430FA3140928831693C815340B65F53482AFA3140B7E912502D815340EDD1CC092FFA3140B9AB43BF1E8153400A853CFA3EFA31405BEF0C461181534026D3D47C59FA3140E7D3F96805815340F70DCB8C7DFA3140D4D1BF9CFB8053400BA15CC7A9FA314019B8C241F4805340686F7079DCFA3140D46360A0EF80534020714EB013FB3140A0A328E6ED805340793BC94C4DFB3140FDAA1C24EF805340CF0F1D1887FB3140FB5E074EF38053405837B7D9BEFB3140CAFDF43AFA805340C17A0F6DF2FB31406B99C5A6038153401FC6BCD61FFC314097FDCA340F8153401A8DF55745FC31409F4E58731C8153401200BC7F61FC3140225F20E02A815340E3030D3973FC314063C237ED398153408E4186D579FC3140634F890649815340986A1A1475FC3140CD5D879757815340A2A2912365FC314059ACE110658153401661BDA04AFC314068B107EE70815340C674719026FC31406C1341BA7A815340EA7D7D55FAFB3140CC1B2A1582815340169409A3C7FB314088FB67B686815340E260DC6B90FB3140D0707070888153407D5D2FCF56FB3140'::geometry)
  Filter: st_intersects(geom, '0103000020E61000000100000021000000D0707070888153407D5D2FCF56FB31404D7B493287815340E8EDCD031DFB3140E4F62F088381534039DB4C42E5FA3140E6C51E1B7C815340408B30AFB1FA3140A9403BAF7281534060F1D84584FA31408C7C3621678153402D8E02C55EFA3140E03CBDE259815340AD369C9D42FA3140E5A019764B81534056649AE430FA3140928831693C815340B65F53482AFA3140B7E912502D815340EDD1CC092FFA3140B9AB43BF1E8153400A853CFA3EFA31405BEF0C461181534026D3D47C59FA3140E7D3F96805815340F70DCB8C7DFA3140D4D1BF9CFB8053400BA15CC7A9FA314019B8C241F4805340686F7079DCFA3140D46360A0EF80534020714EB013FB3140A0A328E6ED805340793BC94C4DFB3140FDAA1C24EF805340CF0F1D1887FB3140FB5E074EF38053405837B7D9BEFB3140CAFDF43AFA805340C17A0F6DF2FB31406B99C5A6038153401FC6BCD61FFC314097FDCA340F8153401A8DF55745FC31409F4E58731C8153401200BC7F61FC3140225F20E02A815340E3030D3973FC314063C237ED398153408E4186D579FC3140634F890649815340986A1A1475FC3140CD5D879757815340A2A2912365FC314059ACE110658153401661BDA04AFC314068B107EE70815340C674719026FC31406C1341BA7A815340EA7D7D55FAFB3140CC1B2A1582815340169409A3C7FB314088FB67B686815340E260DC6B90FB3140D0707070888153407D5D2FCF56FB3140'::geometry)
  Index Searches: 1
  Buffers: shared hit=7
Planning:
  Buffers: shared hit=6
Planning Time: 1.097 ms
Execution Time: 3.461 ms
```

### ANTI-PATTERN: transforming the indexed column (index cannot be used)

```sql
SELECT unit_id FROM geo.admin_units u WHERE ST_Intersects(ST_Transform(u.geom, 3857), ST_MakeEnvelope(8683365, 2034290, 8684478, 2035460, 3857))
```

```text
Seq Scan on admin_units u (actual time=0.242..16.971 rows=5.00 loops=1)
  Filter: st_intersects(st_transform(geom, 3857), '0103000020110F00000100000005000000000000A0EC8F604100000000720A3F41000000A0EC8F604100000000040F3F41000000C07790604100000000040F3F41000000C07790604100000000720A3F41000000A0EC8F604100000000720A3F41'::geometry)
  Rows Removed by Filter: 22497
  Buffers: shared hit=735
Planning Time: 0.028 ms
Execution Time: 17.034 ms
```

### catalog.current_datasets with bbox predicate (pushes down to GiST + PK anti-join)

```sql
SELECT dataset_id, version FROM catalog.current_datasets WHERE bbox && ST_MakeEnvelope(78.010, 17.980, 78.020, 17.990, 4326)
```

```text
Nested Loop Anti Join (actual time=0.016..0.017 rows=1.00 loops=1)
  Buffers: shared hit=6
  ->  Index Scan using datasets_bbox_gist on datasets d (actual time=0.012..0.012 rows=1.00 loops=1)
        Index Cond: (bbox && '0103000020E61000000100000005000000713D0AD7A38053407B14AE47E1FA3140713D0AD7A38053403D0AD7A370FD3140E17A14AE478153403D0AD7A370FD3140E17A14AE478153407B14AE47E1FA3140713D0AD7A38053407B14AE47E1FA3140'::geometry)
        Filter: (status = 'ready'::text)
        Index Searches: 1
        Buffers: shared hit=4
  ->  Index Only Scan using datasets_ready_latest_idx on datasets n (actual time=0.004..0.004 rows=0.00 loops=1)
        Index Cond: ((dataset_id = d.dataset_id) AND (version > d.version))
        Heap Fetches: 0
        Index Searches: 1
        Buffers: shared hit=2
Planning:
  Buffers: shared hit=110
Planning Time: 0.478 ms
Execution Time: 0.030 ms
```

### catalog.resolve_dataset() for one id

```sql
SELECT dataset_id, version FROM catalog.resolve_dataset('bench-ds-00042')
```

```text
Nested Loop Anti Join (actual time=0.009..0.010 rows=1.00 loops=1)
  Join Filter: (n.version > d.version)
  Rows Removed by Join Filter: 2
  Buffers: shared hit=8
  ->  Index Only Scan using datasets_ready_latest_idx on datasets d (actual time=0.005..0.005 rows=2.00 loops=1)
        Index Cond: (dataset_id = 'bench-ds-00042'::text)
        Heap Fetches: 2
        Index Searches: 1
        Buffers: shared hit=4
  ->  Materialize (actual time=0.001..0.002 rows=1.50 loops=2)
        Storage: Memory  Maximum Storage: 17kB
        Buffers: shared hit=4
        ->  Index Only Scan using datasets_ready_latest_idx on datasets n (actual time=0.001..0.002 rows=2.00 loops=1)
              Index Cond: (dataset_id = 'bench-ds-00042'::text)
              Heap Fetches: 2
              Index Searches: 1
              Buffers: shared hit=4
Planning Time: 0.119 ms
Execution Time: 0.017 ms
```
