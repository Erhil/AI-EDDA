import zipfile
from uuid import uuid4

import pytest

from ai_edda.native_capture import capture_inventory, hash_inventory
from ai_edda.native_zip import read_native_zip, write_native_zip


def test_round_trip_and_pinned_head(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    (root / "project.json").write_bytes(b"old")
    (root / "grid-1.json").write_bytes(b"grid")
    inventory = capture_inventory(root, str(uuid4()), b"old")
    envelope = hash_inventory(inventory)
    (root / "project.json").write_bytes(b"new")
    archive = tmp_path / "backup.zip"
    write_native_zip(archive, inventory, envelope)
    result, project = read_native_zip(archive, tmp_path / "prepared")
    assert result == envelope
    assert (project / "project.json").read_bytes() == b"old"
    assert (project / "grid-1.json").read_bytes() == b"grid"


def test_rejects_changed_capture_and_preserves_existing_output(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    (root / "project.json").write_bytes(b"head")
    grid = root / "grid-1.json"
    grid.write_bytes(b"grid")
    inventory = capture_inventory(root, str(uuid4()), b"head")
    envelope = hash_inventory(inventory)
    grid.write_bytes(b"changed")
    output = tmp_path / "backup.zip"
    with pytest.raises(ValueError):
        write_native_zip(output, inventory, envelope)
    assert not output.exists()
    output.write_bytes(b"owned by caller")
    with pytest.raises(FileExistsError):
        write_native_zip(output, inventory, envelope)
    assert output.read_bytes() == b"owned by caller"


def test_rejects_extra_file_and_cleans_destination(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    (root / "project.json").write_bytes(b"head")
    inventory = capture_inventory(root, str(uuid4()), b"head")
    archive = tmp_path / "backup.zip"
    write_native_zip(archive, inventory, hash_inventory(inventory))
    with zipfile.ZipFile(archive, "a") as stream:
        stream.writestr("project/extra", b"extra")
    destination = tmp_path / "prepared"
    with pytest.raises(ValueError):
        read_native_zip(archive, destination)
    assert not destination.exists()
