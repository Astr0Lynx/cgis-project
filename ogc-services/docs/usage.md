# Using the OGC services: publication, curl, QGIS

All examples assume the gateway at `http://localhost:8080` (project default). Against the Agent 4
test stack use `http://127.0.0.1:18080` from the host; links inside responses then say
`http://gateway:8080` because the stack's `OGC_PUBLIC_URL` targets in-network clients (CITE, tests).

All data in Phase 2 is **SYNTHETIC** (`deterministic-fixture`); values are not population estimates.

## 1. Publication model (catalog → collections)

- Every row of `catalog.current_datasets` with `status = 'ready'` becomes a collection whose id is the
  catalog `dataset_id`. Nothing is listed by hand in any configuration file.
- `kind = 'vector'` → OGC API Features collection over `geo.admin_units`. If the table has a
  `dataset_id` column (proposed in `docs/contract-change-requests/agent4.md`) rows are scoped by it;
  otherwise an optional catalog hint `provenance.ogc_publication.filter` (`{"column": value}` equality,
  columns must exist) scopes them; otherwise the whole table is published.
- `kind = 'raster'` with an `asset_path` → OGC API Tiles map tilesets (PNG) in WebMercatorQuad and
  WorldCRS84Quad; zoom range from the catalog resolution; extent from the catalog bbox. Optional hint
  `provenance.ogc_publication.render.rescale = [lo, hi]` fixes the colour range.
- Rows that are `staged`/`failed`, have unsafe ids, no bbox, or rasters without `asset_path` are skipped
  with a log line.

```bash
# apply catalog changes now (reloads gunicorn only if the generated config changed)
docker compose exec ogc /cgis/entrypoint.sh refresh
# or let OGC_REFRESH_INTERVAL=<seconds> do it periodically
# inspect what would be published
docker compose exec ogc /venv/bin/python -m cgis_ogc.publish --dry-run
```

## 2. curl: OGC API - Features

```bash
B=http://localhost:8080/ogc
curl -s "$B/?f=json"                                   # landing page
curl -s "$B/conformance?f=json"                        # declared conformance classes
curl -s "$B/collections?f=json"                        # catalog datasets as collections
curl -s "$B/collections/synthetic-towns?f=json"
curl -s "$B/collections/synthetic-towns/items?f=json"                      # Town A + Town B
curl -s "$B/collections/synthetic-towns/items/town-a?f=json"
curl -s "$B/collections/synthetic-towns/items?f=json&bbox=78.009,17.979,78.021,17.991"   # Town A only
curl -s "$B/collections/synthetic-towns/items?f=json&limit=1"                            # follow rel=next
curl -s -D - -o /dev/null "$B/collections/synthetic-towns/items?crs=http://www.opengis.net/def/crs/EPSG/0/4326"  # Content-Crs, lat/lon
curl -s "$B/collections/synthetic-towns/items?f=json&filter=name%3D'Town%20B'&filter-lang=cql2-text"
curl -s "$B/collections/synthetic-towns/queryables?f=json"
curl -s "$B/openapi?f=json"
curl -s -w '%{http_code}\n' -o /dev/null "$B/collections/synthetic-towns/items/no-such-town"   # 404
curl -s -w '%{http_code}\n' -o /dev/null "$B/collections/synthetic-towns/items?bbox=1,2,3"     # 400
```

Response CRS: default `http://www.opengis.net/def/crs/OGC/1.3/CRS84` (lon/lat). With
`crs=http://www.opengis.net/def/crs/EPSG/0/4326` coordinates are lat/lon, per OGC 18-058.

## 3. curl: raster tiles

OGC API - Tiles (standard):

```bash
C=http://localhost:8080/ogc/collections/synthetic-worldpop-2001
curl -s "$C/tiles?f=json"                              # tilesets list
curl -s "$C/tiles/WebMercatorQuad?f=json"              # TileSetMetadata (limits, style, item template)
curl -s "$C/tiles/WebMercatorQuad?f=tilejson"          # TileJSON 3.0 alternative
curl -s "http://localhost:8080/ogc/TileMatrixSets/WebMercatorQuad?f=json"
curl -s -o tile.png "$C/tiles/WebMercatorQuad/13/3680/5871?f=png"     # {tileMatrix}/{tileRow}/{tileCol}
curl -s -o tile.png -H 'Accept: image/png' "$C/tiles/WorldCRS84Quad/12/1639/5871"
```

TiTiler (vendor-specific, not OGC):

```bash
T=http://localhost:8080/tiles
curl -s "$T/datasets"                                   # servable rasters
curl -s "$T/datasets/synthetic-worldpop-2001/WebMercatorQuad/tilejson.json"
curl -s -o v.png "$T/datasets/synthetic-worldpop-2011/tiles/WebMercatorQuad/13/5871/3680.png?rescale=0,2&colormap_name=viridis"  # {z}/{x}/{y}
curl -s "$T/datasets/synthetic-worldpop-2011/point/78.015,17.985"     # {"values":[2.0]}
curl -s "$T/datasets/synthetic-worldpop-2001/info"
curl -s -w '%{http_code}\n' "$T/datasets/no-such-dataset/info"         # 404 dataset_not_found
```

There is deliberately no `url=` parameter and no `/cog`, `/stac` or `/mosaic` endpoint.

## 4. QGIS

### Connection steps (QGIS ≥ 3.28)

1. **Vector (OGC API - Features):** *Layer → Add Layer → Add WFS / OGC API - Features Layer…* → *New*,
   URL `http://localhost:8080/ogc`, version *OGC API - Features* → *Detect* → *Connect*. Add
   `synthetic-towns`: two MultiPolygons, attributes `unit_id`, `name`, `level`.
   (Python: `QgsVectorLayer("url='http://localhost:8080/ogc' typename='synthetic-towns'", "towns", "OAPIF")`.)
2. **Raster map tiles (OGC API - Tiles):** QGIS has no raster OGC API - Tiles client; use the standard
   tile URI template from the tileset metadata as an XYZ source: *Browser → XYZ Tiles → New Connection*,
   URL `http://localhost:8080/ogc/collections/synthetic-worldpop-2001/tiles/WebMercatorQuad/{z}/{y}/{x}?f=png`,
   max zoom `13`. (Note `{y}/{x}`: the OGC template is `{tileMatrix}/{tileRow}/{tileCol}`.)
3. **Raster via TiTiler (vendor):** XYZ connection with
   `http://localhost:8080/tiles/datasets/synthetic-worldpop-2001/tiles/WebMercatorQuad/{z}/{x}/{y}.png?rescale=0,2&colormap_name=viridis`.

Expected: the towns overlay the yellow valid area of the raster; the 4-cell NoData strip along the
north and west edges is transparent.

### What was verified

- **Run:** headless QGIS **3.44.15 LTR** (`qgis/qgis:3.44.15` image, offscreen Qt) against the test stack,
  `tests/qgis/check_qgis.py`, transcript in `docs/evidence/qgis-check.txt` — all 10 checks pass:
  QGIS's own OAPIF provider loads `synthetic-towns` (2 MultiPolygons, `OGC:CRS84`, Town A extent exact,
  spatially filtered request returns Town A only); an XYZ layer on the OGC API - Tiles URI template
  loads and renders; the valid area is viridis-maximum yellow, both NoData strips are transparent, and
  the valid/NoData boundary falls at 78.004°E as defined by the fixture.
- **Not run:** the QGIS desktop GUI. The workstation's QGIS 4.2.3 build fails to load
  (`libqgis_core` Qt WebEngine symbol error), so the interactive steps above are documented, not
  demonstrated. QGIS's *OGC API - Tiles* client support was not used for rasters (QGIS has none);
  the XYZ route consumes the standard tile template.
