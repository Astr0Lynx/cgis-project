# Agent 1 handoff report: infrastructure

| | |
|---|---|
| Branch | `phase2/agent1-infrastructure` (pushed to `origin`; no PR, nothing merged) |
| Based on | `BASE_SHA` = `cc395c69ee738b4597684cf2ccfb84254c15723c` (`origin/phase2/base`) |
| Branch HEAD | The commit that adds this report. Verify with `git rev-parse origin/phase2/agent1-infrastructure`. |
| Who did the work | The **baseline** stage (`phase2/base`) was done by the original Agent 1 session. That session never pushed the **infrastructure** stage. At the operator's request, the Agent 6 session completed it on this branch, strictly within Agent 1's scope and package instructions (`CyberGIS_Phase2_Agent_01_Infrastructure.zip`), before starting integration. |

## Agent 1 checklist status

| Item | Status |
|---|---|
| Actual default branch identified (`main` @ `264bce0`), existing content preserved | done by the baseline session |
| `phase2/base` pushed, `BASE_SHA` given | done by the baseline session (`cc395c6`) |
| `phase2/agent1-infrastructure` created at the identical `BASE_SHA` | done (this branch) |
| Owned infrastructure committed and pushed | done |
| Compose configuration and gateway documented; paths match the frozen contract | done (`infra/README.md`) |
| Volumes, environment, readiness and restart tested where possible | done for db/gateway; full stack **BLOCKED UNTIL INTEGRATION** (see below) |
| No credentials or secrets in commits | done (`.env` is ignored; `.env.example` has `CHANGE_ME` only) |
| No merge into `phase2/base`, integration or the default branch | done |

## Files (all within Agent 1's ownership)

| File | Purpose |
|---|---|
| `compose.yaml` | Services `volume-init`, `db`, `migrate`, `ingestion` (profile `tools`), `tiles`, `ogc`, `processing`, `viewer`, `gateway`; volumes `pgdata`, `assets`, `processing-outputs` |
| `.env.example` | Non-secret defaults and `CHANGE_ME` placeholders; one login role per service |
| `Makefile` | `help env check-modules config build up down restart ps logs migrate fixtures import seed smoke reset` |
| `.gitattributes` | LF line endings on every checkout, so shell entrypoints keep working when Windows `core.autocrlf=true` |
| `infra/gateway/` | nginx gateway (`Dockerfile`, `nginx.conf` with JSON access logs, `default.conf` routes) |
| `infra/viewer/default.conf` | Static server config for `demo-client/` (Agent 6 owns the content) |
| `infra/migrate/` | Migration runner image (context: repo root, narrowed by `Dockerfile.dockerignore`) |
| `infra/db/provision_roles.py` | Creates or updates the service login roles as members of Agent 2's group roles |
| `infra/compose.db-port.yaml` | Opt-in loopback DB port for development |
| `infra/scripts/init-env.sh`, `infra/scripts/smoke.sh` | `.env` generator; gateway route smoke test |
| `infra/tests/` | Mock-upstream routing test (`compose.mock.yaml`, `echo_server.py`, `test_gateway_routes.sh`) and `test_db_persistence.sh` |
| `infra/README.md` | Requirements, services, routes, environment, commands, volumes, tests, troubleshooting |
| `README.md` | Quick start, replacing the "added by the infrastructure branch" placeholder |

## Integration requests honoured

Every request from the four module branches was read from GitHub and wired as asked:

- **Agent 3:** `ingestion/docs/compose-request.md`. Tool profile; the only read-write `assets` mount;
  `./data/fixtures` bind; migrations first; `seed` target.
- **Agent 4:** `ogc-services/docs/gateway-request.md`. Prefix stripping and `Host` forwarding;
  `TILES_ROOT_PATH=/tiles`; `OGC_PUBLIC_URL`; start order `db` → `tiles` → `ogc`;
  `OGC_REFRESH_INTERVAL=30`; read-only role (CCR-4.2).
- **Agent 5:** `spatial-processing/docs/compose-request.md`. `target: runtime`; read-only `assets`;
  writable `processing-outputs` owned by uid 10001; `ROOT_PATH=/api`; 1 MiB body limit; 150 s read
  timeout; role with `ops.jobs` write access.
- **Agent 2:** the migration command `python -m cgis_db migrate up`, and login users granted the
  NOLOGIN group roles from migration 0006.

## Design decisions

| Area | Decision |
|---|---|
| Ports | Only the gateway publishes a port, on `127.0.0.1:8080`. The DB is private unless you opt in with `infra/compose.db-port.yaml`. |
| Least privilege | `ingestion` runs as a `cgis_ingest` member. `ogc`/`tiles` run as a `cgis_reader` member, with `default_transaction_read_only=on`. `processing` runs as a `cgis_processing` member. Only `db` and `migrate` use the owner role. |
| Gateway methods | `/ogc/`, `/tiles/` and `/viewer/` allow GET/HEAD only. TiTiler's read-only `POST /statistics` is therefore not exposed publicly; Agent 4's tests do not use it. |
| Upstream paths | Taken from the raw `$request_uri`, so encoded characters reach the service unchanged. Dot-segment paths are refused with 400. |
| Volume ownership | `volume-init` fixes ownership before any service mounts a volume. Otherwise a read-only consumer that mounts a fresh volume first would leave it root-owned, and ingestion (uid 10001) could not write. |
| Image pins | All base images are pinned by digest: postgis 17-3.5, nginx 1.27-alpine (the same digests Agent 4 tested), busybox 1.37.0 and python 3.12.12-slim. Module images keep their owners' pins. |

## Commands run and results

Environment: Windows 11, Docker Desktop (Engine 29.1.3, Compose v5.0.0), Git Bash.

| Command | Exit | Result |
|---|---:|---|
| `sh infra/scripts/init-env.sh` | 0 | `.env` written. No `CHANGE_ME` left outside comments, and `git check-ignore .env` confirms it is ignored. |
| `docker compose config --quiet` | 0 | Resolves; `--profile tools` lists all 9 services. |
| `docker compose -p cgis-gateway-test -f compose.yaml -f infra/tests/compose.mock.yaml up -d --build --wait gateway` | 0 | db, viewer, mock tiles/ogc/processing and gateway all healthy; volume-init and mock migrate exited 0. |
| `sh infra/tests/test_gateway_routes.sh` | 0 | **19/19 checks passed**: prefix stripping, query and `%2F` preserved, Host forwarded, POST to `/api/` proxied, POST/DELETE to read-only prefixes refused (403), 1.1 MB body refused (413), relative redirects, dot-segments refused (400), JSON 404, no nginx version banner. |
| `sh infra/scripts/smoke.sh http://localhost:18080` | 0 | 8/8 routes (against the mocks) |
| `docker compose exec gateway nginx -t` | 0 | Syntax OK |
| `sh infra/tests/test_db_persistence.sh` | 0 | PostgreSQL 17.5 / PostGIS 3.5.2. A probe row survived `down` and `up` without `-v`. |

Two problems came up during testing and were fixed:

- nginx's prefix replacement decoded `%2F`. The gateway now forwards the raw URI.
- Test stacks reused the real project name, because `COMPOSE_PROJECT_NAME` in `.env` overrides a
  file's `name:`. The tests now always pass `-p`.

**BLOCKED UNTIL INTEGRATION.** The module code is not on this branch, so these could not run here:
`migrate` against Agent 2's migrations, `provision_roles.py` against the real group roles, the
module image builds, `seed`, and the real-service smoke test. Agent 6 runs them after merging.

## Known limitations

- nginx resolves upstream names at start-up. If one backend container is recreated, run
  `docker compose restart gateway`.
- The owner password is fixed when `pgdata` is first initialised. Service passwords are re-applied
  on every `migrate`.
- `make` is optional. On hosts without it, `infra/README.md` lists the equivalent raw commands.
- No TLS. The stack is local development only, bound to loopback by default.
