import stat
import zipfile

import pytest

from ai_edda.safe_zip import extract_zip, validate_member_paths


def make_zip(path, entries):
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in entries:
            archive.writestr(zipfile.ZipInfo(name) if isinstance(name, str) else name, data)


def test_valid_and_existing_destination(tmp_path):
    source = tmp_path / "a.zip"
    make_zip(source, [("dir/file.txt", b"hello")])
    dest = tmp_path / "out"
    assert extract_zip(source, dest) == ["dir/file.txt"]
    assert (dest / "dir/file.txt").read_bytes() == b"hello"
    with pytest.raises(FileExistsError):
        extract_zip(source, dest)
    assert (dest / "dir/file.txt").read_bytes() == b"hello"


@pytest.mark.parametrize(
    "names",
    [["../x"], ["/x"], ["C:/x"], ["a\\b"], ["A", "a"], ["a", "a"], ["a", "a/b"], ["CON.txt"]],
)
def test_bad_paths(tmp_path, names):
    source = tmp_path / "a.zip"
    make_zip(source, [(name, b"x") for name in names])
    if names == ["a\\b"]:
        source.write_bytes(source.read_bytes().replace(b"a/b", b"a\\b"))
    with pytest.raises(ValueError):
        extract_zip(source, tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_symlink(tmp_path):
    info = zipfile.ZipInfo("link")
    info.create_system = 3
    info.external_attr = (stat.S_IFLNK | 0o777) << 16
    source = tmp_path / "a.zip"
    make_zip(source, [(info, b"target")])
    with pytest.raises(ValueError):
        extract_zip(source, tmp_path / "out")


def test_size_and_crc_cleanup(tmp_path):
    source = tmp_path / "a.zip"
    make_zip(source, [("x", b"hello")])
    with pytest.raises(ValueError):
        extract_zip(source, tmp_path / "out", max_bytes=2)
    assert not (tmp_path / "out").exists()
    data = source.read_bytes().replace(b"hello", b"jello")
    source.write_bytes(data)
    with pytest.raises(zipfile.BadZipFile):
        extract_zip(source, tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_validate_member_paths():
    assert validate_member_paths([("dir/file", False), ("dir/", True)]) == [
        ["dir", "file"],
        ["dir"],
    ]
    for entries in [
        [("a/", True), ("a/", True)],
        [("a", False), ("a/b", False)],
        [("A/x", False), ("a/y", False)],
        [("x.", False)],
        [("NUL.txt", False)],
        [("", False)],
        [("a\\b", False)],
    ]:
        with pytest.raises(ValueError):
            validate_member_paths(entries)
