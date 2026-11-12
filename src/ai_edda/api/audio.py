import threading

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from ..asset_catalog import resolve_audio
from ..audio_view import build_waveform, read_pcm_fragment, read_waveform


def create_audio_router(service, auth):
    router = APIRouter(prefix="/api/projects/{project_id}/audio")
    waveform_lock = threading.Lock()

    def require_auth(request: Request):
        token = request.cookies.get("ai_edda_session")
        if not token or not auth.authenticate(token):
            raise HTTPException(401, "Authentication required")

    def capture(project_id, audio_hash):
        try:
            with service._lock:
                snapshot = service.store.load(project_id)
                asset = snapshot.project.audio
                root = service.store._dir(project_id)
            if asset is None:
                raise HTTPException(404, "Audio not found")
            if asset.sha256 != audio_hash:
                raise HTTPException(409, "Stale audio hash")
            parts = asset.path.split("/")
            if len(parts) != 3 or parts[0] != "audio" or parts[2] != "song.ogg":
                raise ValueError("Invalid audio path")
            if resolve_audio(root, parts[1]) != asset:
                raise ValueError("Audio identity mismatch")
            return asset, root
        except (FileNotFoundError, KeyError):
            raise HTTPException(404, "Project or audio not found")
        except ValueError:
            raise HTTPException(422, "Invalid audio")
        except (OSError, RuntimeError):
            raise HTTPException(503, "Storage unavailable")

    def run(fn):
        try:
            return fn()
        except ValueError:
            raise HTTPException(422, "Invalid audio request")
        except (OSError, RuntimeError, KeyError):
            raise HTTPException(503, "Audio unavailable")

    @router.get("/pcm")
    def pcm(
        project_id: str,
        audio_hash: str,
        start_sample: int = Query(ge=0),
        frame_count: int = Query(gt=0),
        token=Depends(require_auth),
    ):
        asset, root = capture(project_id, audio_hash)
        decoded_path = (root / asset.path).with_name("decoded.wav")
        result = run(
            lambda: read_pcm_fragment(
                asset, root, audio_hash, start_sample, frame_count, decoded_path=decoded_path
            )
        )
        return Response(
            result["pcm"],
            media_type="application/octet-stream",
            headers={
                "X-Audio-Hash": result["hash"],
                "X-Source-Start-Sample": str(result["source_start_sample"]),
                "X-Sample-Rate": str(result["sample_rate"]),
                "X-Channels": str(result["channels"]),
                "X-Frames": str(result["frames"]),
            },
        )

    @router.get("/waveform")
    def waveform(project_id: str, audio_hash: str, token=Depends(require_auth)):
        asset, root = capture(project_id, audio_hash)
        with waveform_lock:
            return run(lambda: build_waveform(asset, root, audio_hash))

    @router.get("/waveform/peaks")
    def peaks(
        project_id: str,
        audio_hash: str,
        level: int = Query(ge=0),
        start: int = Query(ge=0),
        count: int = Query(gt=0, le=16384),
        token=Depends(require_auth),
    ):
        asset, root = capture(project_id, audio_hash)
        with waveform_lock:
            run(lambda: build_waveform(asset, root, audio_hash))
            result = run(lambda: read_waveform(asset, root, audio_hash, level, start, count))
        result["peaks"] = result["peaks"].tolist()
        return result

    return router
