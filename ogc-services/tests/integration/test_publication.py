# Integration tests: catalog rows appear/disappear as collections with no config edits. Needs the ogc
# service running with OGC_REFRESH_INTERVAL set (the periodic `python -m cgis_ogc.publish --refresh`).

from conftest import (
    OGC,
    asset_path_of,
    collection_ids,
    get_json,
    insert_dataset,
    png_image,
    tile_pixel,
    wait_for,
)


# A new vector row is published and scoped to its own rows only.
def test_new_vector_dataset_is_published_and_scoped(http, db, temp_dataset):
    dataset_id = temp_dataset("vector", provenance={"ogc_publication": {"filter": {"name": "Town B"}}},
                              bbox=[78.03, 17.95, 78.035, 17.955])
    with db.cursor() as cur:
        cur.execute("SELECT 1 FROM information_schema.columns WHERE table_schema='geo' "
                    "AND table_name='admin_units' AND column_name='dataset_id'")
        has_dataset_id = cur.fetchone() is not None
    unit = "town-b"
    if has_dataset_id:
        # With a dataset_id column the publisher scopes by dataset_id = <catalog id> (preferred over
        # the provenance hint), so this new dataset needs its own unit row.
        unit = f"{dataset_id}-unit"
        with db.cursor() as cur:
            cur.execute("INSERT INTO geo.admin_units (unit_id, name, level, geom, dataset_id, dataset_version) "
                        "VALUES (%s, 'Town B', 'synthetic_town', ST_Multi(ST_MakeEnvelope(78.03, 17.95, 78.035, 17.955, 4326)), %s, 1)",
                        (unit, dataset_id))
    try:
        assert wait_for(lambda: dataset_id in collection_ids(http)), "new vector dataset not published"
        fc = get_json(http, f"{OGC}/collections/{dataset_id}/items?f=json")
        assert [f["id"] for f in fc["features"]] == [unit]
        assert http.get(f"{OGC}/collections/{dataset_id}/items/town-a", params={"f": "json"}).status_code == 404
        assert get_json(http, f"{OGC}/collections/{dataset_id}/items/{unit}?f=json")["id"] == unit
        # The scoped filter does not leak into the original collection.
        assert get_json(http, f"{OGC}/collections/synthetic-towns/items?f=json")["numberMatched"] == 2
    finally:
        if has_dataset_id:
            with db.cursor() as cur:
                cur.execute("DELETE FROM geo.admin_units WHERE unit_id = %s", (unit,))


# A new raster row is published and its tiles render.
def test_new_raster_dataset_is_published_with_tiles(http, db, temp_dataset):
    dataset_id = temp_dataset("raster", asset_path=asset_path_of(db, "synthetic-worldpop-2011"))
    assert wait_for(lambda: dataset_id in collection_ids(http)), "new raster dataset not published"
    tile, pixel = tile_pixel("WebMercatorQuad", 78.03, 17.97, 13)
    resp = http.get(f"{OGC}/collections/{dataset_id}/tiles/WebMercatorQuad/{tile.z}/{tile.y}/{tile.x}",
                    params={"f": "png"})
    assert png_image(resp).getpixel(pixel)[3] == 255


# Staged rows stay unpublished; deleted rows are withdrawn.
def test_staged_row_is_not_published_and_removed_row_is_withdrawn(http, db, temp_dataset):
    staged = temp_dataset("vector", status="staged")
    ready = temp_dataset("vector")
    assert wait_for(lambda: ready in collection_ids(http))
    assert staged not in collection_ids(http)
    with db.cursor() as cur:
        cur.execute("DELETE FROM catalog.datasets WHERE dataset_id = %s", (ready,))
    assert wait_for(lambda: ready not in collection_ids(http)), "removed dataset still published"


# A higher ready version replaces the collection metadata.
def test_higher_ready_version_replaces_metadata(http, db, temp_dataset):
    dataset_id = temp_dataset("vector", title="TEST version one")
    assert wait_for(lambda: dataset_id in collection_ids(http))
    insert_dataset(db, dataset_id, "vector", version=2, title="TEST version two")

    # Current title of the test collection.
    def title():
        return get_json(http, f"{OGC}/collections/{dataset_id}?f=json")["title"]

    assert wait_for(lambda: title() == "TEST version two"), title()
