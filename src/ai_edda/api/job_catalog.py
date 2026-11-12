import heapq
import re
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request

from ..project_store import _id

TERMINAL = {"completed", "failed", "cancelled", "interrupted"}


def create_job_catalog_router(coordinator, auth):
    router = APIRouter(prefix="/api")

    @router.get("/jobs")
    def listing(
        request: Request,
        project_id: str | None = None,
        state: Literal["active", "terminal"] | None = None,
        after: str | None = None,
        limit: int = Query(20, ge=1, le=50),
    ):
        token = request.cookies.get("ai_edda_session")
        if not token or not auth.authenticate(token):
            raise HTTPException(401, "Authentication required")
        try:
            if project_id is not None and project_id != _id(project_id):
                raise ValueError()
            if after is not None and not re.fullmatch(r"[0-9a-f]{32}", after):
                raise ValueError()
        except ValueError:
            raise HTTPException(422, "Invalid request")

        def project(job):
            context = job.get("context")
            context = context if isinstance(context, dict) else {}
            result = job.get("publication_result")
            result = result if isinstance(result, dict) else {}
            value = context.get("project_id") or result.get("project_id")
            return value if isinstance(value, str) else None

        with coordinator.jobs.lock:
            jobs = coordinator.jobs.jobs
            ids = heapq.nsmallest(
                limit + 1,
                (
                    key
                    for key, job in jobs.items()
                    if (after is None or key > after)
                    and (project_id is None or project(job) == project_id)
                    and (state is None or (job.get("state") in TERMINAL) == (state == "terminal"))
                ),
            )
            selected = ids[:limit]
            items = []
            for key in selected:
                job = jobs[key]
                context = job.get("context") if isinstance(job.get("context"), dict) else {}
                req = context.get("request") if isinstance(context.get("request"), dict) else {}
                kind = job.get("kind") if isinstance(job.get("kind"), str) else "unknown"
                label = (
                    context.get("file_name")
                    or context.get("kind")
                    or req.get("generator_id")
                    or kind
                )
                if not isinstance(label, str):
                    label = kind
                request_id = context.get("request_id")
                items.append(
                    {
                        "id": key,
                        "kind": kind,
                        "state": job.get("state"),
                        "progress": job.get("progress")
                        if isinstance(job.get("progress"), (str, int, float, type(None)))
                        else None,
                        "project_id": project(job),
                        "request_id": request_id if isinstance(request_id, str) else None,
                        "label": label,
                        "has_publication_result": "publication_result" in job,
                        "coordinator_error": None,
                        "error": job.get("error") if isinstance(job.get("error"), str) else None,
                    }
                )
            with coordinator.lock:
                for item in items:
                    item["coordinator_error"] = coordinator._job_errors.get(item["id"])
        return {"items": items, "next_cursor": selected[-1] if len(ids) > limit else None}

    return router
