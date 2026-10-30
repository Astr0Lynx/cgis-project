"""Generate pygeoapi's OpenAPI document with the conformance adapter applied."""

from __future__ import annotations

import os
from pathlib import Path

from cgis_ogc import conformance


def write_openapi(config_path: Path, openapi_path: Path) -> None:
    from pygeoapi.openapi import generate_openapi_document
    from pygeoapi.util import yaml_load

    with config_path.open(encoding="utf-8") as fh:
        conformance.apply(yaml_load(fh))
    content = generate_openapi_document(config_path, "yaml", fail_on_invalid_collection=True)
    tmp = openapi_path.with_suffix(openapi_path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, openapi_path)
