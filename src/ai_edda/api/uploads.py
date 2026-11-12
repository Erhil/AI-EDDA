import asyncio
from urllib.parse import unquote
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from starlette.requests import ClientDisconnect

from ..archive_handlers import _project_context
from ..asset_catalog import _source
from ..audio_jobs import reserve_audio
from ..cover_catalog import resolve_cover
from ..cover_jobs import reserve_cover
from ..import_receipts import ImportReceipt, build_import_receipt
from ..jobs import BusyError
from ..project_imports import _receipt

MAX_SIZE = 1024**3
WRITE_SIZE = 1024**2


def create_upload_router(service, auth, leases, coordinator):
    router = APIRouter(prefix="/api")

    def authenticate(request):
        token = request.cookies.get("ai_edda_session")
        if not token or not auth.authenticate(token):
            raise HTTPException(401, "Authentication required")

    def lease(request, project_id):
        with service._lock:
            try:
                service.get(project_id)
            except (FileNotFoundError, KeyError):
                raise HTTPException(404, "Project not found")
            except ValueError:
                raise HTTPException(422, "Invalid request")
            except (OSError, RuntimeError):
                raise HTTPException(503, "Storage unavailable")
            if not leases.require(
                project_id, request.headers.get("X-Tab-Id"), request.headers.get("X-Writer-Lease")
            ):
                raise HTTPException(409, "Writer lease required")

    def error(exc):
        if isinstance(exc, BusyError):
            return HTTPException(409, "Coordinator busy")
        if isinstance(exc, (FileNotFoundError, KeyError)):
            return HTTPException(404, "Not found")
        if isinstance(exc, ValueError):
            return HTTPException(
                409 if str(exc) == "REQUEST_ID_CONFLICT" else 422, "Invalid request"
            )
        return HTTPException(503, "Storage unavailable")

    async def upload(project_id, request, reserve, max_size):
        authenticate(request)
        lease(request, project_id)
        try:
            if (
                request.headers.get("content-type", "").split(";")[0].strip().lower()
                != "application/octet-stream"
            ):
                raise ValueError("content type")
            request_id = str(UUID(request.headers.get("X-Request-Id", "")))
            name = unquote(request.headers.get("X-File-Name", ""), errors="strict")
            size = int(request.headers.get("X-File-Size", ""))
            if size > max_size:
                raise HTTPException(413, "Upload too large")
            reservation = reserve(coordinator, service.store, project_id, request_id, name, size)
        except HTTPException:
            raise
        except (ValueError, KeyError, OSError, RuntimeError, BusyError) as exc:
            raise error(exc)
        job_id = reservation["job_id"]
        if not reservation["upload_required"]:
            return reservation
        started = False
        try:
            with coordinator.writer(job_id) as workspace:
                with (workspace / "upload.bin").open("wb") as output:
                    stream = request.stream().__aiter__()
                    total = 0
                    while True:
                        try:
                            chunk = await asyncio.wait_for(stream.__anext__(), timeout=60)
                        except StopAsyncIteration:
                            break
                        total += len(chunk)
                        if total > max_size:
                            raise HTTPException(413, "Upload too large")
                        if total > size:
                            raise HTTPException(422, "File size mismatch")
                        for offset in range(0, len(chunk), WRITE_SIZE):
                            output.write(chunk[offset : offset + WRITE_SIZE])
                    if total != size:
                        raise HTTPException(422, "File size mismatch")
            coordinator.start(job_id, {"workspace": str(workspace), "upload": "upload.bin"})
            started = True
            return reservation
        except asyncio.TimeoutError:
            raise HTTPException(408, "Upload timed out")
        except ClientDisconnect:
            raise HTTPException(400, "Upload disconnected")
        except HTTPException:
            raise
        except (ValueError, KeyError, OSError, RuntimeError, BusyError) as exc:
            raise error(exc)
        finally:
            if not started:
                coordinator.cancel(job_id)

    @router.post("/projects/{project_id}/audio-imports", status_code=202)
    async def upload_audio(project_id: str, request: Request):
        return await upload(project_id, request, reserve_audio, MAX_SIZE)

    @router.post("/projects/{project_id}/cover-imports", status_code=202)
    async def upload_cover(project_id: str, request: Request):
        return await upload(project_id, request, reserve_cover, 50 * 1024**2)

    @router.get("/projects/{project_id}/covers/{asset_id}")
    def get_cover(project_id: str, asset_id: str, request: Request):
        authenticate(request)
        try:
            with service._lock:
                service.get(project_id)
                root = service.store._dir(project_id)
            cover = resolve_cover(root, asset_id)
            return Response(_source(root, cover.path).read_bytes(), media_type="image/jpeg")
        except (ValueError, KeyError, OSError, RuntimeError) as exc:
            raise error(exc)

    @router.get("/jobs/{job_id}")
    def get_job(job_id: str, request: Request):
        authenticate(request)
        try:
            return coordinator.get(job_id)
        except (ValueError, KeyError, OSError, RuntimeError) as exc:
            raise error(exc)

    @router.post("/jobs/{job_id}/cancel")
    def cancel_job(job_id: str, request: Request):
        authenticate(request)
        try:
            job = coordinator.get(job_id)
            if job.get("kind") == "project_import":
                context = _project_context(job["context"])
                build_import_receipt(
                    context["request"],
                    "validation",
                    "0" * 64,
                    "00000000-0000-0000-0000-000000000000",
                    0,
                    "0" * 64,
                    "0" * 64,
                )
                if "prior_receipt" in context:
                    receipt = ImportReceipt.model_validate(context["prior_receipt"], strict=True)
                    _receipt(context, {"receipt": receipt.model_dump(mode="json")}, job_id)
            else:
                lease(request, job["context"]["project_id"])
            return coordinator.cancel(job_id)
        except HTTPException:
            raise
        except (ValueError, KeyError, OSError, RuntimeError, BusyError) as exc:
            raise error(exc)

    return router
