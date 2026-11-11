"""Test fixtures. Database tests run against a REAL PostGIS server with the real migrations:

* ``database/`` core migrations (Agent 4's frozen schema) + ``cgis_app`` migrations, applied by the owner;
* the API and the workers connect with their own least-privilege login roles, exactly as in Compose.

Run everything with ``application-api/scripts/run_tests.sh`` (starts a throwaway cluster). The ingest worker
runs in a SEPARATE interpreter with the ingestion image's pinned GDAL stack when ``APP_TEST_INGEST_PYTHON``
is set (as in production); otherwise in-process.
"""

from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
import uuid
from dataclasses import replace
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
APP_ROOT = HERE.parent
REPO = APP_ROOT.parent
sys.path.insert(0, str(APP_ROOT / "src"))
sys.path.insert(1, str(REPO / "database"))
sys.path.insert(2, str(REPO))  # the existing ingestion package
sys.path.insert(3, str(REPO / "spatial-processing" / "src"))
sys.path.insert(0, str(HERE))

from cgis_app.config import GIB, MIB, Settings  # noqa: E402

PASSWORD_ADMIN = "adm-" + secrets.token_urlsafe(18)
PASSWORD_USER = "usr-" + secrets.token_urlsafe(18)


def pytest_collection_modifyitems(config, items):
    if os.environ.get("APP_TEST_DSN"):
        return
    skip = pytest.mark.skip(reason="APP_TEST_DSN not set (run scripts/run_tests.sh for the PostGIS suite)")
    for item in items:
        if "db" in item.keywords:
            item.add_marker(skip)


# ------------------------------------------------------------------ database
class TestDatabase:
    def __init__(self, owner_dsn: str, api_dsn: str, worker_dsn: str, dbname: str):
        self.owner_dsn = owner_dsn
        self.api_dsn = api_dsn
        self.worker_dsn = worker_dsn
        self.dbname = dbname

    def owner(self):
        import psycopg
        from psycopg.rows import dict_row

        return psycopg.connect(self.owner_dsn, autocommit=True, row_factory=dict_row)

    def reset(self) -> None:
        with self.owner() as conn:
            conn.execute("TRUNCATE cgis_app.jobs, cgis_app.uploads, cgis_app.dataset_access, cgis_app.sessions, "
                         "cgis_app.login_attempts, cgis_app.users CASCADE")
            conn.execute("TRUNCATE geo.admin_units CASCADE")
            conn.execute("ALTER TABLE catalog.datasets DISABLE TRIGGER USER")
            conn.execute("DELETE FROM catalog.datasets")
            conn.execute("ALTER TABLE catalog.datasets ENABLE TRIGGER USER")


@pytest.fixture(scope="session")
def database():
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import conninfo_to_dict, make_conninfo

    base = os.environ.get("APP_TEST_DSN")
    if not base:
        pytest.skip("APP_TEST_DSN not set")
    dbname = f"cgis_app_t_{uuid.uuid4().hex[:10]}"
    with psycopg.connect(base, autocommit=True) as c:
        c.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(dbname)))
    owner_dsn = make_conninfo(base, dbname=dbname)
    from cgis_db import migrate as core_migrate

    from cgis_app import migrate as app_migrate

    suffix = uuid.uuid4().hex[:6]
    api_user, worker_user = f"cgis_app_api_t{suffix}", f"cgis_app_worker_t{suffix}"
    with psycopg.connect(owner_dsn, autocommit=True) as conn:
        core_migrate.up(conn, "core", None, log=lambda *_: None)
        app_migrate.apply_migrations(conn)
        app_migrate.provision_login_roles(conn, [(api_user, secrets.token_hex(16), "cgis_app_api"),
                                                 (worker_user, secrets.token_hex(16), "cgis_app_worker")])
    params = conninfo_to_dict(owner_dsn)
    params.pop("password", None)
    db = TestDatabase(owner_dsn, make_conninfo(**{**params, "user": api_user}),
                      make_conninfo(**{**params, "user": worker_user}), dbname)
    yield db
    with psycopg.connect(base, autocommit=True) as c:
        c.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(dbname)))
        for role in (api_user, worker_user):
            c.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(role)))


@pytest.fixture
def db(database):
    database.reset()
    return database


# ------------------------------------------------------------------ settings / app
@pytest.fixture
def roots(tmp_path):
    out = {name: tmp_path / name for name in ("staging", "assets", "exports")}
    for p in out.values():
        p.mkdir()
    return out


@pytest.fixture
def settings(db, roots):
    return Settings(
        database_url=db.api_dsn, db_pool_min=0, db_pool_max=4, public_base_url="http://testserver",
        allowed_origins=("http://testserver",), staging_root=roots["staging"], asset_root=roots["assets"],
        export_root=roots["exports"], max_upload_bytes=64 * MIB, max_vector_upload_bytes=8 * MIB,
        staging_max_total_bytes=1 * GIB, min_free_bytes=64 * MIB, login_max_failures=3, login_lockout_seconds=60,
        session_ttl_seconds=3600, session_idle_seconds=900, worker_poll_seconds=0.05, job_lease_seconds=30,
        export_clip_max_pixels=2_000_000, export_max_pixels=50_000_000)


@pytest.fixture
def worker_settings(settings, db):
    return replace(settings, database_url=db.worker_dsn)


@pytest.fixture
def app(settings):
    from cgis_app.app import create_app

    return create_app(settings, start_maintenance=False)


@pytest.fixture
def make_client(app):
    from fastapi.testclient import TestClient

    clients = []

    def factory():
        c = TestClient(app, base_url="http://testserver")
        c.__enter__()
        clients.append(c)
        return c

    yield factory
    for c in clients:
        c.__exit__(None, None, None)


@pytest.fixture
def users(db, settings):
    from cgis_app import identity
    from cgis_app.db import connect

    with connect(db.api_dsn, application_name="test") as conn:
        admin = identity.create_user(conn, settings, "alice-admin", PASSWORD_ADMIN, "admin")
        user = identity.create_user(conn, settings, "bob-user", PASSWORD_USER, "user")
    return {"admin": {"username": admin.username, "password": PASSWORD_ADMIN},
            "user": {"username": user.username, "password": PASSWORD_USER}}


def login(client, creds: dict) -> str:
    r = client.post("/auth/login", json={"username": creds["username"], "password": creds["password"]})
    assert r.status_code == 200, r.text
    return r.json()["csrf_token"]


@pytest.fixture
def admin_client(make_client, users):
    c = make_client()
    c.headers["X-CSRF-Token"] = login(c, users["admin"])
    return c


@pytest.fixture
def user_client(make_client, users):
    c = make_client()
    c.headers["X-CSRF-Token"] = login(c, users["user"])
    return c


@pytest.fixture
def anon_client(make_client):
    return make_client()


# ------------------------------------------------------------------ workers
def _ingest_python() -> str | None:
    return os.environ.get("APP_TEST_INGEST_PYTHON") or None


@pytest.fixture
def run_ingest(worker_settings, db, roots):
    """Drain the ingest queue. Out-of-process with the ingestion pins when APP_TEST_INGEST_PYTHON is set."""

    py = _ingest_python()
    if py is None:
        try:
            import rio_cogeo  # noqa: F401  (the importer needs it to verify COGs)
        except ImportError:
            pytest.skip("set APP_TEST_INGEST_PYTHON to an interpreter with ingestion/requirements.txt")

    def run():
        if py is None:
            from cgis_app.worker.runner import serve

            return serve(worker_settings, "ingest", dsn=db.worker_dsn, once=True)
        env = {"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", "/tmp"),
               "PYTHONPATH": os.pathsep.join([str(APP_ROOT / "src"), str(REPO)]),
               "APP_DATABASE_URL": db.worker_dsn, "APP_STAGING_ROOT": str(roots["staging"]),
               "APP_ASSET_ROOT": str(roots["assets"]), "APP_EXPORT_ROOT": str(roots["exports"]),
               "APP_MIN_FREE_BYTES": str(worker_settings.min_free_bytes), "APP_WORKER_POLL_SECONDS": "0.05",
               "APP_LOG_LEVEL": "WARNING", "APP_MAX_COMPRESSION_RATIO": str(worker_settings.max_compression_ratio),
               "APP_MAX_RASTER_PIXELS": str(worker_settings.max_raster_pixels)}
        proc = subprocess.run([py, "-P", "-m", "cgis_app", "worker", "--queue", "ingest", "--once"], env=env,
                              capture_output=True, text=True, timeout=600, cwd=str(roots["staging"].parent))
        if proc.returncode != 0:
            raise AssertionError(f"ingest worker failed:\n{proc.stdout}\n{proc.stderr}")
        return None

    return run


@pytest.fixture
def run_export(worker_settings, db):
    from cgis_app.worker.runner import serve

    def run():
        return serve(worker_settings, "export", dsn=db.worker_dsn, once=True)

    return run


# ------------------------------------------------------------------ data helpers
@pytest.fixture
def samples(tmp_path):
    import synthetic

    return synthetic.make_all(tmp_path / "samples")


def upload(client, path: Path, metadata: dict, *, filename: str | None = None, content_type: str = "application/octet-stream",
           url: str = "/admin/datasets/uploads"):
    with open(path, "rb") as fh:
        return client.post(url, files={"file": (filename or path.name, fh, content_type),
                                       "metadata": (None, json.dumps(metadata), "application/json")})


def wait_job(client, job_id: str, runner, *, prefix: str = "/admin/jobs") -> dict:
    runner()
    r = client.get(f"{prefix}/{job_id}")
    assert r.status_code == 200, r.text
    return r.json()
