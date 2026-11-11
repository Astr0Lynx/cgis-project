# Streaming multipart/form-data receiver for admin uploads (python-multipart). The 'file' part goes straight
# to a server-named staging file while hashed; 'metadata' stays in memory under a small cap. Byte budgets are
# enforced as bytes arrive; any other part, nested multipart or encoding is refused. Used by routes_admin.

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncIterator

import anyio
from python_multipart.exceptions import MultipartParseError
from python_multipart.multipart import MultipartParser, parse_options_header

from . import errors, storage
from .config import MIB, Settings
from .errors import ApiError

SNIFF_BYTES = 16
FREE_SPACE_CHECK_EVERY = 256 * MIB


# What receive_multipart hands back to routes_admin._register_upload.
@dataclass
class ReceivedUpload:
    metadata_raw: bytes
    staged_name: str
    size: int
    sha256: str
    sniffed_kind: str
    original_filename: str | None
    part_content_type: str | None


# Internal: carries an ApiError out of the parser callbacks.
class _Abort(Exception):
    # Wrap the ApiError.
    def __init__(self, error: ApiError):
        super().__init__(error.message)
        self.error = error


# Byte limit for a kind (GeoJSON has its own, smaller cap). Used by _Receiver._decide_kind.
def _limit_for(kind: str | None, settings: Settings) -> int:
    if kind == "vector":
        return min(settings.max_vector_upload_bytes, settings.max_upload_bytes)
    return settings.max_upload_bytes


# 413 FILE_TOO_LARGE for a limit. Used by the receiver and preflight.
def _too_large(limit: int, kind: str | None) -> ApiError:
    what = {"raster": "raster", "vector": "GeoJSON"}.get(kind or "", "upload")
    return ApiError(413, errors.FILE_TOO_LARGE, f"the {what} file exceeds the {limit} byte limit",
                    {"limit_bytes": limit})


# python-multipart callback target holding the per-request parse state.
class _Receiver:
    # Start with no parts seen and the raster byte limit.
    def __init__(self, settings: Settings, staging_root: Path, staged_stem: str):
        self.settings = settings
        self.root = staging_root
        self.stem = staged_stem
        self.headers: dict[bytes, bytes] = {}
        self._hname = bytearray()
        self._hval = bytearray()
        self.current: str | None = None
        self.metadata = bytearray()
        self.metadata_parts = 0
        self.file_parts = 0
        self.file_done = False
        self.head = bytearray()
        self.kind: str | None = None
        self.limit = settings.max_upload_bytes
        self.size = 0
        self.pending = bytearray()
        self.filename: str | None = None
        self.part_ctype: str | None = None
        self.fd: int | None = None
        self.staged_name: str | None = None
        self.hasher = hashlib.sha256()
        self._written_since_check = 0

    # -- python-multipart callbacks (synchronous) -------------------------------------------------
    # Parser callback: reset headers for a new part.
    def on_part_begin(self) -> None:
        self.headers = {}
        self.current = None

    # Parser callback: accumulate a header name.
    def on_header_field(self, data: bytes, start: int, end: int) -> None:
        self._hname.extend(data[start:end])

    # Parser callback: accumulate a header value.
    def on_header_value(self, data: bytes, start: int, end: int) -> None:
        self._hval.extend(data[start:end])

    # Parser callback: store one complete header.
    def on_header_end(self) -> None:
        self.headers[bytes(self._hname).strip().lower()] = bytes(self._hval).strip()
        self._hname.clear()
        self._hval.clear()

    # Parser callback: accept exactly one 'metadata' and one 'file' part, refuse anything else.
    def on_headers_finished(self) -> None:
        disposition, params = parse_options_header(self.headers.get(b"content-disposition", b""))
        if disposition != b"form-data":
            raise _Abort(ApiError(422, errors.INVALID_REQUEST, "every multipart part needs Content-Disposition: "
                                                               "form-data"))
        if b"content-transfer-encoding" in self.headers:
            raise _Abort(ApiError(415, errors.UNSUPPORTED_MEDIA_TYPE, "Content-Transfer-Encoding is not supported"))
        name = params.get(b"name", b"").decode("utf-8", "replace")
        if name == "metadata":
            self.metadata_parts += 1
            if self.metadata_parts > 1:
                raise _Abort(ApiError(422, errors.INVALID_METADATA, "send exactly one 'metadata' part"))
            self.current = "metadata"
        elif name == "file":
            self.file_parts += 1
            if self.file_parts > 1:
                raise _Abort(ApiError(422, errors.INVALID_REQUEST, "send exactly one 'file' part"))
            ctype = self.headers.get(b"content-type", b"").decode("latin-1", "replace")[:100]
            if ctype.lower().startswith("multipart/"):
                raise _Abort(ApiError(415, errors.UNSUPPORTED_MEDIA_TYPE, "nested multipart is not supported"))
            raw_name = params.get(b"filename")
            self.filename = storage.display_filename(raw_name.decode("utf-8", "replace")) if raw_name else None
            self.part_ctype = ctype or None
            self.current = "file"
        else:
            raise _Abort(ApiError(422, errors.INVALID_REQUEST,
                                  "unexpected form field; only 'metadata' and 'file' are accepted",
                                  {"field": name[:64]}))

    # Parser callback: buffer metadata (capped) or file bytes (sniffed, counted against the limit).
    def on_part_data(self, data: bytes, start: int, end: int) -> None:
        chunk = data[start:end]
        if self.current == "metadata":
            if len(self.metadata) + len(chunk) > self.settings.max_metadata_bytes:
                raise _Abort(ApiError(413, errors.REQUEST_TOO_LARGE,
                                      f"metadata exceeds {self.settings.max_metadata_bytes} bytes"))
            self.metadata.extend(chunk)
        elif self.current == "file":
            if self.kind is None:
                need = SNIFF_BYTES - len(self.head)
                self.head.extend(chunk[:need])
                if len(self.head) >= SNIFF_BYTES:
                    self._decide_kind()
            self.size += len(chunk)
            if self.size > self.limit:
                raise _Abort(_too_large(self.limit, self.kind))
            self.pending.extend(chunk)

    # Parser callback: finish a part (decide the kind of a tiny file).
    def on_part_end(self) -> None:
        if self.current == "file":
            if self.kind is None:
                self._decide_kind()
            self.file_done = True
        self.current = None

    # Callback dict for MultipartParser. Used by receive_multipart.
    def callbacks(self) -> dict:
        return {name: getattr(self, name) for name in (
            "on_part_begin", "on_header_field", "on_header_value", "on_header_end", "on_headers_finished",
            "on_part_data", "on_part_end")}

    # -- helpers ---------------------------------------------------------------------------------
    # Check magic bytes vs extension, set the kind's limit and create the staged file.
    def _decide_kind(self) -> None:
        kind = storage.sniff_kind(bytes(self.head))
        if kind is None:
            raise _Abort(ApiError(415, errors.UNSUPPORTED_MEDIA_TYPE,
                                  "the file is neither a GeoTIFF (TIFF/BigTIFF) nor a GeoJSON object; archives, "
                                  "compressed files and other formats are not accepted"))
        ext_kind = storage.extension_kind(self.filename)
        if self.filename and ext_kind != kind:
            raise _Abort(ApiError(415, errors.UNSUPPORTED_MEDIA_TYPE,
                                  "the file name extension does not match its content (.tif/.tiff for GeoTIFF, "
                                  ".geojson/.json for GeoJSON)"))
        self.kind = kind
        self.limit = _limit_for(kind, self.settings)
        if self.size > self.limit:
            raise _Abort(_too_large(self.limit, kind))
        self.staged_name = f"{self.stem}.{storage.EXT_FOR_KIND[kind]}"
        self.fd = storage.create_exclusive(self.root, self.staged_name)

    # Write pending file bytes and update the hash (runs in a worker thread).
    def flush(self) -> None:
        if not self.pending:
            return
        if self.fd is None:
            if len(self.pending) > SNIFF_BYTES * 4:
                raise _Abort(ApiError(422, errors.INVALID_REQUEST, "could not determine the uploaded file type"))
            return  # still sniffing a tiny prefix
        view = memoryview(bytes(self.pending))
        self.pending.clear()
        self.hasher.update(view)
        while view:
            n = os.write(self.fd, view)
            view = view[n:]

    # Re-check the free-space floor every 256 MiB written (507 STORAGE_FULL).
    def check_space(self, written_total: int) -> None:
        if written_total - self._written_since_check < FREE_SPACE_CHECK_EVERY:
            return
        self._written_since_check = written_total
        if storage.free_bytes(self.root) < self.settings.min_free_bytes:
            raise _Abort(ApiError(507, errors.STORAGE_FULL, "the staging volume is almost full; upload refused"))

    # fsync and close the staged file.
    def finish(self) -> None:
        if self.fd is not None:
            os.fsync(self.fd)
            os.close(self.fd)
            self.fd = None

    # Close and delete a partial staged file.
    def discard(self) -> None:
        if self.fd is not None:
            try:
                os.close(self.fd)
            except OSError:
                pass
            self.fd = None
        if self.staged_name:
            storage.remove_staged(self.root, self.staged_name)


# Refuse before reading the body when Content-Length, free space or the staging quota rule it out.
# Used by routes_admin._handle_upload.
def preflight(settings: Settings, staging_root: Path, content_length: int | None) -> None:
    if content_length is not None and content_length > settings.max_upload_bytes + settings.max_metadata_bytes + MIB:
        raise _too_large(settings.max_upload_bytes, None)
    expected = content_length or 0
    if storage.free_bytes(staging_root) - expected < settings.min_free_bytes:
        raise ApiError(507, errors.STORAGE_FULL, "the staging volume does not have enough free space for this upload")
    if storage.used_bytes(staging_root) + expected > settings.staging_max_total_bytes:
        raise ApiError(507, errors.STORAGE_FULL,
                       "the staging quota is exhausted; publish or discard staged uploads first",
                       {"quota_bytes": settings.staging_max_total_bytes})


# Stream the request body into staging; returns ReceivedUpload or raises ApiError (nothing left on
# disk). Used by routes_admin._handle_upload.
async def receive_multipart(stream: AsyncIterator[bytes], content_type: str, *, settings: Settings,
                            staging_root: Path, staged_stem: str) -> ReceivedUpload:
    ctype, params = parse_options_header(content_type or "")
    if ctype != b"multipart/form-data":
        raise ApiError(415, errors.UNSUPPORTED_MEDIA_TYPE,
                       "send multipart/form-data with a 'metadata' JSON part and a 'file' part")
    boundary = params.get(b"boundary")
    if not boundary or len(boundary) > 200:
        raise ApiError(400, errors.INVALID_REQUEST, "multipart boundary is missing or too long")

    rx = _Receiver(settings, staging_root, staged_stem)
    parser = MultipartParser(boundary, rx.callbacks(), max_size=float(settings.max_upload_bytes + 2 * MIB),
                             max_header_count=8, max_header_size=4096)
    received = 0
    try:
        async for chunk in stream:
            if not chunk:
                continue
            received += len(chunk)
            if received > settings.max_upload_bytes + settings.max_metadata_bytes + MIB:
                raise _Abort(_too_large(settings.max_upload_bytes, rx.kind))
            parser.write(chunk)
            if rx.pending:
                await anyio.to_thread.run_sync(rx.flush)
                rx.check_space(rx.size)
        parser.finalize()
        if rx.pending:
            await anyio.to_thread.run_sync(rx.flush)
        if rx.file_parts == 0 or not rx.file_done or rx.size == 0:
            raise _Abort(ApiError(422, errors.INVALID_REQUEST, "a non-empty 'file' part is required"))
        if rx.metadata_parts == 0:
            raise _Abort(ApiError(422, errors.INVALID_METADATA, "a 'metadata' part (JSON text) is required"))
        if rx.fd is None and rx.kind is None:
            raise _Abort(ApiError(422, errors.INVALID_REQUEST, "could not determine the uploaded file type"))
        await anyio.to_thread.run_sync(rx.finish)
    except _Abort as exc:
        await anyio.to_thread.run_sync(rx.discard)
        raise exc.error from None
    except MultipartParseError as exc:
        await anyio.to_thread.run_sync(rx.discard)
        raise ApiError(400, errors.INVALID_REQUEST, "malformed multipart body") from exc
    except BaseException:
        await anyio.to_thread.run_sync(rx.discard)
        raise
    return ReceivedUpload(metadata_raw=bytes(rx.metadata), staged_name=rx.staged_name or "", size=rx.size,
                          sha256=rx.hasher.hexdigest(), sniffed_kind=rx.kind or "", original_filename=rx.filename,
                          part_content_type=rx.part_ctype)
