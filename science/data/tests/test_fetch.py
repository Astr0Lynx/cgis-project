# Tests for fetch.py against a local HTTP server (resume, checksum, gated sources, state).
import dataclasses
import hashlib
import json

import pytest

from cgis_data import fetch as F
from cgis_data import sources as S

PAYLOAD = bytes(range(256)) * 4000      # ~1 MB


# Places a payload in the served dir and returns its sha256.
def _put(served, name="f.bin", data=PAYLOAD):
    (served / name).write_bytes(data)
    return hashlib.sha256(data).hexdigest()


# A verified download is skipped on re-run.
def test_download_and_idempotent_skip(http_server, tmp_path):
    served, base = http_server
    sha = _put(served)
    r1 = F.download_resumable(f"{base}/f.bin", tmp_path / "o" / "f.bin", expected_sha256=sha)
    assert not r1["skipped"] and r1["sha256"] == sha
    r2 = F.download_resumable(f"{base}/f.bin", tmp_path / "o" / "f.bin", expected_sha256=sha)
    assert r2["skipped"]


# A .part file is resumed with Range and removed after success.
def test_resume_from_partial(http_server, tmp_path):
    served, base = http_server
    sha = _put(served)
    dest = tmp_path / "o" / "f.bin"
    dest.parent.mkdir()
    (dest.parent / "f.bin.part").write_bytes(PAYLOAD[:300000])
    r = F.download_resumable(f"{base}/f.bin", dest, expected_sha256=sha)
    assert r["resumed_from"] == 300000 and dest.read_bytes() == PAYLOAD and not (dest.parent / "f.bin.part").exists()


# A server ignoring Range causes a clean restart, not a corrupt file.
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


# A sha256 mismatch raises and publishes nothing.
def test_checksum_mismatch_rejected_and_nothing_published(http_server, tmp_path):
    served, base = http_server
    _put(served)
    dest = tmp_path / "o" / "f.bin"
    with pytest.raises(F.ChecksumMismatch):
        F.download_resumable(f"{base}/f.bin", dest, expected_sha256="0" * 64)
    assert not dest.exists()


# An existing right-size wrong-content file is detected.
def test_existing_corrupt_file_detected(http_server, tmp_path):
    served, base = http_server
    sha = _put(served)
    dest = tmp_path / "f.bin"
    dest.write_bytes(b"x" * len(PAYLOAD))          # right size, wrong content
    with pytest.raises(F.ChecksumMismatch):
        F.download_resumable(f"{base}/f.bin", dest, expected_sha256=sha, expected_size=len(PAYLOAD))


# Gated sources are refused by fetch_source.
@pytest.mark.parametrize("sid", ["india-bhuvan-lulc-50k-2011-12", "india-census-2011-pca", "india-admin-boundaries-2011"])
def test_gated_sources_refused(sid, tmp_path):
    src = next(s for s in S.all_sources() if s.source_id == sid)
    with pytest.raises(F.RestrictedSourceError):
        F.fetch_source(src, tmp_path, tmp_path / "state.json")


# fetch_source records size/sha256 in the state and re-runs idempotently.
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


# A HEAD length smaller than the body does not loop forever.
def test_head_content_length_lie_does_not_loop_forever(http_server, tmp_path, monkeypatch):
    served, base = http_server
    sha = _put(served, "lie.bin", b"x" * 145)
    monkeypatch.setattr(F, "head_size", lambda *a, **k: 139)
    r = F.download_resumable(f"{base}/lie.bin", tmp_path / "o" / "lie.bin", expected_sha256=sha, max_retries=1)
    assert r["size"] == 145 and (tmp_path / "o" / "lie.bin").read_bytes() == b"x" * 145


# An explicit expected_size is enforced.
def test_explicit_expected_size_is_still_enforced(http_server, tmp_path):
    served, base = http_server
    _put(served, "e.bin", b"y" * 100)
    with pytest.raises(F.FetchError, match="expected 99"):
        F.download_resumable(f"{base}/e.bin", tmp_path / "o" / "e.bin", expected_size=99, max_retries=1)
