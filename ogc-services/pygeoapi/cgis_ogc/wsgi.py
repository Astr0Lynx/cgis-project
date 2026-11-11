# WSGI entry point (gunicorn cgis_ogc.wsgi:APP, see docker/entrypoint.sh): pygeoapi's Flask app,
# imported only after the conformance adapter has patched pygeoapi.

from cgis_ogc import conformance

conformance.apply_from_env()

from pygeoapi.flask_app import APP  # noqa: E402  (must import after patching)

__all__ = ["APP"]
