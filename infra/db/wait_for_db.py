"""Wait until PostgreSQL accepts connections (PG* environment), then exit 0; exit 1 after a timeout.

Run by the `migrate` service before migrations. `depends_on: service_healthy` covers a normal
start-up, but not every case: for example, `docker compose restart` restarts this one-shot
together with the database.
"""

from __future__ import annotations

import os
import sys
import time

import psycopg

TIMEOUT = float(os.environ.get("CGIS_DB_WAIT_SECONDS", "60"))

deadline = time.monotonic() + TIMEOUT
last_error = None
while time.monotonic() < deadline:
    try:
        with psycopg.connect("", connect_timeout=3, application_name="cgis_wait_for_db") as conn:
            conn.execute("SELECT 1")
        print("wait_for_db: database is accepting connections")
        sys.exit(0)
    except psycopg.Error as exc:
        last_error = exc.__class__.__name__
        time.sleep(1)
print(f"wait_for_db: database not reachable after {TIMEOUT:.0f}s ({last_error})", file=sys.stderr)
sys.exit(1)
