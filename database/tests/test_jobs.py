"""ops.jobs lifecycle and status/result/error consistency."""

import uuid

import psycopg
import pytest
from psycopg.types.json import Jsonb

from cgis_db import queries


def job(conn, jid):
    return queries.get_job(conn, jid)


def test_async_lifecycle_to_completed(conn):
    jid = queries.create_job(conn, "clip", {"dataset_id": "synthetic-worldpop-2001"})
    j = job(conn, jid)
    assert j["status"] == "queued" and j["started_at"] is None and j["finished_at"] is None
    queries.start_job(conn, jid)
    assert job(conn, jid)["started_at"] is not None
    queries.complete_job(conn, jid, result_path=f"exports/{jid}.tif")
    j = job(conn, jid)
    assert j["status"] == "completed" and j["result_path"].endswith(".tif") and j["error"] is None
    assert j["finished_at"] is not None


def test_sync_job_inline_result(conn):
    jid = queries.create_job(conn, "zonal-statistics", {"aoi": "town-a"})
    queries.complete_job(conn, jid, result={"sum": 100, "count_valid": 100})
    assert job(conn, jid)["result"] == {"sum": 100, "count_valid": 100}


def test_failure_records_error_and_clears_results(conn):
    jid = queries.create_job(conn, "clip")
    queries.start_job(conn, jid)
    queries.fail_job(conn, jid, "AOI does not overlap raster")
    j = job(conn, jid)
    assert (j["status"], j["error"], j["result_path"], j["result"]) == (
        "failed", "AOI does not overlap raster", None, None)


@pytest.mark.parametrize("action", [
    lambda c, j: queries.start_job(c, j),
    lambda c, j: queries.complete_job(c, j, result_path="x.tif"),
    lambda c, j: queries.fail_job(c, j, "late"),
    lambda c, j: c.execute("UPDATE ops.jobs SET result_path = 'other.tif' WHERE job_id = %s", (j,)),
])
def test_terminal_jobs_are_immutable(conn, action):
    jid = queries.create_job(conn, "clip")
    queries.complete_job(conn, jid, result_path="a.tif")
    with pytest.raises(psycopg.errors.IntegrityConstraintViolation, match="terminal"):
        action(conn, jid)


def test_running_cannot_go_back_to_queued(conn):
    jid = queries.create_job(conn, "clip")
    queries.start_job(conn, jid)
    with pytest.raises(psycopg.errors.IntegrityConstraintViolation, match="transition"):
        conn.execute("UPDATE ops.jobs SET status = 'queued' WHERE job_id = %s", (jid,))


@pytest.mark.parametrize("status, extra, error", [
    ("completed", {}, psycopg.errors.CheckViolation),                         # no result
    ("failed", {}, psycopg.errors.CheckViolation),                            # no error
    ("failed", {"error": "  "}, psycopg.errors.CheckViolation),               # blank error
    ("queued", {"error": "x"}, psycopg.errors.CheckViolation),                # error on non-failed
    ("running", {"result_path": "a.tif"}, psycopg.errors.CheckViolation),     # result before completion
    ("completed", {"result_path": "/abs/a.tif"}, psycopg.errors.CheckViolation),
    ("completed", {"result_path": "../a.tif"}, psycopg.errors.CheckViolation),
    ("done", {}, psycopg.errors.CheckViolation),
])
def test_status_semantics_enforced_on_insert(conn, status, extra, error):
    cols = {"kind": "clip", "status": status, **extra}
    with pytest.raises(error):
        conn.execute(
            "INSERT INTO ops.jobs (kind, status, error, result_path) VALUES (%s, %s, %s, %s)",
            (cols["kind"], cols["status"], cols.get("error"), cols.get("result_path")))


def test_direct_terminal_insert_sets_timestamps(conn):
    jid = conn.execute("INSERT INTO ops.jobs (kind, status, result) VALUES ('zonal', 'completed', %s)"
                       " RETURNING job_id", (Jsonb({"sum": 1}),)).fetchone()[0]
    assert job(conn, jid)["finished_at"] is not None


@pytest.mark.parametrize("kind", ["", "Clip Job", "x" * 80, "drop table;"])
def test_bad_kind_rejected(conn, kind):
    with pytest.raises(psycopg.errors.CheckViolation):
        queries.create_job(conn, kind)


def test_input_must_be_object_and_immutable(conn):
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute("INSERT INTO ops.jobs (kind, input) VALUES ('clip', '[1]')")
    conn.rollback()
    jid = queries.create_job(conn, "clip", {"a": 1})
    with pytest.raises(psycopg.errors.IntegrityConstraintViolation, match="immutable"):
        conn.execute("UPDATE ops.jobs SET input = '{\"a\": 2}' WHERE job_id = %s", (jid,))


def test_unknown_job(conn):
    with pytest.raises(psycopg.errors.NoDataFound):
        queries.start_job(conn, uuid.uuid4())
