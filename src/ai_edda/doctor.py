"""Inspect local readiness without changing files or downloading assets."""

import hashlib
import importlib.metadata as metadata
import json
import platform
import shutil
from pathlib import Path

from .model_download import _SHA256, _SIZE


def inspect(project_root, config_root, static, device="cpu"):
    project_root, config_root = Path(project_root).resolve(), Path(config_root).resolve()
    errors, warnings = [], []
    versions = {}
    for name in (
        "ai-edda",
        "fastapi",
        "uvicorn",
        "typer",
        "soundfile",
        "numpy",
        "beat-this",
        "torch",
        "torchaudio",
        "soxr",
        "psutil",
        "Pillow",
        "pydantic",
        "cryptography",
    ):
        try:
            versions[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            versions[name] = None
    for name in (
        "fastapi",
        "uvicorn",
        "typer",
        "soundfile",
        "psutil",
        "numpy",
        "Pillow",
        "pydantic",
        "cryptography",
    ):
        if versions[name] is None:
            errors.append(f"Missing required dependency: {name}.")
    for name in ("beat-this", "torch", "torchaudio", "soxr"):
        if versions[name] is None:
            warnings.append(f"Analysis dependency missing: {name}.")
    frontend = (Path(static) / "index.html").is_file()
    owner_path = config_root / "owner.json"
    owner = owner_path.is_file()
    if project_root.exists() and not project_root.is_dir():
        errors.append("Project root is not a directory. Select a valid project directory.")
    if not frontend:
        errors.append("Frontend is missing. Reinstall the application with its static files.")
    if not owner:
        errors.append("Owner configuration is missing. Run ai-edda init.")
    else:
        try:
            config = json.loads(owner_path.read_text(encoding="utf-8"))
            salt, digest = config.get("salt"), config.get("hash")
            if (
                not isinstance(config, dict)
                or not isinstance(salt, str)
                or not isinstance(digest, str)
            ):
                raise ValueError
            if len(bytes.fromhex(salt)) != 16 or len(bytes.fromhex(digest)) != 64:
                raise ValueError
        except (OSError, UnicodeError, ValueError, TypeError, AttributeError):
            errors.append("Owner configuration is invalid. Run ai-edda init.")
    disks = {}
    for label, path in (("project", project_root), ("config", config_root)):
        existing = path
        while not existing.exists() and existing.parent != existing:
            existing = existing.parent
        try:
            disks[label] = shutil.disk_usage(existing).free
        except OSError:
            disks[label] = None
            warnings.append(f"Cannot inspect free disk space for {label}.")
    try:
        import psutil

        ram = psutil.virtual_memory().available
    except (ImportError, OSError):
        ram = None
        warnings.append("Cannot inspect available RAM.")
    try:
        import soundfile

        codecs = soundfile.available_formats()
    except (ImportError, OSError, RuntimeError) as exc:
        codecs = {}
        errors.append(f"Cannot inspect audio codecs: {exc}")
    checkpoint = config_root / "models" / "beat-this" / "final0.ckpt"
    model = {
        "path": str(checkpoint),
        "status": "missing",
        "size": None,
        "sha256": None,
        "expected_size": _SIZE,
        "expected_sha256": _SHA256,
    }
    if checkpoint.is_file():
        try:
            digest = hashlib.sha256()
            with checkpoint.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
            model.update(size=checkpoint.stat().st_size, sha256=digest.hexdigest())
            model["status"] = (
                "valid" if model["size"] == _SIZE and model["sha256"] == _SHA256 else "invalid"
            )
        except OSError as exc:
            model["status"] = "unreadable"
            warnings.append(f"Cannot read checkpoint: {exc}")
    if model["status"] != "valid":
        guidance = "Checkpoint is missing or invalid. Run ai-edda download-model."
        if not errors:
            guidance += " The manual editor remains available."
        warnings.append(guidance)
    hardware = {
        "requested_device": device,
        "torch_version": versions["torch"],
        "cuda_available": None,
        "device_memory_bytes": None,
    }
    if device == "cuda":
        try:
            import torch

            hardware["cuda_available"] = bool(torch.cuda.is_available())
            if hardware["cuda_available"]:
                hardware["device_memory_bytes"] = torch.cuda.get_device_properties(0).total_memory
            else:
                errors.append(
                    "CUDA was requested but is unavailable. Select --device cpu or fix the CUDA installation."
                )
        except (ImportError, RuntimeError, OSError) as exc:
            errors.append(f"CUDA was requested but could not be inspected: {exc}")
    return {
        "schema_version": 1,
        "ok": not errors,
        "paths": {"project_root": str(project_root), "config_root": str(config_root)},
        "python": platform.python_version(),
        "dependencies": versions,
        "free_disk_bytes": disks,
        "available_ram_bytes": ram,
        "frontend_present": frontend,
        "owner_config_present": owner,
        "audio_formats": codecs,
        "checkpoint": model,
        "hardware": hardware,
        "errors": errors,
        "warnings": warnings,
    }
