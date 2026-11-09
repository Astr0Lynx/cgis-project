import dataclasses
import hashlib
import json

import pytest

from cgis_data import fetch as F
from cgis_data import sources as S

PAYLOAD = bytes(range(256)) * 4000      # ~1 MB


def _put(served, name="f.bin", data=PAYLOAD):
    (served / name).write_bytes(data)
    return hashlib.sha256(data).hexdigest()


def test_download_and_idempotent_skip(http_server, tmp_path):
    served, base = http_server
    sha = _put(served)
    r1 = F.download_resumable(f"{base}/f.bin", tmp_path / "o" / "f.bin", expected_sha256=sha)
    assert not r1["skipped"] and r1["sha256"] == sha
    r2 = F.download_resumable(f"{base}/f.bin", tmp_path / "o" / "f.bin", expected_sha256=sha)
    assert r2["skipped"]


def test_resume_from_partial(http_server, tmp_path):
    served, base = http_server
    sha = _put(served)
    dest = tmp_path / "o" / "f.bin"
    dest.parent.mkdir()
    (dest.parent / "f.bin.part").write_bytes(PAYLOAD[:300000])
    r = F.download_resumable(f"{base}/f.bin", dest, expected_sha256=sha)
    assert r["resumed_from"] == 300000 and dest.read_bytes() == PAYLOAD and not (dest.parent / "f.bin.part").exists()


def test_resume_when_server_ignores_range(http_server, tmp_path, monkeypatch):
    served, base = http_server
    sha = _put(served)
    from conftest import RangeHandler
    monkeypatch.setattr(RangeHandler, "honour_range", False)
    dest = tmp_path / "o" / "f.bin"
    dest.parent.mkdir()
    (dest.parent / "f.bin.part").write_bytes(b"garbage-prefix" * 10)
    F.download_resumable(f"{base}/f.bin", dest, expected_sha256=sha)
    assert dest.read_bytes() == PAYLOAD


def test_checksum_mismatch_rejected_and_nothing_published(http_server, tmp_path):
    served, base = http_server
    _put(served)
    dest = tmp_path / "o" / "f.bin"
    with pytest.raises(F.ChecksumMismatch):
        F.download_resumable(f"{base}/f.bin", dest, expected_sha256="0" * 64)
    assert not dest.exists()


def test_existing_corrupt_file_detected(http_server, tmp_path):
    served, base = http_server
    sha = _put(served)
    dest = tmp_path / "f.bin"
    dest.write_bytes(b"x" * len(PAYLOAD))          # right size, wrong content
    with pytest.raises(F.ChecksumMismatch):
        F.download_resumable(f"{base}/f.bin", dest, expected_sha256=sha, expected_size=len(PAYLOAD))


@pytest.mark.parametrize("sid", ["india-bhuvan-lulc-50k-2011-12", "india-census-2011-pca", "india-admin-boundaries-2011"])
def test_gated_sources_refused(sid, tmp_path):
    src = next(s for s in S.all_sources() if s.source_id == sid)
    with pytest.raises(F.RestrictedSourceError):
        F.fetch_source(src, tmp_path, tmp_path / "state.json")


def test_fetch_source_records_state(http_server, tmp_path):
    served, base = http_server
    sha = _put(served, "ind_ppp_2005.tif")
    src = dataclasses.replace(next(s for s in S.all_sources() if s.source_id == "india-worldpop-unconstrained-2005"),
                              url=f"{base}/ind_ppp_2005.tif")
    st = tmp_path / "state.json"
    F.fetch_source(src, tmp_path, st)
    rec = json.loads(st.read_text())["india-worldpop-unconstrained-2005"]
    assert rec["sha256"] == sha and rec["size_bytes"] == len(PAYLOAD)
    F.fetch_source(src, tmp_path, st)            # idempotent re-run verifies against recorded sha
