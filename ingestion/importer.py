"""Idempotent, atomic dataset import.

Per dataset (under a PostgreSQL advisory lock on ``dataset_id``/``version``):

1. checksum the source; an existing ``ready`` row with the same checksum is a no-op, a different
   checksum is a conflict (nothing is written);
2. validate the source completely (read-only);
3. upsert the catalog row as ``staged`` with full metadata;
4. build the managed asset in ``<assets>/.staging/<tmp>/``, verify it, fsync it;
5. place it at its final path with a no-clobber hard link (an existing different file is never
   overwritten);
6. in ONE transaction: upsert vector units (if any) and flip the row to ``ready``.

Any failure after step 3 removes the temp file and any final file this attempt created, and marks
the row ``failed`` with the error in ``provenance.ingestion.error``. ``ready`` is only ever set in
step 6, so a reader of ``catalog.current_datasets`` never sees a half-imported dataset.
"""

from __future__ import annotations

import errno
import logging
import os
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import rasterio

from . import IMPORTER_NAME, __version__
from .catalog import Catalog
from .errors import AssetError, ConflictError, IngestionError
from .manifest import DatasetSpec
from .paths import STAGING_DIRNAME, asset_relpath, fsync_dir, resolve_under, sha256_file
from .raster import RasterInfo, inspect_raster, verify_cog, write_cog
from .vector import VectorInfo, parse_geojson_bytes, read_vector

log = logging.getLogger(__name__)

_NO_HARDLINK_ERRNOS = {errno.EPERM, errno.EXDEV, errno.EMLINK, errno.ENOTSUP, errno.EOPNOTSUPP}


@dataclass
class ImportResult:
    dataset_id: str
    version: int
    kind: str
    outcome: str  # imported | unchanged | validated | failed
    asset_path: str | None = None
    checksum_sha256: str | None = None
    error: dict[str, Any] | None = None
    summary: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.outcome != "failed"

    def to_dict(self) -> dict[str, Any]:
        d = {"dataset_id": self.dataset_id, "version": self.version, "kind": self.kind, "outcome": self.outcome,
             "asset_path": self.asset_path, "checksum_sha256": self.checksum_sha256}
        if self.summary:
            d["summary"] = self.summary
        if self.error:
            d["error"] = self.error
        return d


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def validate_source(spec: DatasetSpec) -> RasterInfo | VectorInfo:
    return inspect_raster(spec) if spec.kind == "raster" else read_vector(spec)


def build_metadata(spec: DatasetSpec, info: RasterInfo | VectorInfo, checksum: str) -> dict[str, Any]:
    """Catalog row values derived from the file (never trusted from the manifest)."""
    rel = str(asset_relpath(spec.kind, spec.dataset_id, spec.version))
    provenance: dict[str, Any] = dict(spec.provenance)
    if spec.is_synthetic:
        provenance.setdefault("synthetic", True)
    if spec.extra:
        provenance["manifest_extra"] = spec.extra
    provenance["ingestion"] = {
        "importer": IMPORTER_NAME, "importer_version": __version__, "gdal_version": rasterio.__gdal_version__,
        "source_file": spec.file.name, "source_sha256": checksum, "started_at": _now(),
    }
    meta: dict[str, Any] = {"asset_path": rel, "checksum_sha256": checksum, "bbox_wgs84": info.bbox_wgs84}
    if isinstance(info, RasterInfo):
        provenance["raster"] = info.to_provenance()
        meta.update(crs=info.crs, resolution_x=info.resolution_x, resolution_y=info.resolution_y,
                    nodata=None if info.nodata is None else float(info.nodata))
    else:
        provenance["vector"] = info.to_provenance(spec)
        meta.update(crs="EPSG:4326", resolution_x=None, resolution_y=None, nodata=None)
    meta["provenance"] = provenance
    return meta


def summarize(info: RasterInfo | VectorInfo) -> dict[str, Any]:
    if isinstance(info, RasterInfo):
        return {"crs": info.crs, "width": info.width, "height": info.height, "dtype": info.dtype,
                "nodata": info.nodata, "bbox_wgs84": info.bbox_wgs84, "valid_count": info.stats["valid_count"],
                "sum": info.stats["sum"]}
    return {"crs": "EPSG:4326", "source_crs": info.source_crs, "feature_count": len(info.features),
            "bbox_wgs84": info.bbox_wgs84}


class Importer:
    def __init__(self, catalog: Catalog | None, assets_dir: Path):
        self.catalog = catalog
        self.assets_dir = assets_dir

    # Public API ----------------------------------------------------------------------------------
    def validate(self, spec: DatasetSpec) -> ImportResult:
        """Validate only: no database, no asset writes."""
        checksum = sha256_file(spec.file)
        self._check_expected_checksum(spec, checksum)
        info = validate_source(spec)
        return ImportResult(spec.dataset_id, spec.version, spec.kind, "validated",
                            asset_path=str(asset_relpath(spec.kind, spec.dataset_id, spec.version)),
                            checksum_sha256=checksum, summary=summarize(info))

    def import_dataset(self, spec: DatasetSpec) -> ImportResult:
        if self.catalog is None:
            raise IngestionError("import requires a database connection", code="db_unavailable")
        checksum = sha256_file(spec.file)
        with self.catalog.lock(spec):
            existing = self.catalog.get(spec)
            if existing is not None and existing.status == "ready":
                return self._already_ready(spec, existing, checksum)
            meta = None
            try:
                self._check_expected_checksum(spec, checksum)
                info = validate_source(spec)
                meta = build_metadata(spec, info, checksum)
                self.catalog.stage(spec, meta)
                return self._materialize_and_publish(spec, info, meta)
            except IngestionError as exc:
                self._record_failure(spec, meta, exc)
                raise
            except Exception as exc:  # unexpected: still never leave a staged/ready half-import behind
                self._record_failure(spec, meta, IngestionError(str(exc), code="internal_error"))
                raise

    # Internals -----------------------------------------------------------------------------------
    @staticmethod
    def _check_expected_checksum(spec: DatasetSpec, checksum: str) -> None:
        if spec.checksum_sha256 and spec.checksum_sha256 != checksum:
            raise AssetError(f"{spec.file.name} sha256 {checksum} does not match manifest {spec.checksum_sha256}",
                             code="checksum_mismatch")

    def _already_ready(self, spec: DatasetSpec, existing, checksum: str) -> ImportResult:
        if existing.checksum_sha256 != checksum:
            raise ConflictError(
                f"{spec.key} is already ready with checksum {existing.checksum_sha256[:12]}…; the new file "
                f"({checksum[:12]}…) differs. Publish it as a new version instead.",
                code="checksum_conflict",
                details={"existing_checksum": existing.checksum_sha256, "new_checksum": checksum})
        if existing.asset_path:
            final = resolve_under(self.assets_dir, existing.asset_path)
            if not final.is_file():
                raise AssetError(f"{spec.key} is ready but its asset {existing.asset_path} is missing from "
                                 f"{self.assets_dir}; is the assets volume mounted?", code="asset_missing")
            expected = (existing.provenance.get("ingestion") or {}).get("asset_sha256")
            if expected and sha256_file(final) != expected:
                raise AssetError(f"{spec.key} asset {existing.asset_path} no longer matches its recorded checksum",
                                 code="asset_corrupted")
        log.info("%s already ready with identical checksum; nothing to do", spec.key)
        return ImportResult(spec.dataset_id, spec.version, spec.kind, "unchanged", asset_path=existing.asset_path,
                            checksum_sha256=checksum)

    def _materialize_and_publish(self, spec: DatasetSpec, info, meta: dict[str, Any]) -> ImportResult:
        rel = meta["asset_path"]
        final = resolve_under(self.assets_dir, rel)
        staging_root = self.assets_dir / STAGING_DIRNAME
        staging_root.mkdir(parents=True, exist_ok=True)
        tmp_dir = Path(tempfile.mkdtemp(prefix=f"{spec.dataset_id}_v{spec.version}_", dir=staging_root))
        created_final = False
        try:
            tmp_asset = tmp_dir / final.name
            asset_details = self._build_asset(spec, info, tmp_asset)
            _fsync_file(tmp_asset)
            asset_sha = sha256_file(tmp_asset)

            final.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.link(tmp_asset, final)  # atomic and never overwrites
                created_final = True
            except FileExistsError:
                self._adopt_existing(spec, info, final, asset_sha)
            except OSError as exc:
                if exc.errno not in _NO_HARDLINK_ERRNOS:
                    raise
                # Filesystem without hard links: rename is still atomic, and the advisory lock
                # guarantees no other importer targets this path between the check and the rename.
                if final.exists():
                    self._adopt_existing(spec, info, final, asset_sha)
                else:
                    os.rename(tmp_asset, final)
                    created_final = True
            if created_final:
                fsync_dir(final.parent)

            provenance = meta["provenance"]
            provenance["ingestion"].update(asset_sha256=sha256_file(final), asset_path=rel,
                                           completed_at=_now(), **asset_details)
            units = info.features if isinstance(info, VectorInfo) else None
            self.catalog.publish(spec, meta["checksum_sha256"], rel, provenance, units)
        except BaseException:
            if created_final:
                final.unlink(missing_ok=True)
            raise
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        log.info("%s imported -> %s", spec.key, rel)
        return ImportResult(spec.dataset_id, spec.version, spec.kind, "imported", asset_path=rel,
                            checksum_sha256=meta["checksum_sha256"], summary=summarize(info))

    def _build_asset(self, spec: DatasetSpec, info, tmp_asset: Path) -> dict[str, Any]:
        if isinstance(info, RasterInfo):
            tags = {"CGIS_DATASET_ID": spec.dataset_id, "CGIS_DATASET_VERSION": str(spec.version),
                    "CGIS_UNITS": spec.units or ""}
            if spec.is_synthetic:
                tags["SYNTHETIC"] = "TRUE"
            write_cog(spec, info, tmp_asset, tags)
            return {"asset": verify_cog(spec, info, tmp_asset)}
        data = info.to_geojson_bytes(spec)
        tmp_asset.write_bytes(data)
        back = parse_geojson_bytes(tmp_asset.read_bytes())
        if [f["id"] for f in back] != [u.unit_id for u in info.features]:
            raise AssetError(f"normalized GeoJSON for {spec.key} failed read-back", code="asset_verification_failed")
        return {"asset": {"format": "GeoJSON", "crs": "EPSG:4326", "geometry_type": "MultiPolygon"}}

    def _adopt_existing(self, spec: DatasetSpec, info, final: Path, new_sha: str) -> None:
        """A file already sits at the final path (e.g. crash after link, before commit)."""
        if sha256_file(final) == new_sha:
            log.warning("%s: reusing identical asset left by an earlier interrupted import", spec.key)
            return
        if isinstance(info, RasterInfo):
            try:
                verify_cog(spec, info, final)
                log.warning("%s: reusing equivalent COG left by an earlier interrupted import", spec.key)
                return
            except IngestionError:
                pass
        raise AssetError(f"refusing to overwrite existing asset {final.relative_to(self.assets_dir)} with "
                         f"different content for {spec.key}", code="asset_exists")

    def _record_failure(self, spec: DatasetSpec, meta: dict[str, Any] | None, exc: IngestionError) -> None:
        if isinstance(exc, ConflictError) and exc.code in ("checksum_conflict", "import_in_progress"):
            return  # the existing record is someone else's; leave it untouched
        provenance = dict(meta["provenance"]) if meta else {}
        ing = dict(provenance.get("ingestion") or {})
        ing.update(error=exc.to_dict(), failed_at=_now())
        provenance["ingestion"] = ing
        recorded = self.catalog.record_failure(spec, meta, provenance)
        log.error("%s failed [%s]: %s%s", spec.key, exc.code, exc.message,
                  "" if recorded else " (no catalog row recorded)")


def _fsync_file(path: Path) -> None:
    with open(path, "rb") as fh:
        os.fsync(fh.fileno())
