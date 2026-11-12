import hashlib
from uuid import uuid4

import pytest

from ai_edda.native_capture import capture_inventory, hash_inventory


def test_pinned_head_and_files(tmp_path):
    head = b"old"
    (tmp_path / "project.json").write_bytes(head)
    (tmp_path / "grid-1.json").write_bytes(b"grid")
    target = tmp_path / "targets"
    target.mkdir()
    (target / f"{uuid4()}-1.json").write_bytes(b"target")
    cache = tmp_path / "waveforms" / ("a" * 64)
    cache.mkdir(parents=True)
    (cache / "peaks.npz").write_bytes(b"cache")
    inventory = capture_inventory(tmp_path, str(uuid4()), head)
    (tmp_path / "project.json").write_bytes(b"new")
    result = hash_inventory(inventory)
    assert len(result.files) == 3
    assert (
        next(x for x in result.files if x.path == "project.json").sha256
        == hashlib.sha256(head).hexdigest()
    )


def test_changed_and_unknown(tmp_path):
    (tmp_path / "project.json").write_bytes(b"h")
    path = tmp_path / "grid-1.json"
    path.write_bytes(b"a")
    inventory = capture_inventory(tmp_path, str(uuid4()), b"h")
    path.write_bytes(b"changed")
    with pytest.raises(ValueError):
        hash_inventory(inventory)
    (tmp_path / "unknown").write_bytes(b"x")
    with pytest.raises(ValueError):
        capture_inventory(tmp_path, str(uuid4()), b"h")
