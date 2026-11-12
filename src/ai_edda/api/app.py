import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

from ..access import OwnerAuth, WriterLeases
from ..analysis_jobs import make_analysis_handlers
from ..application import ApplicationService
from ..archive_handlers import make_import_handlers
from ..audio_jobs import make_audio_handlers
from ..cover_jobs import make_cover_handlers
from ..export_jobs import make_export_handlers
from ..generation_jobs import make_generation_handlers
from ..project_store import ProjectStore
from ..work_coordinator import WorkCoordinator
from .analysis import create_analysis_router
from .archives import create_archive_router
from .audio import create_audio_router
from .boundaries import BoundaryMiddleware
from .catalogs import create_catalog_router
from .core import create_router
from .exports import create_export_router
from .generation import create_generation_router
from .grid import create_grid_router
from .job_catalog import create_job_catalog_router
from .targets import create_target_router
from .uploads import create_upload_router

STATIC = Path(__file__).resolve().parent.parent / "static"


def config_root_default():
    return Path(os.environ.get("LOCALAPPDATA", Path.home() / ".local" / "share")) / "AI-EDDA"


def create_app(project_root=None, config_root=None, allowed_hosts=None):
    config_root = Path(config_root) if config_root is not None else config_root_default()

    @asynccontextmanager
    async def lifespan(app):
        store = jobs = None
        try:
            config = config_root / "owner.json"
            if not config.is_file():
                raise RuntimeError("Owner configuration missing. Run ai-edda init first.")
            auth = OwnerAuth(config)
            store = ProjectStore(project_root)
            jobs = WorkCoordinator(
                config_root,
                {
                    **make_audio_handlers(store),
                    **make_cover_handlers(store),
                    **make_analysis_handlers(store),
                    **make_generation_handlers(store),
                    **make_import_handlers(store),
                    **make_export_handlers(store, config_root),
                },
            )
            app.state.service = ApplicationService(store)
            app.state.auth = auth
            app.state.leases = WriterLeases()
            app.state.coordinator = jobs
            app.state.jobs = jobs.jobs
            yield
        finally:
            try:
                if jobs is not None:
                    jobs.close()
            finally:
                if store is not None:
                    store.close()

    app = FastAPI(lifespan=lifespan)
    app.add_middleware(
        BoundaryMiddleware,
        allowed_hosts=allowed_hosts
        if allowed_hosts is not None
        else ["localhost", "127.0.0.1", "[::1]", "testserver"],
    )

    # The router resolves the active service after startup.
    class Proxy:
        def __init__(self, name):
            self.name = name

        def __getattr__(self, name):
            return getattr(getattr(app.state, self.name), name)

    app.include_router(create_router(Proxy("service"), Proxy("auth"), Proxy("leases")))
    app.include_router(create_catalog_router(Proxy("service"), Proxy("auth")))
    app.include_router(create_job_catalog_router(Proxy("coordinator"), Proxy("auth")))
    app.include_router(
        create_upload_router(Proxy("service"), Proxy("auth"), Proxy("leases"), Proxy("coordinator"))
    )
    app.include_router(create_archive_router(Proxy("service"), Proxy("auth"), Proxy("coordinator")))
    app.include_router(create_audio_router(Proxy("service"), Proxy("auth")))
    app.include_router(
        create_analysis_router(
            Proxy("service"),
            Proxy("auth"),
            Proxy("leases"),
            Proxy("coordinator"),
            config_root.resolve() / "models" / "beat-this" / "final0.ckpt",
        )
    )
    app.include_router(
        create_export_router(
            Proxy("service"), Proxy("auth"), Proxy("leases"), Proxy("coordinator"), config_root
        )
    )
    app.include_router(
        create_generation_router(
            Proxy("service"), Proxy("auth"), Proxy("leases"), Proxy("coordinator")
        )
    )
    app.include_router(create_grid_router(Proxy("service"), Proxy("auth")))
    app.include_router(create_target_router(Proxy("service"), Proxy("auth")))

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    @app.get("/{path:path}")
    def frontend(path: str):
        if path == "api" or path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not Found")
        index = STATIC / "index.html"
        if not index.is_file():
            raise HTTPException(
                status_code=503,
                detail="Frontend build missing. Run npm ci and npm run build in web.",
            )
        target = (STATIC / path).resolve()
        if not target.is_relative_to(STATIC.resolve()):
            raise HTTPException(status_code=404, detail="Not Found")
        if target.is_file():
            return FileResponse(target)
        if path and (Path(path).suffix or path.startswith("assets/")):
            raise HTTPException(status_code=404, detail="Not Found")
        return FileResponse(index)

    return app
