# Restricted TiTiler for catalog-registered COGs (vendor-specific tile routes).
FROM python@sha256:aa943ce411423c7582288f18707ff66eddcbaeb510502d747a72aeb34f30d159

# rasterio wheels bundle GDAL but need libexpat at runtime
RUN apt-get update \
    && apt-get install -y --no-install-recommends libexpat1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt requirements.lock ./
RUN pip install --no-cache-dir -r requirements.txt -c requirements.lock

COPY cgis_tiler ./cgis_tiler

# No network access is needed to read local COGs; keep GDAL from listing dirs
# or probing sidecar files beyond what the COG itself requires.
ENV CGIS_ASSET_ROOT=/data/assets \
    GDAL_DISABLE_READDIR_ON_OPEN=EMPTY_DIR \
    GDAL_HTTP_MAX_RETRY=0 \
    CPL_VSIL_CURL_ALLOWED_EXTENSIONS=.none \
    TILES_ROOT_PATH="" \
    WEB_CONCURRENCY=2 \
    PYTHONUNBUFFERED=1

RUN useradd --system --uid 10001 --no-create-home tiler
USER tiler

EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=5s --start-period=10s --retries=6 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/readyz', timeout=4).status == 200 else 1)"

CMD ["sh", "-c", "exec uvicorn cgis_tiler.main:app --host 0.0.0.0 --port 8000 --workers ${WEB_CONCURRENCY} --proxy-headers --forwarded-allow-ips='*' --root-path \"${TILES_ROOT_PATH}\""]
