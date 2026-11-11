# Integration tests: version-scoped OGC Features collections, collection-scoped item links, full paging.
# Need the version-aware geo.admin_units of migration core/0007 (identity on dataset_id, dataset_version,
# unit_id); skipped when that migration is not applied.

import uuid

import pytest
from conftest import OGC, collection_ids, get_json, insert_dataset, link, wait_for

GRID_SIDE = 50   # 2 500 features: more than pygeoapi's max_items (1 000) -> at least 3 pages


# Skips the module unless geo.admin_units has the version-aware identity constraint.
@pytest.fixture(scope="module")
def versioned(db):
    with db.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_constraint WHERE conname = 'admin_units_identity'")
        if cur.fetchone() is None:
            pytest.skip("geo.admin_units has no version-aware identity (database core/0007 not applied)")


# Factory for vector datasets with units; removes units then catalog rows afterwards.
@pytest.fixture
def boundary_sets(db, versioned):
    created = []

    # Inserts one staged dataset version with the given unit ids, then marks it ready; returns its id.
    def make(units, *, dataset_id=None, version=1, title=None, x0=78.0):
        dataset_id = dataset_id or f"itest-bnd-{uuid.uuid4().hex[:8]}"
        n = max(1, len(units))
        insert_dataset(db, dataset_id, "vector", version=version, status="staged",
                       bbox=[x0, 17.0, x0 + 0.01 * n, 17.01], title=title or f"TEST boundaries v{version}")
        with db.cursor() as cur:
            cur.executemany(
                "INSERT INTO geo.admin_units (unit_id, name, level, geom, dataset_id, dataset_version) "
                "VALUES (%s, %s, 'itest', ST_Multi(ST_MakeEnvelope(%s, 17.0, %s, 17.01, 4326)), %s, %s)",
                [(u, f"Unit {u}", x0 + 0.01 * i, x0 + 0.01 * (i + 1), dataset_id, version)
                 for i, u in enumerate(units)])
            cur.execute("UPDATE catalog.datasets SET status = 'ready' WHERE dataset_id = %s AND version = %s",
                        (dataset_id, version))
        created.append(dataset_id)
        return dataset_id

    yield make
    with db.cursor() as cur:
        for dataset_id in set(created):
            cur.execute("DELETE FROM geo.admin_units WHERE dataset_id = %s", (dataset_id,))
            cur.execute("DELETE FROM catalog.datasets WHERE dataset_id = %s", (dataset_id,))


# GET a collection's items as JSON with extra query params.
def items(http, dataset_id, **params):
    return get_json(http, f"{OGC}/collections/{dataset_id}/items", params={"f": "json", **params})


# Waits until the collection is published and its metadata satisfies predicate.
def published(http, dataset_id, predicate=lambda c: True):
    # One poll: published and matching.
    def check():
        if dataset_id not in collection_ids(http):
            return False
        return predicate(get_json(http, f"{OGC}/collections/{dataset_id}?f=json"))
    return wait_for(check)


# A collection serves only its current version; withdrawing v2 republishes v1.
def test_collection_serves_only_the_current_version(http, db, boundary_sets):
    ds = boundary_sets(["u1", "u2"], title="TEST boundaries v1")
    assert published(http, ds)
    boundary_sets(["u1", "u3"], dataset_id=ds, version=2, title="TEST boundaries v2", x0=79.0)
    assert published(http, ds, lambda c: c["title"] == "TEST boundaries v2"), "v2 not published"

    fc = items(http, ds)
    assert fc["numberMatched"] == 2 and sorted(f["id"] for f in fc["features"]) == ["u1", "u3"]
    u1 = get_json(http, f"{OGC}/collections/{ds}/items/u1?f=json")
    assert min(p[0] for p in u1["geometry"]["coordinates"][0][0]) == pytest.approx(79.0), "v2 geometry"
    assert http.get(f"{OGC}/collections/{ds}/items/u2", params={"f": "json"}).status_code == 404

    # v1 rows are kept in the database, not overwritten
    with db.cursor() as cur:
        cur.execute("SELECT dataset_version, count(*) FROM geo.admin_units WHERE dataset_id = %s"
                    " GROUP BY 1 ORDER BY 1", (ds,))
        assert cur.fetchall() == [(1, 2), (2, 2)]
        # withdrawal of v2 republishes v1
        cur.execute("UPDATE catalog.datasets SET status = 'failed' WHERE dataset_id = %s AND version = 2", (ds,))
    assert published(http, ds, lambda c: c["title"] == "TEST boundaries v1"), "withdrawal not applied"
    assert sorted(f["id"] for f in items(http, ds)["features"]) == ["u1", "u2"]


# Item lookup and prev/next links never leave the collection; no self-links at the ends.
def test_item_lookup_and_neighbours_stay_in_the_collection(http, boundary_sets):
    a = boundary_sets(["k-1", "k-3", "k-5"])
    b = boundary_sets(["k-1", "k-2", "k-4", "k-6"], x0=80.0)
    assert published(http, a) and published(http, b)

    mid = get_json(http, f"{OGC}/collections/{a}/items/k-3?f=json")
    assert link(mid, "prev")["href"].startswith(f"{OGC}/collections/{a}/items/k-1")
    assert link(mid, "next")["href"].startswith(f"{OGC}/collections/{a}/items/k-5")
    first = get_json(http, f"{OGC}/collections/{a}/items/k-1?f=json")
    assert min(p[0] for p in first["geometry"]["coordinates"][0][0]) == pytest.approx(78.0), "own row, not b's"
    assert "prev" not in {lk["rel"] for lk in first["links"]}, "no self-pointing prev at the start"
    last = get_json(http, f"{OGC}/collections/{a}/items/k-5?f=json")
    assert "next" not in {lk["rel"] for lk in last["links"]}
    assert http.get(f"{OGC}/collections/{a}/items/k-2", params={"f": "json"}).status_code == 404


# limit above max_items is throttled and next links page through every feature exactly once.
def test_paging_covers_every_feature_without_truncation(http, boundary_sets):
    ids = [f"p-{i:05d}" for i in range(GRID_SIDE * GRID_SIDE)]
    ds = boundary_sets(ids, x0=70.0)
    assert published(http, ds)

    page = items(http, ds, limit=5000)       # above max_items: throttled, never silently cut
    assert page["numberMatched"] == len(ids) and page["numberReturned"] == 1000
    seen, pages = [], 0
    url = f"{OGC}/collections/{ds}/items?f=json&limit=1000"
    while url:
        doc = get_json(http, url)
        pages += 1
        seen += [f["id"] for f in doc["features"]]
        nxt = [lk["href"] for lk in doc["links"] if lk["rel"] == "next"]
        assert all(f"/collections/{ds}/items" in h for h in nxt), "next link left the collection"
        url = nxt[0] if nxt else None
        assert pages <= 5
    assert pages == 3 and seen == sorted(ids) and len(set(seen)) == len(ids)


# bbox filtering combines with paging (numberMatched and next page).
def test_bbox_filter_with_paging(http, boundary_sets):
    ids = [f"b-{i:04d}" for i in range(300)]
    ds = boundary_sets(ids, x0=60.0)
    assert published(http, ds)
    # units i=100..199 lie in [61.0, 62.0]; touching neighbours 99 and 200 intersect the bbox edges
    fc = items(http, ds, bbox="61.0005,17.0,61.9995,17.01", limit=50)
    assert fc["numberMatched"] == 100 and fc["numberReturned"] == 50
    assert fc["features"][0]["id"] == "b-0100"
    second = get_json(http, link(fc, "next")["href"])
    assert [f["id"] for f in second["features"]][-1] == "b-0199"
