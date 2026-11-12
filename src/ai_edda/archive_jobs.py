"""Admission and startup for project archive imports."""

import hashlib

from .import_receipts import build_import_receipt, resolve_import_receipt
from .project_imports import _receipt
from .project_store import _json


class ImportInProgress(Exception):
    def __init__(self, job_id):
        self.job_id = job_id
        super().__init__(job_id)


def reserve_project_import(coordinator, store, request_id, archive_kind, file_name, file_size):
    request = dict(
        request_id=request_id, archive_kind=archive_kind, file_name=file_name, file_size=file_size
    )
    build_import_receipt(
        request,
        "validation",
        "0" * 64,
        "00000000-0000-0000-0000-000000000000",
        0,
        "0" * 64,
        "0" * 64,
    )
    prior = None
    with coordinator.jobs.lock:
        matching = []
        for job in coordinator.jobs.jobs.values():
            old = job.get("context") or {}
            if old.get("request_id") != request_id:
                continue
            if job["kind"] != "project_import" or _json(
                {
                    k: old[k]
                    for k in ("request_id", "archive_kind", "file_name", "file_size")
                    if k in old
                }
            ) != _json(request):
                raise ValueError("REQUEST_ID_CONFLICT")
            if set(old) - {"request_id", "archive_kind", "file_name", "file_size", "prior_receipt"}:
                raise ValueError("REQUEST_ID_CONFLICT")
            matching.append(job)
        for job in matching:
            if job["state"] not in ("completed", "failed", "cancelled", "interrupted") or (
                job["state"] == "interrupted" and "result" in job
            ):
                raise ImportInProgress(job["id"])
        for job in matching:
            if job["state"] != "completed":
                continue
            try:
                result = job.get("result")
                raw = result["receipt"]
            except (KeyError, TypeError):
                raise ValueError("Invalid local import receipt") from None
            helper_context = {"request": request}
            if "prior_receipt" in job["context"]:
                helper_context["prior_receipt"] = job["context"]["prior_receipt"]
            receipt, had_prior = _receipt(helper_context, result, job["id"])
            job["context"].get("prior_receipt")
            bound_request = dict(request)
            if archive_kind != receipt.public_result.archive_kind:
                raise ValueError("Invalid local import receipt")
            if (
                receipt.request_id != request_id
                or receipt.request_sha256 != hashlib.sha256(_json(bound_request)).hexdigest()
                or (receipt.job_id != job["id"] and not had_prior)
                or (receipt.job_id == job["id"] and had_prior)
                or _json(job.get("publication_result"))
                != _json(receipt.public_result.model_dump(mode="json"))
                or resolve_import_receipt(store._dir(receipt.project_id), request_id) != receipt
            ):
                raise ValueError("Invalid local import receipt")
            if prior is not None and prior != raw:
                raise ValueError("Invalid local import receipt")
            prior = raw
        context = dict(request)
        if prior is not None:
            context["prior_receipt"] = prior
        job_id = coordinator.reserve("project_import", context)
        return {"job_id": job_id, "upload_required": True, "verifying": prior is not None}


def start_project_import(coordinator, job_id):
    job = coordinator.jobs.get(job_id)
    if job.get("kind") != "project_import":
        raise ValueError("Job is not a project import")
    context = job.get("context")
    if not isinstance(context, dict) or not all(
        k in context for k in ("request_id", "archive_kind", "file_name", "file_size")
    ):
        raise ValueError("Invalid project import reservation")
    if job.get("state") != "reserved":
        raise ValueError("Project import is not reserved")
    try:
        with coordinator.writer(job_id) as workspace:
            payload = {
                "workspace": str(workspace.resolve()),
                "job_id": job_id,
                "request_context": {
                    k: context[k] for k in ("request_id", "archive_kind", "file_name", "file_size")
                },
            }
            if "prior_receipt" in context:
                payload["prior_receipt"] = context["prior_receipt"]
        coordinator.start(job_id, payload)
    except BaseException:
        coordinator.cancel(job_id)
        raise
