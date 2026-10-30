# Compose wiring request — Agent 3 → Agent 1 / Agent 6

Agent 3 does not edit the root `compose.yaml`, `.env.example` or `Makefile`. Please wire the service
roughly as below. Names follow `contracts/ARCHITECTURE.md`: service `ingestion`, database `db:5432`,
assets at `/data/assets`, fixtures at `/data/fixtures`.

```yaml
services:
  ingestion:
    build: ./ingestion                 # ingestion/Dockerfile, build context = ingestion/
    image: cgis-ingestion:local
    environment:
      PGHOST: db
      PGPORT: "5432"
      PGDATABASE: ${POSTGRES_DB}
      PGUSER: ${POSTGRES_USER}
      PGPASSWORD: ${POSTGRES_PASSWORD}  # from .env, never committed
      INGESTION_ASSETS_DIR: /data/assets
      INGESTION_FIXTURES_DIR: /data/fixtures
      INGESTION_DB_WAIT_SECONDS: "60"
    volumes:
      - assets:/data/assets            # read-write ONLY here
      - ./data/fixtures:/data/fixtures # generated, git-ignored (/data/ in .gitignore)
    depends_on:
      db:
        condition: service_healthy     # and after migrations have completed
    profiles: ["tools"]                # one-shot: `docker compose run --rm ingestion seed`
    # command defaults to --help; the entrypoint is `python -m ingestion.cli`

  tiles:       # and processing
    volumes:
      - assets:/data/assets:ro         # consumers mount read-only

volumes:
  assets:
```

Alternatively, the variables `INGESTION_DATABASE_URL` / `DATABASE_URL`
(`postgresql://user:pass@db:5432/dbname`) are honoured.

## Seed / demo commands (for the Makefile and docs)

```bash
docker compose run --rm ingestion seed              # generate SYNTHETIC fixtures + import (idempotent)
docker compose run --rm ingestion generate-fixtures # fixtures only
docker compose run --rm ingestion validate --manifest /data/fixtures/manifest.json
docker compose run --rm ingestion import   --manifest /data/fixtures/manifest.json
docker compose run --rm --entrypoint python \
  -e INGESTION_TEST_DSN="host=db dbname=<disposable>_test user=... password=..." ingestion -m pytest ingestion/tests
```

Running `seed` twice is safe: the second run reports `"outcome": "unchanged"` for all three datasets.
Exit code is non-zero if any dataset fails, so a `make seed` target can gate on it.

## Notes for integration

- **Migrations first.** `import` checks that the `catalog`/`geo` columns exist. If the DB is
  unreachable (`db_unavailable`) or unmigrated (`schema_incompatible`), it exits with code 3.
- **Volume ownership.** The image runs as uid `10001` (`ingest`). A named `assets` volume picks up the
  image's `/data/assets` ownership on first use. For the `./data/fixtures` bind mount, either create the
  host directory writable by that uid or add `user: "${UID}:${GID}"` to the service.
- **SELinux hosts (Fedora/RHEL + podman).** Bind mounts need `:z` (e.g. `./data/fixtures:/data/fixtures:z`).
  This was verified on the Agent 3 machine. Without it, writes fail with `fixture_write_failed`.
- **Consumers.** Resolve catalog `asset_path` against `/data/assets`, use only `status='ready'` rows
  (`catalog.current_datasets`), ignore `/data/assets/.staging/`, and never accept client-supplied paths.
- **Image size.** It is based on `python:3.12.7-slim-bookworm`, plus `libexpat1`, which the rasterio
  wheel's bundled GDAL needs.
