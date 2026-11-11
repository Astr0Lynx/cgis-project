"""CyberGIS application API (Agent 5): authentication, admin dataset hosting, catalog discovery, exports.

Layout:

* ``config``, ``db``, ``jobs``, ``storage``, ``worker.*`` are importable WITHOUT FastAPI so the
  restricted ingest worker can run on the ingestion image's own pinned GDAL stack.
* ``app`` and the ``routes_*`` modules form the HTTP service.
"""

__version__ = "1.0.0"
SERVICE_NAME = "cgis-application-api"
