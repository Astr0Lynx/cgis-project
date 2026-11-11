# Operator CLI against the real database: migrate (idempotent, role provisioning), bootstrap, users, fixtures.

from __future__ import annotations

import io
import json
import os
import stat

import pytest
from psycopg.conninfo import conninfo_to_dict

from cgis_app import cli

pytestmark = pytest.mark.db


# Environment with the owner's PG* settings for CLI subprocesses.
@pytest.fixture
def owner_env(db, monkeypatch):
    params = conninfo_to_dict(db.owner_dsn)
    for key, var in (("host", "PGHOST"), ("port", "PGPORT"), ("user", "PGUSER"), ("dbname", "PGDATABASE")):
        if key in params:
            monkeypatch.setenv(var, str(params[key]))
    return params


# Verifies: migrate is idempotent and provisions roles.
def test_migrate_is_idempotent_and_provisions_roles(owner_env, db, monkeypatch, capsys):
    monkeypatch.setenv("APP_DB_USER", "cgis_app_cli_api")
    monkeypatch.setenv("APP_DB_PASSWORD", "x9" * 12)
    monkeypatch.setenv("APP_WORKER_DB_USER", "cgis_app_cli_worker")
    monkeypatch.setenv("APP_WORKER_DB_PASSWORD", "y8" * 12)
    assert cli.main(["migrate"]) == 0
    out = capsys.readouterr().out
    assert "applied" not in out and "cgis_app_cli_api (member of cgis_app_api)" in out  # schema already current
    assert cli.main(["migrate", "--status"]) == 0
    assert "applied" in capsys.readouterr().out
    with db.owner() as conn:
        roles = conn.execute("SELECT r.rolname, g.rolname AS grp FROM pg_auth_members m JOIN pg_roles r ON "
                             "r.oid = m.member JOIN pg_roles g ON g.oid = m.roleid WHERE r.rolname LIKE "
                             "'cgis_app_cli_%' ORDER BY 1, 2").fetchall()
        assert {(r["rolname"], r["grp"]) for r in roles} == {("cgis_app_cli_api", "cgis_app_api"),
                                                            ("cgis_app_cli_worker", "cgis_app_worker")}
        for role in ("cgis_app_cli_api", "cgis_app_cli_worker"):
            conn.execute(f"DROP OWNED BY {role}")  # removes the CONNECT grant
            conn.execute(f"DROP ROLE {role}")
    monkeypatch.setenv("APP_DB_PASSWORD", "CHANGE_ME_PLEASE_123")
    assert cli.main(["migrate"]) == 1


# Verifies: bootstrap users and fixtures.
def test_bootstrap_users_and_fixtures(db, monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("APP_DATABASE_URL", db.api_dsn)
    monkeypatch.setenv("APP_BOOTSTRAP_ADMIN_USERNAME", "boot-admin")
    pw_file = tmp_path / "pw"
    pw_file.write_text("Bootstrap-Admin-Pass-1\n")
    monkeypatch.setenv("APP_BOOTSTRAP_ADMIN_PASSWORD_FILE", str(pw_file))
    assert cli.main(["bootstrap"]) == 0
    assert "admin account boot-admin: created" in capsys.readouterr().out
    assert cli.main(["bootstrap"]) == 0
    assert "unchanged" in capsys.readouterr().out
    monkeypatch.setenv("APP_BOOTSTRAP_USER_USERNAME", "boot-admin")  # same name, other role: refused
    monkeypatch.setenv("APP_BOOTSTRAP_USER_PASSWORD", "Another-Strong-Pass-2")
    assert cli.main(["bootstrap"]) == 1
    monkeypatch.delenv("APP_BOOTSTRAP_USER_USERNAME")
    monkeypatch.delenv("APP_BOOTSTRAP_USER_PASSWORD")

    monkeypatch.setattr("sys.stdin", io.StringIO("Strong-Pass-for-C-3\n"))
    assert cli.main(["users", "add", "carol", "--role", "user", "--password-stdin"]) == 0
    monkeypatch.setattr("sys.stdin", io.StringIO("short\n"))
    assert cli.main(["users", "add", "dave", "--role", "user", "--password-stdin"]) == 1
    assert cli.main(["users", "set-role", "carol", "admin"]) == 0
    assert cli.main(["users", "disable", "carol"]) == 0
    assert cli.main(["users", "list"]) == 0
    listing = capsys.readouterr().out
    assert "carol" in listing and "disabled" in listing and "Strong-Pass-for-C" not in listing

    out = tmp_path / "creds" / "e2e.json"
    assert cli.main(["fixtures", "--out", str(out)]) == 0
    creds = json.loads(out.read_text())
    assert creds["admin"]["username"] == "e2e-admin" and creds["user"]["username"] == "e2e-user"
    assert stat.S_IMODE(os.stat(out).st_mode) == 0o600
    assert creds["admin"]["password"] not in capsys.readouterr().out
    assert cli.main(["fixtures", "--out", str(out)]) == 0  # re-keys existing fixtures
    assert json.loads(out.read_text())["admin"]["password"] != creds["admin"]["password"]
