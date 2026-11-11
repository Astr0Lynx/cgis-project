"""JSON views of job rows (admin jobs and export jobs). Never exposes lease owners or server paths."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def _iso(value: Any) -> Any:
    return value.isoformat() if isinstance(value, datetime) else value


def _error(row: dict) -> dict | None:
    err = row.get("error")
    if not err:
        return None
    out = {"code": str(err.get("code", "ERROR")), "message": str(err.get("message", ""))}
    if err.get("details"):
        out["details"] = err["details"]
    return out


def admin_job(row: dict) -> dict[str, Any]:
    job_id = str(row["job_id"])
    return {
        "job_id": job_id,
        "kind": row["kind"],
        "status": row["status"],
        "progress": row.get("progress"),
        "progress_message": row.get("progress_message"),
        "dataset_id": row.get("dataset_id"),
        "version": row.get("dataset_version"),
        "upload_id": str(row["upload_id"]) if row.get("upload_id") else None,
        "attempts": row.get("attempts", 0),
        "result": row.get("result"),
        "error": _error(row),
        "created_at": _iso(row["created_at"]),
        "updated_at": _iso(row["updated_at"]),
        "started_at": _iso(row.get("started_at")),
        "finished_at": _iso(row.get("finished_at")),
        "links": {"self": f"/admin/jobs/{job_id}"},
    }


def export_job(row: dict) -> dict[str, Any]:
    job_id = str(row["job_id"])
    status = row["status"]
    links = {"self": f"/api/exports/{job_id}"}
    result = dict(row["result"]) if row.get("result") else None
    expires_at = row.get("expires_at")
    if status == "completed":
        links["download"] = f"/api/exports/{job_id}/download"
        links["metadata"] = f"/api/exports/{job_id}/metadata"
        if result is not None:
            result["download_url"] = links["download"]
            result["metadata_url"] = links["metadata"]
            result["expires_at"] = _iso(expires_at)
    if status in ("queued", "running"):
        links["cancel"] = f"/api/exports/{job_id}/cancel"
    if status == "expired" and result is not None:
        result = {**result, "expired": True}
    return {
        "job_id": job_id,
        "kind": "export",
        "status": status,
        "progress": row.get("progress"),
        "progress_message": row.get("progress_message"),
        "input": row.get("input") or {},
        "result": result,
        "error": _error(row),
        "attempts": row.get("attempts", 0),
        "owner": str(row["owner_id"]) if row.get("owner_id") else None,
        "created_at": _iso(row["created_at"]),
        "updated_at": _iso(row["updated_at"]),
        "started_at": _iso(row.get("started_at")),
        "finished_at": _iso(row.get("finished_at")),
        "expires_at": _iso(expires_at),
        "links": links,
    }


def is_expired(row: dict) -> bool:
    exp = row.get("expires_at")
    return row["status"] == "expired" or (exp is not None and exp <= datetime.now(timezone.utc))
