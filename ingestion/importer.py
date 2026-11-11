# Idempotent, atomic dataset import, plus the server-owned asset layout under the assets root.
# Per dataset, under an advisory lock: checksum -> validate -> row `staged` -> build+verify asset in
# <assets>/.staging -> no-clobber placement -> ONE transaction (units + `ready`). Failures mark `failed`.

from __future__ import annotations

import errno
import hashlib
import json
import logging
import os
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

import rasterio

from . import IMPORTER_NAME, __version__
from .catalog import Catalog
from .errors import AssetError, ConflictError, IngestionError
from .manifest import DatasetSpec
from .raster import RasterInfo, inspect_raster, verify_cog, write_cog
from .vector import VectorInfo, read_vector

log = logging.getLogger(__name__)

STAGING_DIRNAME = ".staging"
_SUFFIX = {"raster": ".tif", "vector": ".geojson"}
_NO_HARDLINK_ERRNOS = {errno.EPERM, errno.EXDEV, errno.EMLINK, errno.ENOTSUP, errno.EOPNOTSUPP}


# ---- asset paths and files -------------------------------------------------------------------------

# Relative asset location, e.g. rasters/<id>/v1/<id>_v1.tif. Used by build_metadata, Importer.validate, tests.
def asset_relpath(kind: str, dataset_id: str, version: int) -> PurePosixPath:
    if kind not in _SUFFIX:
        raise AssetError(f"no asset layout for kind {kind!r}", code="unsupported_kind")
    return PurePosixPath(f"{kind}s", dataset_id, f"v{version}", f"{dataset_id}_v{version}{_SUFFIX[kind]}")


# Join `rel` onto `root`, refusing absolute paths, `..` and symlink escapes (AssetError unsafe_path).
# Used by Importer._already_ready, Importer._materialize_and_publish and tests.
def resolve_under(root: Path, rel: str | PurePosixPath) -> Path:
    rel_p = PurePosixPath(str(rel))
    if rel_p.is_absolute() or not rel_p.parts or any(p in ("..", "") for p in rel_p.parts):
        raise AssetError(f"unsafe asset path {str(rel)!r}", code="unsafe_path")
    candidate = root.joinpath(*rel_p.parts)
    real_root, real = root.resolve(), candidate.resolve()
    if real != real_root and real_root not in real.parents:
        raise AssetError(f"asset path {str(rel)!r} escapes the assets root", code="unsafe_path")
    return candidate


# Hex sha256 of a file, read in 1 MiB chunks. Used by Importer and tests.
def sha256_file(path: str | os.PathLike) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while block := fh.read(1 << 20):
            h.update(block)
    return h.hexdigest()


# fsync a file or directory so a placed asset survives a crash; errors on directories are ignored.
# Files open "r+b" because Windows' fsync needs a writable handle. Used by Importer._materialize_and_publish.
def _fsync(path: Path) -> None:
    if not path.is_dir():
        with open(path, "r+b") as fh:
            os.fsync(fh.fileno())
        return
    try:
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass


# Current UTC time as ISO-8601 seconds. Used for provenance timestamps.
def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# ---- results and metadata --------------------------------------------------------------------------

# Per-dataset outcome printed by the CLI. Returned by Importer.validate/import_dataset.
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

    # The JSON line the CLI prints for this dataset. Used by cli._run_manifest.
    def to_dict(self) -> dict[str, Any]:
        d = {"dataset_id": self.dataset_id, "version": self.version, "kind": self.kind, "outcome": self.outcome,
             "asset_path": self.asset_path, "checksum_sha256": self.checksum_sha256}
        if self.summary:
            d["summary"] = self.summary
        if self.error:
            d["error"] = self.error
        return d


# Validate the source file read-only, by kind. Used by Importer.validate and Importer.import_dataset.
def validate_source(spec: DatasetSpec) -> RasterInfo | VectorInfo:
    return inspect_raster(spec) if spec.kind == "raster" else read_vector(spec)


# Catalog row values derived from the file (never trusted from the manifest), including provenance.
# Used by Importer.import_dataset.
def build_metadata(spec: DatasetSpec, info: RasterInfo | VectorInfo, checksum: str) -> dict[str, Any]:
    provenance: dict[str, Any] = dict(spec.provenance)
    if spec.is_synthetic:
        provenance.setdefault("synthetic", True)
    if spec.extra:
        provenance["manifest_extra"] = spec.extra
    provenance["ingestion"] = {
        "importer": IMPORTER_NAME, "importer_version": __version__, "gdal_version": rasterio.__gdal_version__,
        "source_file": spec.file.name, "source_sha256": checksum, "started_at": _now(),
    }
    meta: dict[str, Any] = {"asset_path": str(asset_relpath(spec.kind, spec.dataset_id, spec.version)),
                            "checksum_sha256": checksum, "bbox_wgs84": info.bbox_wgs84}
    if isinstance(info, RasterInfo):
        provenance["raster"] = info.to_provenance()
        meta.update(crs=info.crs, resolution_x=info.resolution_x, resolution_y=info.resolution_y,
                    nodata=None if info.nodata is None else float(info.nodata))
    else:
        provenance["vector"] = info.to_provenance(spec)
        meta.update(crs="EPSG:4326", resolution_x=None, resolution_y=None, nodata=None)
    meta["provenance"] = provenance
    return meta


# Short summary included in the CLI result line. Used by Importer.validate and _materialize_and_publish.
def summarize(info: RasterInfo | VectorInfo) -> dict[str, Any]:
    if isinstance(info, RasterInfo):
        return {"crs": info.crs, "width": info.width, "height": info.height, "dtype": info.dtype,
                "nodata": info.nodata, "bbox_wgs84": info.bbox_wgs84, "valid_count": info.stats["valid_count"],
                "sum": info.stats["sum"]}
    return {"crs": "EPSG:4326", "source_crs": info.source_crs, "feature_count": len(info.features),
            "bbox_wgs84": info.bbox_wgs84}


# ---- importer --------------------------------------------------------------------------------------

# Validates and imports DatasetSpecs into the catalog and the assets root.
class Importer:
    # `catalog` may be None for validate-only runs. Used by cli._run_manifest and tests.
    def __init__(self, catalog: Catalog | None, assets_dir: Path):
        self.catalog = catalog
        self.assets_dir = assets_dir

    # Validate only: no database, no asset writes. Used by the `validate` CLI command.
    def validate(self, spec: DatasetSpec) -> ImportResult:
        checksum = sha256_file(spec.file)
        self._check_expected_checksum(spec, checksum)
        info = validate_source(spec)
        return ImportResult(spec.dataset_id, spec.version, spec.kind, "validated",
                            asset_path=str(asset_relpath(spec.kind, spec.dataset_id, spec.version)),
                            checksum_sha256=checksum, summary=summarize(info))

    # Full idempotent import of one dataset (see file header). Used by the `import`/`seed` CLI commands.
    def import_dataset(self, spec: DatasetSpec) -> ImportResult:
        if self.catalog is None:
            raise IngestionError("import requires a database connection", code="db_unavailable")
        checksum = sha256_file(spec.file)
        with self.catalog.lock(spec):
            existing = self.catalog.get(spec)
            if existing is not None and existing["status"] == "ready":
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

    # Reject a file whose sha256 differs from the manifest's checksum_sha256. Used by validate, import_dataset.
    @staticmethod
    def _check_expected_checksum(spec: DatasetSpec, checksum: str) -> None:
        if spec.checksum_sha256 and spec.checksum_sha256 != checksum:
            raise AssetError(f"{spec.file.name} sha256 {checksum} does not match manifest {spec.checksum_sha256}",
                             code="checksum_mismatch")

    # A ready row exists: same checksum -> `unchanged` after re-verifying the asset; different -> conflict.
    # Used by import_dataset.
    def _already_ready(self, spec: DatasetSpec, existing: dict[str, Any], checksum: str) -> ImportResult:
        old = existing["checksum_sha256"]
        if old != checksum:
            raise ConflictError(
                f"{spec.key} is already ready with checksum {old[:12]}…; the new file "
                f"({checksum[:12]}…) differs. Publish it as a new version instead.",
                code="checksum_conflict", details={"existing_checksum": old, "new_checksum": checksum})
        rel = existing["asset_path"]
        if rel:
            final = resolve_under(self.assets_dir, rel)
            if not final.is_file():
                raise AssetError(f"{spec.key} is ready but its asset {rel} is missing from "
                                 f"{self.assets_dir}; is the assets volume mounted?", code="asset_missing")
            expected = (existing["provenance"].get("ingestion") or {}).get("asset_sha256")
            if expected and sha256_file(final) != expected:
                raise AssetError(f"{spec.key} asset {rel} no longer matches its recorded checksum",
                                 code="asset_corrupted")
        log.info("%s already ready with identical checksum; nothing to do", spec.key)
        return ImportResult(spec.dataset_id, spec.version, spec.kind, "unchanged", asset_path=rel,
                            checksum_sha256=checksum)

    # Build the asset in staging, place it without clobbering, then publish; on any error remove the
    # final file this attempt created. Used by import_dataset.
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
            _fsync(tmp_asset)
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
                # No hard links on this filesystem: rename is still atomic, and the advisory lock
                # guarantees no other importer targets this path between the check and the rename.
                if final.exists():
                    self._adopt_existing(spec, info, final, asset_sha)
                else:
                    os.rename(tmp_asset, final)
                    created_final = True
            if created_final:
                _fsync(final.parent)

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

    # Write and verify the COG (raster) or normalized GeoJSON (vector) at `tmp_asset`; returns
    # {"asset": details} for provenance. Used by _materialize_and_publish.
    def _build_asset(self, spec: DatasetSpec, info, tmp_asset: Path) -> dict[str, Any]:
        if isinstance(info, RasterInfo):
            tags = {"CGIS_DATASET_ID": spec.dataset_id, "CGIS_DATASET_VERSION": str(spec.version),
                    "CGIS_UNITS": spec.units or ""}
            if spec.is_synthetic:
                tags["SYNTHETIC"] = "TRUE"
            write_cog(spec, info, tmp_asset, tags)
            return {"asset": verify_cog(spec, info, tmp_asset)}
        tmp_asset.write_bytes(info.to_geojson_bytes(spec))
        back = json.loads(tmp_asset.read_text(encoding="utf-8"))["features"]
        if [f["id"] for f in back] != [u.unit_id for u in info.features]:
            raise AssetError(f"normalized GeoJSON for {spec.key} failed read-back", code="asset_verification_failed")
        return {"asset": {"format": "GeoJSON", "crs": "EPSG:4326", "geometry_type": "MultiPolygon"}}

    # A file already sits at the final path (e.g. crash after link, before commit): reuse it if it is
    # identical (or an equivalent COG), otherwise refuse with asset_exists. Used by _materialize_and_publish.
    def _adopt_existing(self, spec: DatasetSpec, info, final: Path, new_sha: str) -> None:
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

    # Store the error in provenance.ingestion.error and mark the row failed, except for conflicts on
    # someone else's record. Used by import_dataset.
    def _record_failure(self, spec: DatasetSpec, meta: dict[str, Any] | None, exc: IngestionError) -> None:
        if isinstance(exc, ConflictError) and exc.code in ("checksum_conflict", "import_in_progress"):
            return
        provenance = dict(meta["provenance"]) if meta else {}
        provenance["ingestion"] = {**(provenance.get("ingestion") or {}), "error": exc.to_dict(),
                                   "failed_at": _now()}
        recorded = self.catalog.record_failure(spec, meta, provenance)
        log.error("%s failed [%s]: %s%s", spec.key, exc.code, exc.message,
                  "" if recorded else " (no catalog row recorded)")
