"""Download the pinned Beat This checkpoint without replacing existing files."""

import hashlib
import os
import tempfile
from pathlib import Path
from urllib.request import urlopen

from .asset_catalog import _safe

_URL = "https://cloud.cp.jku.at/public.php/dav/files/7ik4RrBKTS273gp/final0.ckpt"
_SIZE = 81058141
_SHA256 = "8c328b45f59d8dd3dff219253ff6a8d6482be57d0133a29140e2febbf8eb8331"
_CHUNK = 1024 * 1024


def _verify(path):
    path = _safe(path)
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while chunk := stream.read(_CHUNK):
            size += len(chunk)
            if size > _SIZE:
                raise ValueError("Invalid checkpoint size")
            digest.update(chunk)
    if size != _SIZE or digest.hexdigest() != _SHA256:
        raise ValueError("Invalid checkpoint size or SHA256")


def ensure_model(config_root, progress=None, cancel=None):
    """Return (checkpoint path, status), downloading the pinned file if absent.

    progress receives (bytes received, total bytes); cancel returns a boolean.
    """
    directory = _safe(Path(config_root) / "models" / "beat-this")
    final = _safe(directory / "final0.ckpt")
    if final.exists():
        _verify(final)
        return final, "existing"
    directory.mkdir(parents=True, exist_ok=True)
    _safe(directory)
    name = None
    try:
        if cancel and cancel():
            raise InterruptedError("Checkpoint download cancelled")
        with tempfile.NamedTemporaryFile(
            dir=directory, prefix=".final0-", suffix=".tmp", delete=False
        ) as out:
            name = out.name
            digest = hashlib.sha256()
            size = 0
            with urlopen(_URL, timeout=30) as response:
                while True:
                    if cancel and cancel():
                        raise InterruptedError("Checkpoint download cancelled")
                    chunk = response.read(min(_CHUNK, _SIZE - size + 1))
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > _SIZE:
                        raise ValueError("Checkpoint download exceeds expected size")
                    digest.update(chunk)
                    out.write(chunk)
                    if progress:
                        progress(size, _SIZE)
            if size != _SIZE or digest.hexdigest() != _SHA256:
                raise ValueError("Invalid checkpoint size or SHA256")
            out.flush()
            os.fsync(out.fileno())
        _safe(final)
        try:
            os.link(name, final)
        except FileExistsError:
            _verify(final)
            return final, "existing"
        return final, "downloaded"
    finally:
        if name is not None:
            os.unlink(name)
