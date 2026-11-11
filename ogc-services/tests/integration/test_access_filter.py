"""Agent 5 CR R5: datasets marked admins-only in cgis_app.dataset_access are not published by OGC.

Runs against the integrated database. If the application API schema is absent, the test creates a
minimal stand-in table (and removes it afterwards); the read grant mirrors Compose's ``app-grants``.
"""

import pytest
from conftest import collection_ids, wait_for


@pytest.fixture
def access_table(db):
    with db.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_roles WHERE rolname = 'cgis_reader'")
        if cur.fetchone() is None:
            pytest.skip("needs the database migrations' cgis_reader role (integrated stack)")
        cur.execute("SELECT to_regclass('cgis_app.dataset_access') IS NOT NULL")
        existed = cur.fetchone()[0]
        if not existed:
            cur.execute("CREATE SCHEMA IF NOT EXISTS cgis_app")
            cur.execute("CREATE TABLE cgis_app.dataset_access (dataset_id text PRIMARY KEY, access text NOT NULL)")
        cur.execute("GRANT USAGE ON SCHEMA cgis_app TO cgis_reader")
        cur.execute("GRANT SELECT (dataset_id, access) ON cgis_app.dataset_access TO cgis_reader")
    yield
    if not existed:
        with db.cursor() as cur:
            cur.execute("DROP TABLE cgis_app.dataset_access")
            cur.execute("DROP SCHEMA IF EXISTS cgis_app")


def test_admins_only_dataset_is_withheld_from_ogc(http, db, temp_dataset, access_table):
    hidden = temp_dataset("vector", status="ready")
    with db.cursor() as cur:
        cur.execute("INSERT INTO cgis_app.dataset_access (dataset_id, access) VALUES (%s, 'admins')", (hidden,))
    try:
        visible = temp_dataset("vector", status="ready")
        assert wait_for(lambda: visible in collection_ids(http)), "publisher stopped refreshing"
        assert hidden not in collection_ids(http)
        with db.cursor() as cur:
            cur.execute("UPDATE cgis_app.dataset_access SET access = 'users' WHERE dataset_id = %s", (hidden,))
        assert wait_for(lambda: hidden in collection_ids(http)), "relaxed dataset not published"
    finally:
        with db.cursor() as cur:
            cur.execute("DELETE FROM cgis_app.dataset_access WHERE dataset_id = %s", (hidden,))
