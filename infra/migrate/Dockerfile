# One-shot migration runner: Agent 2's cgis_db migrations, then service login roles.
# Build context: repository root (only database/ and infra/db/ are sent; see Dockerfile.dockerignore).
FROM python:3.12.12-slim@sha256:f3fa41d74a768c2fce8016b98c191ae8c1bacd8f1152870a3f9f87d350920b7c

# cgis_db finds its SQL relative to the package source; once pip-installed into site-packages it
# needs the explicit directory (database/README.md: CGIS_DB_MIGRATIONS_DIR).
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    CGIS_DB_MIGRATIONS_DIR=/opt/cgis/database/migrations

WORKDIR /opt/cgis
COPY database/ ./database/
COPY infra/db/ ./infra-db/
RUN pip install ./database \
    && useradd --system --uid 10001 --home-dir /opt/cgis migrate
USER migrate
# Migrations are applied by the owner role from the environment (PG* variables).
CMD ["sh", "-c", "python /opt/cgis/infra-db/wait_for_db.py && python -m cgis_db migrate up && python /opt/cgis/infra-db/provision_roles.py"]
