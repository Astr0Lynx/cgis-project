# pygeoapi (OGC API - Features + Tiles) with catalog-driven publication.
FROM geopython/pygeoapi:0.24.0@sha256:a62895f2585ef1fcd1fc97ec35bf13177becc1334a622172a2c5c4d2145b8f0a

# The stock tiles HTML viewer only renders tile_type 'raster'; our provider uses
# the standard dataType 'map'. Derive the template at build time and fail loudly
# if upstream changes the line.
RUN set -eux; \
    mkdir -p /cgis/templates/collections/tiles /cgis/runtime; \
    src=/pygeoapi/pygeoapi/templates/collections/tiles/index.html; \
    dst=/cgis/templates/collections/tiles/index.html; \
    sed "s/{% if data\['tile_type'\] == 'raster' %}/{% if data['tile_type'] in ('raster', 'map') %}/" "$src" > "$dst"; \
    grep -q "in ('raster', 'map')" "$dst"; \
    useradd --system --uid 10002 --home-dir /cgis cgis; \
    chown -R cgis /cgis/runtime

COPY cgis_ogc /cgis/lib/cgis_ogc
COPY config /cgis/config
COPY docker/entrypoint.sh /cgis/entrypoint.sh

ENV PYTHONPATH=/cgis/lib \
    PYGEOAPI_CONFIG=/cgis/runtime/pygeoapi-config.yml \
    PYGEOAPI_OPENAPI=/cgis/runtime/pygeoapi-openapi.yml \
    CGIS_OGC_BASE_CONFIG=/cgis/config/pygeoapi.base.yml \
    CGIS_TILER_INTERNAL_URL=http://tiles:8000 \
    CONTAINER_PORT=5000

USER cgis
WORKDIR /cgis
EXPOSE 5000
HEALTHCHECK --interval=10s --timeout=5s --start-period=30s --retries=6 \
    CMD /venv/bin/python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:5000/conformance?f=json', timeout=4).status == 200 else 1)"

ENTRYPOINT ["/cgis/entrypoint.sh"]
CMD ["serve"]
