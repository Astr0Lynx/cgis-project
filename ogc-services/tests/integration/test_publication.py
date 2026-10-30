"""Catalog-driven publication: catalog rows appear/disappear as collections with no config edits.

The test stack runs the ogc container with OGC_REFRESH_INTERVAL=3, i.e. the same
`python -m cgis_ogc.publish --refresh` command an operator would run, on a timer.
"""

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


def test_new_vector_dataset_is_published_and_scoped(http, temp_dataset):
    dataset_id = temp_dataset("vector", provenance={"ogc_publication": {"filter": {"name": "Town B"}}},
                              bbox=[78.03, 17.95, 78.035, 17.955])
    assert wait_for(lambda: dataset_id in collection_ids(http)), "new vector dataset not published"
    fc = get_json(http, f"{OGC}/collections/{dataset_id}/items?f=json")
    assert [f["id"] for f in fc["features"]] == ["town-b"]
    assert http.get(f"{OGC}/collections/{dataset_id}/items/town-a", params={"f": "json"}).status_code == 404
    assert get_json(http, f"{OGC}/collections/{dataset_id}/items/town-b?f=json")["id"] == "town-b"
    # The scoped filter does not leak into the original collection.
    assert get_json(http, f"{OGC}/collections/synthetic-towns/items?f=json")["numberMatched"] == 2


def test_new_raster_dataset_is_published_with_tiles(http, db, temp_dataset):
    dataset_id = temp_dataset("raster", asset_path=asset_path_of(db, "synthetic-worldpop-2011"))
    assert wait_for(lambda: dataset_id in collection_ids(http)), "new raster dataset not published"
    tile, pixel = tile_pixel("WebMercatorQuad", 78.03, 17.97, 13)
    resp = http.get(f"{OGC}/collections/{dataset_id}/tiles/WebMercatorQuad/{tile.z}/{tile.y}/{tile.x}",
                    params={"f": "png"})
    assert png_image(resp).getpixel(pixel)[3] == 255


def test_staged_row_is_not_published_and_removed_row_is_withdrawn(http, db, temp_dataset):
    staged = temp_dataset("vector", status="staged")
    ready = temp_dataset("vector")
    assert wait_for(lambda: ready in collection_ids(http))
    assert staged not in collection_ids(http)
    with db.cursor() as cur:
        cur.execute("DELETE FROM catalog.datasets WHERE dataset_id = %s", (ready,))
    assert wait_for(lambda: ready not in collection_ids(http)), "removed dataset still published"


def test_higher_ready_version_replaces_metadata(http, db, temp_dataset):
    dataset_id = temp_dataset("vector", title="TEST version one")
    assert wait_for(lambda: dataset_id in collection_ids(http))
    insert_dataset(db, dataset_id, "vector", version=2, title="TEST version two")

    def title():
        return get_json(http, f"{OGC}/collections/{dataset_id}?f=json")["title"]

    assert wait_for(lambda: title() == "TEST version two"), title()
