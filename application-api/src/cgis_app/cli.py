"""Command line: ``python -m cgis_app <command>`` (also installed as ``cgis-app``).

  serve      [--host 0.0.0.0] [--port 8000] [--workers 2]     HTTP API (uvicorn, path-only access log)
  migrate    [--status] [--no-roles]                         schema + login roles (owner PG* credentials)
  bootstrap                                                  admin/user accounts from APP_BOOTSTRAP_* env
  users      list | add NAME --role R | passwd NAME | set-role NAME R | disable NAME | enable NAME
  fixtures   --out FILE [--prefix e2e]                       random-password admin+user for test suites
  worker     --queue ingest|export [--once]                  background worker
  worker-health [--max-age 120]                              worker container healthcheck
  openapi    [--out FILE]                                    write the OpenAPI 3 document

Passwords are read from stdin (``--password-stdin``) or an interactive prompt, never from argv, so they do
not appear in shell history or ``ps``. Exit codes: 0 ok, 1 failure, 2 usage/config error.
"""

from __future__ import annotations

import argparse
import getpass
import json
import logging
import os
import secrets
import sys
from pathlib import Path

from .config import ConfigError, Settings, read_secret

log = logging.getLogger("cgis_app.cli")


def _settings() -> Settings:
    try:
        return Settings.from_env()
    except ConfigError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        sys.exit(2)


def _read_password(args, prompt: str = "Password: ") -> str:
    if getattr(args, "password_stdin", False):
        value = sys.stdin.readline().rstrip("\n")
    else:
        value = getpass.getpass(prompt)
        if getpass.getpass("Repeat: ") != value:
            print("passwords do not match", file=sys.stderr)
            sys.exit(2)
    if not value:
        print("empty password", file=sys.stderr)
        sys.exit(2)
    return value


def _conn(settings: Settings):
    from .db import connect

    return connect(settings.database_url, application_name="cgis-app-cli", wait_seconds=30)


def cmd_serve(args) -> int:
    import uvicorn

    settings = _settings()
    uvicorn.run("cgis_app.app:app_factory", factory=True, host=args.host, port=args.port, workers=args.workers,
                access_log=False, proxy_headers=True, forwarded_allow_ips=os.environ.get("FORWARDED_ALLOW_IPS", "*"),
                log_level=settings.log_level.lower(), timeout_keep_alive=30,
                h11_max_incomplete_event_size=64 * 1024)
    return 0


def cmd_migrate(args) -> int:
    from . import migrate

    try:
        if args.status:
            import psycopg

            with psycopg.connect("", autocommit=True) as conn:
                rows = migrate.migration_status(conn)
            for r in rows:
                print(f"{r['version']:04d} {r['name']:<28} {r['state']}")
            return 1 if any(r["state"] == "DRIFT" for r in rows) else 0
        result = migrate.run("", provision=not args.no_roles)
    except migrate.MigrationError as exc:
        print(f"cgis_app migrate: ERROR: {exc}", file=sys.stderr)
        return 1
    for name in result["applied"]:
        print(f"applied {name}")
    for line in result["roles"]:
        print(f"role: {line}")
    print("cgis_app migrate: ok")
    return 0


def cmd_bootstrap(args) -> int:
    from . import identity

    settings = _settings()
    reset = os.environ.get("APP_BOOTSTRAP_RESET_PASSWORDS", "false").lower() in ("1", "true", "yes")
    pairs = [("ADMIN", "admin"), ("USER", "user")]
    todo = []
    for prefix, role in pairs:
        name = os.environ.get(f"APP_BOOTSTRAP_{prefix}_USERNAME", "").strip()
        password = read_secret(f"APP_BOOTSTRAP_{prefix}_PASSWORD")
        if not name and not password:
            continue
        if not name or not password:
            print(f"APP_BOOTSTRAP_{prefix}_USERNAME and APP_BOOTSTRAP_{prefix}_PASSWORD must be set together",
                  file=sys.stderr)
            return 2
        todo.append((name, password, role))
    if not todo:
        print("bootstrap: no APP_BOOTSTRAP_* accounts configured; nothing to do")
        return 0
    with _conn(settings) as conn:
        for name, password, role in todo:
            try:
                outcome = identity.ensure_user(conn, settings, name, password, role, reset_password=reset)
            except identity.IdentityError as exc:
                print(f"bootstrap: {role} account {name!r}: {exc.message}", file=sys.stderr)
                return 1
            print(f"bootstrap: {role} account {name}: {outcome}")
    return 0


def cmd_users(args) -> int:
    from . import identity

    settings = _settings()
    with _conn(settings) as conn:
        try:
            if args.action == "list":
                for u in identity.list_users(conn):
                    print(f"{u['username']:<32} {u['role']:<6} {'disabled' if u['disabled'] else 'active':<9} "
                          f"created {u['created_at']:%Y-%m-%d}")
                return 0
            if args.action == "add":
                identity.create_user(conn, settings, args.name, _read_password(args), args.role)
                print(f"created {args.name} ({args.role})")
                return 0
            user = identity.get_user(conn, args.name)
            if user is None:
                print(f"no such user {args.name!r}", file=sys.stderr)
                return 1
            if args.action == "passwd":
                identity.set_password(conn, settings, user, _read_password(args, "New password: "))
            elif args.action == "set-role":
                identity.set_role(conn, user, args.role)
            elif args.action == "disable":
                identity.set_disabled(conn, user, True)
            elif args.action == "enable":
                identity.set_disabled(conn, user, False)
            print(f"{args.action}: {user.username} ok (sessions revoked where applicable)")
            return 0
        except identity.IdentityError as exc:
            print(f"{exc.code}: {exc.message}", file=sys.stderr)
            return 1


def cmd_fixtures(args) -> int:
    """Create (or re-key) an admin and a user with random passwords for automated tests.

    The credentials are written to ``--out`` with mode 0600 and never printed; keep that file out of Git
    (the default location under ./data/ is git-ignored).
    """
    from . import identity

    settings = _settings()
    out = Path(args.out)
    creds = {}
    with _conn(settings) as conn:
        for role in ("admin", "user"):
            name = f"{args.prefix}-{role}"
            password = secrets.token_urlsafe(24)
            user = identity.get_user(conn, name)
            if user is None:
                identity.create_user(conn, settings, name, password, role)
            else:
                if user.role != role:
                    identity.set_role(conn, user, role)
                identity.set_password(conn, settings, user, password)
                identity.set_disabled(conn, user, False)
            creds[role] = {"username": name, "password": password}
    out.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(creds, fh, indent=2)
    print(f"fixtures: {args.prefix}-admin and {args.prefix}-user ready; credentials written to {out} (mode 0600)")
    return 0


def cmd_worker(args) -> int:
    from .worker.runner import serve

    settings = _settings()
    serve(settings, args.queue, once=args.once)
    return 0


def cmd_worker_health(args) -> int:
    """Container healthcheck for workers: 0 when the loop or a job heartbeat ran recently."""
    from .worker.runner import health_ok

    return 0 if health_ok(args.max_age) else 1


def cmd_openapi(args) -> int:
    from .app import create_app

    app = create_app(Settings(), start_maintenance=False)
    text = json.dumps(app.openapi(), indent=2, sort_keys=False, default=str) + "\n"
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"wrote {args.out}")
    else:
        sys.stdout.write(text)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="cgis-app", description="CyberGIS application API")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("serve", help="run the HTTP API")
    s.add_argument("--host", default="0.0.0.0")
    s.add_argument("--port", type=int, default=8000)
    s.add_argument("--workers", type=int, default=int(os.environ.get("APP_HTTP_WORKERS", "2")))
    s.set_defaults(func=cmd_serve)

    m = sub.add_parser("migrate", help="apply the cgis_app schema and provision login roles")
    m.add_argument("--status", action="store_true")
    m.add_argument("--no-roles", action="store_true", help="skip APP_DB_USER/APP_WORKER_DB_USER provisioning")
    m.set_defaults(func=cmd_migrate)

    b = sub.add_parser("bootstrap", help="create the APP_BOOTSTRAP_* accounts if absent")
    b.set_defaults(func=cmd_bootstrap)

    u = sub.add_parser("users", help="manage accounts")
    usub = u.add_subparsers(dest="action", required=True)
    usub.add_parser("list")
    for action in ("add", "passwd", "set-role", "disable", "enable"):
        a = usub.add_parser(action)
        a.add_argument("name")
        if action in ("add", "set-role"):
            a.add_argument("--role", required=True, choices=["admin", "user"]) if action == "add" else \
                a.add_argument("role", choices=["admin", "user"])
        if action in ("add", "passwd"):
            a.add_argument("--password-stdin", action="store_true", help="read the password from the first stdin line")
    u.set_defaults(func=cmd_users)

    f = sub.add_parser("fixtures", help="random-password admin+user accounts for automated tests")
    f.add_argument("--out", required=True)
    f.add_argument("--prefix", default="e2e")
    f.set_defaults(func=cmd_fixtures)

    w = sub.add_parser("worker", help="run a background worker")
    w.add_argument("--queue", required=True, choices=["ingest", "export"])
    w.add_argument("--once", action="store_true", help="exit when the queue is empty")
    w.set_defaults(func=cmd_worker)

    h = sub.add_parser("worker-health", help="healthcheck for a worker container (exit 0 = alive)")
    h.add_argument("--max-age", type=float, default=120.0, help="seconds since the last loop/heartbeat")
    h.set_defaults(func=cmd_worker_health)

    o = sub.add_parser("openapi", help="write the OpenAPI document")
    o.add_argument("--out")
    o.set_defaults(func=cmd_openapi)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    level = os.environ.get("APP_LOG_LEVEL", "INFO").upper()
    logging.basicConfig(level=level, stream=sys.stderr, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
