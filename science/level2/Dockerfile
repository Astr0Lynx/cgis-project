FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b
WORKDIR /repo
ENV PYTHONUNBUFFERED=1 MPLBACKEND=Agg PYTHONDONTWRITEBYTECODE=1
ARG MODEL_COMMIT
ENV CGIS_LEVEL2_BUILD_COMMIT=${MODEL_COMMIT}
COPY ingestion/requirements.txt ingestion/requirements.txt
COPY science/level2/requirements.txt science/level2/requirements.txt
RUN pip install --no-cache-dir -r science/level2/requirements.txt
COPY ingestion ingestion
COPY science/level2 science/level2
ENTRYPOINT ["python", "-m", "science.level2.cgis_level2"]
