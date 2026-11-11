"""Pure unit tests: hashing, tokens, metadata sanitization, path safety, upload stream parsing, config."""

from __future__ import annotations

import asyncio
import json
import os

import pytest

from cgis_app import catalog, security, storage, upload_stream
from cgis_app.config import ConfigError, MIB, Settings
from cgis_app.errors import ApiError


# ------------------------------------------------------------------ passwords and tokens
def test_password_hash_is_argon2id_and_verifies():
    h = security.hash_password("correct horse battery staple")
    assert h.startswith("$argon2id$")
    assert "correct horse" not in h
    assert security.verify_password(h, "correct horse battery staple")
    assert not security.verify_password(h, "wrong password entirely")
    assert not security.verify_password(None, "anything at all")  # unknown user: still does the work
    assert not security.needs_rehash(h)


def test_password_policy():
    assert security.password_problems("short", "bob", 12)
    assert security.password_problems("CHANGE_ME_12345", "bob", 12)
    assert security.password_problems("bobbobbob-is-me", "bob", 12)
    assert security.password_problems("aaaaaaaaaaaaaaaa", "x", 12)
    assert security.password_problems("x7#Lq9!vR2@pZm", "alice", 12) == []


def test_tokens_are_random_prefixed_and_digest_only():
    a, b = security.new_token("pat"), security.new_token("pat")
    assert a != b and a.startswith("cgis_pat_") and security.looks_like_token(a)
    assert security.new_token("browser").startswith("cgis_s_")
    assert len(security.token_digest(a)) == 32
    assert not security.looks_like_token("cgis_pat_short")
    assert not security.looks_like_token("Bearer " + a)
    assert security.constant_time_equals("abc", "abc") and not security.constant_time_equals("abc", None)


# ------------------------------------------------------------------ metadata hygiene
def test_public_provenance_removes_paths_urls_and_secrets():
    prov = {
        "license": "CC BY 4.0",
        "citation": "https://doi.org/10.5258/SOTON/WP00645",
        "download_url": "https://bhuvan.example/secret?token=abc",
        "local_path": "/home/someone/data/x.tif",
        "notes": "/data/incoming/x.tif",
        "api_key": "k",
        "group_id": "india-level2-2011",
        "ingestion": {"source_file": "abc.tif", "asset_path": "rasters/x/v1/x.tif", "error": {"message": "trace"},
                      "importer": "cgis-ingestion", "asset_sha256": "0" * 64},
        "upload": {"uploaded_by": "alice", "size_bytes": 10, "original_filename": "x.tif"},
        "source": {"license": "ODbL", "url": "http://x"},
    }
    out = catalog.public_provenance(prov)
    text = json.dumps(out)
    for leaked in ("bhuvan", "/home/", "/data/", "api_key", "abc.tif", "rasters/x", "trace", "alice", "http://x"):
        assert leaked not in text, leaked
    assert out["license"] == "CC BY 4.0"
    assert out["citation"].startswith("https://doi.org/")
    assert out["group_id"] == "india-level2-2011"
    assert out["ingestion"]["importer"] == "cgis-ingestion"


def test_public_source_withholds_links_and_paths():
    assert catalog.public_source("WorldPop https://data.worldpop.org/a/b.tif?sig=1") == \
        "WorldPop data.worldpop.org (link withheld)"
    assert "withheld" in catalog.public_source("/home/u/data/x.tif")
    assert catalog.public_source("Census of India 2011 PCA") == "Census of India 2011 PCA"


def test_dataset_id_slug():
    assert catalog.valid_dataset_id("india-l1-pop-2011")
    for bad in ("../x", "/etc/passwd", "UPPER", "a b", "http://x", "x/y", "", "-x", "x" * 200):
        assert not catalog.valid_dataset_id(bad), bad


def test_access_levels():
    assert catalog.allowed_levels(None) == ["public"]
    assert catalog.can_read("users", "user") and not catalog.can_read("admins", "user")
    assert catalog.can_read("admins", "admin") and not catalog.can_read("users", None)


# ------------------------------------------------------------------ storage safety
def test_display_filename_strips_paths_and_controls():
    assert storage.display_filename("../../etc/passwd.tif") == "passwd.tif"
    assert storage.display_filename("C:\\Users\\x\\pop.tif") == "pop.tif"
    assert storage.display_filename("a\x00b\x1b[31m.tif") == "ab_31m.tif"
    assert storage.display_filename("..") is None


def test_sniff_kind():
    assert storage.sniff_kind(b"II*\x00rest") == "raster"
    assert storage.sniff_kind(b"MM\x00+rest") == "raster"
    assert storage.sniff_kind(b"\xef\xbb\xbf  {\"type\"") == "vector"
    assert storage.sniff_kind(b"PK\x03\x04") is None
    assert storage.sniff_kind(b"\x89PNG") is None


def test_create_exclusive_refuses_existing_and_symlinks(tmp_path):
    root = storage.ensure_root(tmp_path / "staging")
    fd = storage.create_exclusive(root, "a" * 32 + ".tif")
    os.close(fd)
    with pytest.raises(FileExistsError):
        storage.create_exclusive(root, "a" * 32 + ".tif")
    target = tmp_path / "outside.txt"
    target.write_text("x")
    os.symlink(target, root / ("b" * 32 + ".tif"))
    with pytest.raises(OSError):
        storage.create_exclusive(root, "b" * 32 + ".tif")
    assert target.read_text() == "x"
    with pytest.raises(storage.StorageError):
        storage.create_exclusive(root, "../escape.tif")
    with pytest.raises(storage.StorageError):
        storage.staged_path(root, "b" * 32 + ".tif")  # symlink is not a regular file


def test_symlinked_root_is_refused(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    os.symlink(real, tmp_path / "link")
    with pytest.raises(storage.StorageError):
        storage.ensure_root(tmp_path / "link")


def test_resolve_asset_rejects_escapes(tmp_path):
    root = tmp_path / "assets"
    (root / "rasters").mkdir(parents=True)
    good = root / "rasters" / "x.tif"
    good.write_bytes(b"II*\x00")
    assert storage.resolve_asset(root, "rasters/x.tif", "raster") == good.resolve()
    outside = tmp_path / "secret.tif"
    outside.write_bytes(b"x")
    os.symlink(outside, root / "rasters" / "evil.tif")
    for bad in ("../secret.tif", "/etc/passwd", "rasters/../../secret.tif", "file:///etc/passwd", "/vsicurl/http://x",
                "rasters\\x.tif", "rasters/evil.tif", "rasters/x.vrt"):
        with pytest.raises(storage.StorageError):
            storage.resolve_asset(root, bad, "raster")


def test_resolve_result_rejects_bad_paths(tmp_path):
    root = tmp_path / "exports"
    d = storage.make_result_dir(storage.ensure_root(root), "c" * 32)
    (d / "out.tif").write_bytes(b"x")
    assert storage.resolve_result(root, "c" * 32 + "/out.tif").name == "out.tif"
    for bad in ("../x", "c" * 32 + "/../../x", "/abs", "c" * 32 + "/missing.tif"):
        with pytest.raises(storage.StorageError):
            storage.resolve_result(root, bad)


# ------------------------------------------------------------------ streaming multipart
def _body(parts: list[tuple[str, str | None, bytes, str | None]], boundary: str = "XyZ123") -> bytes:
    out = b""
    for name, filename, data, ctype in parts:
        disp = f'form-data; name="{name}"' + (f'; filename="{filename}"' if filename else "")
        out += f"--{boundary}\r\nContent-Disposition: {disp}\r\n".encode()
        if ctype:
            out += f"Content-Type: {ctype}\r\n".encode()
        out += b"\r\n" + data + b"\r\n"
    return out + f"--{boundary}--\r\n".encode()


async def _stream(data: bytes, chunk: int = 7000):
    for i in range(0, len(data), chunk):
        yield data[i:i + chunk]


def _receive(tmp_path, body: bytes, **overrides):
    settings = Settings(max_upload_bytes=overrides.pop("max_upload", 2 * MIB),
                        max_vector_upload_bytes=overrides.pop("max_vector", 1 * MIB), min_free_bytes=0)
    root = storage.ensure_root(tmp_path / "staging")
    coro = upload_stream.receive_multipart(_stream(body), "multipart/form-data; boundary=XyZ123", settings=settings,
                                           staging_root=root, staged_stem="d" * 32)
    return asyncio.run(coro), root


TIFF = b"II*\x00" + bytes(range(256)) * 300
META = json.dumps({"dataset_id": "x", "version": 1}).encode()


def test_stream_file_first_order_and_checksum(tmp_path):
    import hashlib

    rx, root = _receive(tmp_path, _body([("file", "pop.tif", TIFF, "image/tiff"), ("metadata", None, META, None)]))
    assert rx.sniffed_kind == "raster" and rx.size == len(TIFF)
    assert rx.sha256 == hashlib.sha256(TIFF).hexdigest()
    assert (root / rx.staged_name).read_bytes() == TIFF
    assert rx.staged_name == "d" * 32 + ".tif" and rx.original_filename == "pop.tif"
    assert json.loads(rx.metadata_raw) == {"dataset_id": "x", "version": 1}


@pytest.mark.parametrize("parts,code,status", [
    ([("metadata", None, META, None)], "INVALID_REQUEST", 422),
    ([("file", "a.tif", TIFF, None)], "INVALID_METADATA", 422),
    ([("file", "a.tif", TIFF, None), ("file", "b.tif", TIFF, None), ("metadata", None, META, None)],
     "INVALID_REQUEST", 422),
    ([("metadata", None, META, None), ("role", None, b"admin", None), ("file", "a.tif", TIFF, None)],
     "INVALID_REQUEST", 422),
    ([("metadata", None, META, None), ("file", "a.zip", b"PK\x03\x04" + b"0" * 100, None)],
     "UNSUPPORTED_MEDIA_TYPE", 415),
    ([("metadata", None, META, None), ("file", "a.geojson", TIFF, None)], "UNSUPPORTED_MEDIA_TYPE", 415),
    ([("metadata", None, META, None), ("file", "a.tif", b"{\"type\":\"FeatureCollection\"}", None)],
     "UNSUPPORTED_MEDIA_TYPE", 415),
    ([("metadata", None, b"x" * 70000, None), ("file", "a.tif", TIFF, None)], "REQUEST_TOO_LARGE", 413),
    ([("metadata", None, META, None), ("file", "a.tif", b"II*\x00" + b"0" * (3 * MIB), None)], "FILE_TOO_LARGE", 413),
    ([("metadata", None, META, None), ("file", "a.json", b"{" + b" " * (int(1.5 * MIB)) + b"}", None)],
     "FILE_TOO_LARGE", 413),
    ([("metadata", None, META, None), ("file", "a.tif", b"II*\x00", "multipart/mixed")], "UNSUPPORTED_MEDIA_TYPE", 415),
])
def test_stream_rejections_leave_no_file(tmp_path, parts, code, status):
    with pytest.raises(ApiError) as err:
        _receive(tmp_path, _body(parts))
    assert (err.value.code, err.value.status) == (code, status)
    assert list((tmp_path / "staging").iterdir()) == []


def test_stream_rejects_non_multipart(tmp_path):
    settings = Settings(min_free_bytes=0)
    root = storage.ensure_root(tmp_path / "staging")
    with pytest.raises(ApiError) as err:
        asyncio.run(upload_stream.receive_multipart(_stream(b"abc"), "application/json", settings=settings,
                                                    staging_root=root, staged_stem="e" * 32))
    assert err.value.status == 415


def test_preflight_refuses_declared_oversize_and_full_disk(tmp_path):
    root = storage.ensure_root(tmp_path / "s")
    with pytest.raises(ApiError) as err:
        upload_stream.preflight(Settings(max_upload_bytes=MIB, min_free_bytes=0), root, 10 * MIB)
    assert err.value.code == "FILE_TOO_LARGE"
    with pytest.raises(ApiError) as err:
        upload_stream.preflight(Settings(min_free_bytes=1 << 62), root, 10)
    assert err.value.code == "STORAGE_FULL" and err.value.status == 507


# ------------------------------------------------------------------ configuration
def test_settings_from_env_and_redaction(monkeypatch):
    monkeypatch.setenv("APP_DATABASE_URL", "postgresql://svc:s3cr3t-pass@db:5432/cgis")
    monkeypatch.setenv("APP_PUBLIC_BASE_URL", "https://maps.example.org/")
    monkeypatch.setenv("APP_DEFAULT_DATASET_ACCESS", "public")
    s = Settings.from_env()
    assert s.cookie_secure is True and s.public_origin == "https://maps.example.org"
    assert s.allowed_origins == ("https://maps.example.org",)
    assert "s3cr3t" not in json.dumps(s.describe())
    monkeypatch.setenv("APP_DEFAULT_DATASET_ACCESS", "everyone")
    with pytest.raises(ConfigError):
        Settings.from_env()


def test_secret_file_variable(monkeypatch, tmp_path):
    from cgis_app.config import read_secret

    f = tmp_path / "pw"
    f.write_text("from-a-file\n")
    monkeypatch.setenv("APP_BOOTSTRAP_ADMIN_PASSWORD_FILE", str(f))
    assert read_secret("APP_BOOTSTRAP_ADMIN_PASSWORD") == "from-a-file"


def test_login_role_config_is_checked():
    from cgis_app.migrate import MigrationError, read_login_roles

    good = {"PGUSER": "owner", "APP_DB_USER": "cgis_app_svc", "APP_DB_PASSWORD": "a" * 32,
            "APP_WORKER_DB_USER": "cgis_app_worker_svc", "APP_WORKER_DB_PASSWORD": "b" * 32}
    assert len(read_login_roles(good)) == 2
    for patch in ({"APP_DB_PASSWORD": "CHANGE_ME_CHANGE_ME"}, {"APP_DB_PASSWORD": "short"},
                  {"APP_DB_USER": "cgis_reader"}, {"APP_DB_USER": "catalog"}, {"APP_DB_USER": "owner"},
                  {"APP_DB_USER": "Bad-Name"}, {"APP_WORKER_DB_USER": "cgis_app_svc"}):
        with pytest.raises(MigrationError):
            read_login_roles({**good, **patch})


def test_openapi_document_lists_contract(tmp_path):
    from cgis_app.app import create_app

    doc = create_app(Settings(), start_maintenance=False).openapi()
    assert doc["openapi"].startswith("3.")
    paths = doc["paths"]
    for p in ("/auth/login", "/auth/logout", "/auth/me", "/admin/datasets", "/admin/datasets/uploads",
              "/admin/datasets/{dataset_id}/versions/{version}/publish", "/admin/jobs/{job_id}", "/api/exports",
              "/api/exports/{job_id}/download", "/api/exports/capabilities", "/api/catalog/datasets"):
        assert p in paths, p
    assert paths["/admin/datasets"]["post"]["x-required-role"] == "admin"
    assert paths["/api/exports"]["post"]["x-required-role"] == "user"
    assert paths["/auth/me"]["get"]["security"] == []
    assert "sessionCookie" in doc["components"]["securitySchemes"]


def test_docs_page_is_not_blocked_by_csp():
    from fastapi.testclient import TestClient

    from cgis_app.app import create_app

    with TestClient(create_app(Settings(), start_maintenance=False)) as c:
        docs = c.get("/api/app/docs")
        assert docs.status_code == 200 and "content-security-policy" not in docs.headers
        caps = c.get("/api/exports/capabilities")
        assert caps.headers["content-security-policy"].startswith("default-src 'none'")
        assert caps.headers["x-content-type-options"] == "nosniff" and caps.headers["x-frame-options"] == "DENY"


def test_loopback_origins_are_aliases_only_for_local_hosts(monkeypatch):
    monkeypatch.setenv("APP_PUBLIC_BASE_URL", "http://localhost:8080")
    assert set(Settings.from_env().allowed_origins) == {"http://localhost:8080", "http://127.0.0.1:8080",
                                                        "http://[::1]:8080"}
    monkeypatch.setenv("APP_PUBLIC_BASE_URL", "https://maps.example.org")
    monkeypatch.setenv("APP_ALLOWED_ORIGINS", "https://viewer.example.org")
    assert Settings.from_env().allowed_origins == ("https://maps.example.org", "https://viewer.example.org")


def test_worker_health_file(tmp_path, monkeypatch):
    import os as _os

    from cgis_app.worker.runner import health_ok, touch_health

    monkeypatch.setenv("APP_WORKER_HEALTH_FILE", str(tmp_path / "alive"))
    assert not health_ok()
    touch_health()
    assert health_ok()
    _os.utime(tmp_path / "alive", (1, 1))
    assert not health_ok(120)
