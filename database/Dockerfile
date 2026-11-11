# One-shot `migrate` service image: `python -m cgis_db.setup` waits for the database, applies the
# migrations and creates the service login roles. Build context: database/.
FROM python:3.12.12-slim@sha256:f3fa41d74a768c2fce8016b98c191ae8c1bacd8f1152870a3f9f87d350920b7c

# Once pip-installed, cgis_db needs the explicit path to its SQL files.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    CGIS_DB_MIGRATIONS_DIR=/opt/cgis/database/migrations

WORKDIR /opt/cgis
COPY . ./database/
RUN pip install ./database \
    && useradd --system --uid 10001 --home-dir /opt/cgis migrate
USER migrate
# Runs as the owner role from the PG* environment variables.
CMD ["python", "-m", "cgis_db.setup"]
