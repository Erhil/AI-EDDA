import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path

from .analysis_bundle import load_analysis_bundle
from .arrangement_domain import FeatureManifestRef, RhythmAnalysis
from .asset_catalog import _digest, _safe, _source, resolve_audio
from .domain import AudioAsset
from .project_store import _id, _json


def _hash(value):
    return hashlib.sha256(_json(value)).hexdigest()


def _receipt(root):
    value = json.loads(_safe(root / "publication.json").read_text(encoding="utf-8"))
    if (
        not isinstance(value, dict)
        or set(value) != {"job_id", "context_sha256", "result_sha256", "artifacts", "public_result"}
        or not isinstance(value["job_id"], str)
        or not value["job_id"]
        or any(
            not isinstance(value[k], str)
            or len(value[k]) != 64
            or any(c not in "0123456789abcdef" for c in value[k])
            for k in ("context_sha256", "result_sha256")
        )
        or not isinstance(value["artifacts"], dict)
        or not isinstance(value["public_result"], dict)
    ):
        raise ValueError("Invalid publication receipt")
    return value


def resolve_analysis(project_dir, analysis_id):
    analysis_id = _id(analysis_id)
    project = _safe(project_dir)
    root = _safe(project / "analysis" / analysis_id)
    receipt = _receipt(root)
    required = {
        "analysis.json",
        "descriptor.json",
        "beat/manifest.json",
        "beat/beats.npz",
        "rhythm/manifest.json",
        "rhythm/features.npz",
    }
    if set(receipt["artifacts"]) != required:
        raise ValueError("Invalid analysis artifact set")
    for path, digest in receipt["artifacts"].items():
        if _digest(_source(root, path)) != digest:
            raise ValueError("Analysis artifact hash mismatch")
    analysis = RhythmAnalysis.model_validate_json(_safe(root / "analysis.json").read_bytes())
    descriptor = json.loads(_safe(root / "descriptor.json").read_text(encoding="utf-8"))
    if type(receipt["public_result"].get("revision")) is not int or _json(
        receipt["public_result"]
    ) != _json(analysis.model_dump(mode="json")):
        raise ValueError("Invalid publication result")
    for role, filename in (("beat", "beats.npz"), ("rhythm", "features.npz")):
        if (
            descriptor["roles"][role]["manifest_path"] != f"{role}/manifest.json"
            or descriptor["roles"][role]["npz_path"] != f"{role}/{filename}"
        ):
            raise ValueError("Invalid role paths")
    if analysis.id != analysis_id or type(analysis.revision) is not int or analysis.revision != 0:
        raise ValueError("Invalid analysis identity")
    audio = resolve_audio(project, analysis.audio_asset_id)
    if audio != analysis.audio:
        raise ValueError("Audio mismatch")
    expected = tuple(
        FeatureManifestRef(
            path=f"analysis/{analysis_id}/{role}/manifest.json",
            sha256=descriptor["roles"][role]["manifest_sha256"],
            version="1",
        )
        for role in ("beat", "rhythm")
    )
    if analysis.manifests != expected:
        raise ValueError("Invalid manifest mapping")
    arrays = load_analysis_bundle(root, descriptor, audio, analysis.audio_asset_id)
    return analysis, arrays


def reconcile_analysis(project_dir, analysis_id, job_id, context, result):
    root = _safe(Path(project_dir) / "analysis" / _id(analysis_id))
    if not root.exists():
        return False, None
    receipt = _receipt(root)
    if (receipt["job_id"], receipt["context_sha256"], receipt["result_sha256"]) != (
        str(job_id),
        _hash(context),
        _hash(result),
    ):
        raise ValueError("Analysis publication conflict")
    analysis, _ = resolve_analysis(project_dir, analysis_id)
    audio = AudioAsset.model_validate(context["audio"])
    if context["audio_asset_id"] != analysis.audio_asset_id or audio != analysis.audio:
        raise ValueError("Analysis publication conflict")
    persisted = json.loads(_safe(root / "descriptor.json").read_text(encoding="utf-8"))
    expected = json.loads(_json(result))
    for role in ("beat", "rhythm"):
        expected["roles"][role]["manifest_path"] = f"{role}/manifest.json"
        expected["roles"][role]["npz_path"] = (
            f"{role}/{'beats.npz' if role == 'beat' else 'features.npz'}"
        )
    if _json(persisted) != _json(expected):
        raise ValueError("Analysis publication conflict")
    return True, receipt["public_result"]


def publish_analysis(project_dir, analysis_id, job_id, context, result, workspace):
    analysis_id = _id(analysis_id)
    found, public = reconcile_analysis(project_dir, analysis_id, job_id, context, result)
    if found:
        return public
    project = _safe(project_dir)
    audio = AudioAsset.model_validate(context["audio"])
    asset_id = context["audio_asset_id"]
    if resolve_audio(project, asset_id) != audio:
        raise ValueError("Audio mismatch")
    load_analysis_bundle(workspace, result, audio, asset_id)
    catalog = _safe(project / "analysis")
    catalog.mkdir(exist_ok=True)
    temp = Path(tempfile.mkdtemp(prefix=".staging-", dir=catalog))
    try:
        descriptor = json.loads(_json(result))
        refs = []
        for role in ("beat", "rhythm"):
            (temp / role).mkdir()
            record = descriptor["roles"][role]
            for kind, filename in (
                ("manifest", "manifest.json"),
                ("npz", "beats.npz" if role == "beat" else "features.npz"),
            ):
                shutil.copyfile(
                    _source(_safe(workspace), record[kind + "_path"]), temp / role / filename
                )
                record[kind + "_path"] = f"{role}/{filename}"
            refs.append(
                FeatureManifestRef(
                    path=f"analysis/{analysis_id}/{role}/manifest.json",
                    sha256=record["manifest_sha256"],
                    version="1",
                )
            )
        load_analysis_bundle(temp, descriptor, audio, asset_id)
        analysis = RhythmAnalysis(
            id=analysis_id, revision=0, audio_asset_id=asset_id, audio=audio, manifests=tuple(refs)
        )
        public = analysis.model_dump(mode="json")
        (temp / "analysis.json").write_bytes(_json(public))
        (temp / "descriptor.json").write_bytes(_json(descriptor))
        artifacts = {
            p.relative_to(temp).as_posix(): _digest(p) for p in temp.rglob("*") if p.is_file()
        }
        receipt = {
            "job_id": str(job_id),
            "context_sha256": _hash(context),
            "result_sha256": _hash(result),
            "artifacts": artifacts,
            "public_result": public,
        }
        (temp / "publication.json").write_bytes(_json(receipt))
        for p in temp.rglob("*"):
            if p.is_file():
                with p.open("r+b") as f:
                    os.fsync(f.fileno())
        try:
            os.rename(temp, catalog / analysis_id)
        except OSError:
            found, public = reconcile_analysis(project, analysis_id, job_id, context, result)
            if found:
                return public
            raise
        return public
    finally:
        shutil.rmtree(temp, ignore_errors=True)
