"""WSGI entry point: pygeoapi's Flask app with the conformance adapter applied."""

from cgis_ogc import conformance

conformance.apply_from_env()

from pygeoapi.flask_app import APP  # noqa: E402  (must import after patching)

__all__ = ["APP"]
