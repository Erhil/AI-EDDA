import asyncio
from urllib.parse import unquote
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from starlette.requests import ClientDisconnect

from ..archive_jobs import ImportInProgress, reserve_project_import, start_project_import
from ..jobs import BusyError

MAX_SIZE = 1024**3
WRITE_SIZE = 1024**2


def create_archive_router(service, auth, coordinator):
    router = APIRouter(prefix="/api")

    @router.post("/projects/imports", status_code=202)
    async def import_project(request: Request):
        token = request.cookies.get("ai_edda_session")
        if not token or not auth.authenticate(token):
            raise HTTPException(401, "Authentication required")
        try:
            if (
                request.headers.get("content-type", "").split(";")[0].strip().lower()
                != "application/octet-stream"
            ):
                raise ValueError("content type")
            raw_id = request.headers.get("X-Request-Id", "")
            request_id = str(UUID(raw_id))
            if raw_id != request_id:
                raise ValueError("request id")
            kind = request.headers.get("X-Archive-Kind", "")
            if kind not in ("native", "game"):
                raise ValueError("archive kind")
            encoded_name = request.headers.get("X-File-Name", "")
            if any(
                encoded_name[index] == "%"
                and (
                    index + 2 >= len(encoded_name)
                    or any(
                        char not in "0123456789abcdefABCDEF"
                        for char in encoded_name[index + 1 : index + 3]
                    )
                )
                for index in range(len(encoded_name))
            ):
                raise ValueError("file name encoding")
            name = unquote(encoded_name, errors="strict")
            size = int(request.headers.get("X-File-Size", ""))
            if size > MAX_SIZE:
                raise HTTPException(413, "Upload too large")
            if size < 1:
                raise ValueError("file size")
            length = request.headers.get("content-length")
            if length is not None:
                length = int(length)
                if length > MAX_SIZE:
                    raise HTTPException(413, "Upload too large")
                if length != size:
                    raise ValueError("file size mismatch")
            reservation = reserve_project_import(
                coordinator, service.store, request_id, kind, name, size
            )
        except ImportInProgress as exc:
            raise HTTPException(409, {"code": "IMPORT_IN_PROGRESS", "job_id": exc.job_id})
        except BusyError:
            raise HTTPException(409, "Coordinator busy")
        except ValueError as exc:
            if str(exc) == "REQUEST_ID_CONFLICT":
                raise HTTPException(409, {"code": "REQUEST_ID_CONFLICT"})
            raise HTTPException(422, "Invalid request")
        except (OSError, RuntimeError, KeyError):
            raise HTTPException(503, "Storage unavailable")
        job_id = reservation["job_id"]
        started = False
        try:
            with coordinator.writer(job_id) as workspace:
                with (workspace / "source.zip").open("wb") as output:
                    stream = request.stream().__aiter__()
                    total = 0
                    while True:
                        try:
                            chunk = await asyncio.wait_for(stream.__anext__(), timeout=60)
                        except StopAsyncIteration:
                            break
                        total += len(chunk)
                        if total > MAX_SIZE:
                            raise HTTPException(413, "Upload too large")
                        if total > size:
                            raise HTTPException(422, "File size mismatch")
                        for offset in range(0, len(chunk), WRITE_SIZE):
                            output.write(chunk[offset : offset + WRITE_SIZE])
                    if total != size:
                        raise HTTPException(422, "File size mismatch")
            start_project_import(coordinator, job_id)
            started = True
            return reservation
        except asyncio.TimeoutError:
            raise HTTPException(408, "Upload timed out")
        except ClientDisconnect:
            raise HTTPException(400, "Upload disconnected")
        except HTTPException:
            raise
        except (ValueError, KeyError, OSError, RuntimeError, BusyError):
            raise HTTPException(503, "Storage unavailable")
        finally:
            if not started:
                try:
                    coordinator.cancel(job_id)
                except (OSError, RuntimeError, KeyError):
                    raise HTTPException(503, "Storage unavailable")

    return router
