"""Check that ingestion manifest entries can be uploaded through POST /admin/datasets/uploads unchanged.

The upload metadata is the manifest entry WITHOUT ``file`` (the bytes are the multipart file part). This script
validates every entry of the given manifests against the API's ``UploadMetadata`` model and reports
incompatibilities (e.g. uppercase ids, unknown keys). It reads JSON only; it opens no data file.

    python application-api/scripts/check_manifest_compat.py path/to/manifest.json [...]
    git show origin/completion/agent2-level1:science/level1/eval/india-full/manifest.json > /tmp/m.json && ...
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from pydantic import ValidationError  # noqa: E402

from cgis_app.models import UploadMetadata  # noqa: E402


def main(paths: list[str]) -> int:
    failures = 0
    for p in paths:
        doc = json.loads(Path(p).read_text(encoding="utf-8"))
        entries = doc if isinstance(doc, list) else doc.get("datasets", [doc])
        for e in entries:
            meta = {k: v for k, v in e.items() if k != "file"}
            try:
                UploadMetadata.model_validate(meta)
                status = "ok"
            except ValidationError as exc:
                failures += 1
                status = "INCOMPATIBLE: " + "; ".join(f"{'.'.join(map(str, er['loc']))}: {er['msg']}"
                                                      for er in exc.errors()[:5])
            print(f"{Path(p).name}\t{e.get('dataset_id')}@v{e.get('version')}\t{e.get('kind')}\t{status}")
    print(f"{failures} incompatible entr{'y' if failures == 1 else 'ies'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
