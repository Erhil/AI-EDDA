from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

from ..asset_catalog import _safe
from ..export_jobs import ExportRequest, reserve_project_export
from ..game_export_catalog import resolve_game_export
from ..jobs import BusyError
from ..native_export_catalog import resolve_native_export
from ..project_store import _id


def create_export_router(service, auth, leases, coordinator, config_root):
    router = APIRouter(prefix="/api")

    def authenticate(request):
        token = request.cookies.get("ai_edda_session")
        if not token or not auth.authenticate(token):
            raise HTTPException(401, "Authentication required")

    def ids(project_id, export_id=None):
        for value in (project_id, export_id):
            if value is not None and _id(value) != value:
                raise ValueError("Invalid ID")

    def present(path):
        try:
            path.lstat()
            return True
        except FileNotFoundError:
            return False

    def lookup(project_id, export_id):
        try:
            ids(project_id, export_id)
        except ValueError:
            raise HTTPException(422, "Invalid ID")
        try:
            project_dir = service.store._dir(project_id)
            game = project_dir / "exports" / export_id
            native = Path(config_root) / "exports" / project_id / export_id
            _safe(game.parent)
            _safe(native.parent)
            has_game, has_native = present(game), present(native)
            if has_game and has_native:
                raise HTTPException(503, "Ambiguous export")
            if not has_game and not has_native:
                raise HTTPException(404, "Not found")
            if has_game:
                record = resolve_game_export(project_dir, export_id)
                if record["project_id"] != project_id:
                    raise ValueError("Project mismatch")
                name = record["folder_name"]
                folder = str((game / name).resolve())
                archive = game / (name + ".zip")
            else:
                record = resolve_native_export(config_root, project_id, export_id)
                folder = None
                archive = native / "backup.zip"
            public = record["public_result"]
            if public["project_id"] != project_id or public["export_id"] != export_id:
                raise ValueError("Identity mismatch")
            url = f"/api/projects/{project_id}/exports/{export_id}/download"
            return {
                **public,
                "publication": record,
                "download_url": url,
                "server_folder": folder,
            }, archive
        except HTTPException:
            raise
        except (ValueError, KeyError, OSError, RuntimeError):
            raise HTTPException(503, "Export unavailable")

    @router.post("/projects/{project_id}/exports", status_code=202)
    def create(project_id: str, body: ExportRequest, request: Request):
        authenticate(request)
        try:
            ids(project_id)
            with coordinator.jobs.lock:
                with service._lock:
                    if not leases.require(
                        project_id,
                        request.headers.get("X-Tab-Id"),
                        request.headers.get("X-Writer-Lease"),
                    ):
                        raise HTTPException(409, "Writer lease required")
                    return reserve_project_export(coordinator, service, project_id, body)
        except BusyError:
            raise HTTPException(409, "Coordinator busy")
        except (FileNotFoundError, KeyError):
            raise HTTPException(404, "Not found")
        except ValueError as exc:
            raise HTTPException(
                409 if str(exc) in ("STALE_REVISION", "REQUEST_ID_CONFLICT") else 422, str(exc)
            )
        except (OSError, RuntimeError):
            raise HTTPException(503, "Storage unavailable")

    @router.get("/projects/{project_id}/exports/{export_id}")
    def inspect(project_id: str, export_id: str, request: Request):
        authenticate(request)
        return lookup(project_id, export_id)[0]

    @router.get("/projects/{project_id}/exports/{export_id}/download")
    def download(project_id: str, export_id: str, request: Request):
        authenticate(request)
        _, archive = lookup(project_id, export_id)
        return FileResponse(archive, media_type="application/zip", filename=archive.name)

    return router
