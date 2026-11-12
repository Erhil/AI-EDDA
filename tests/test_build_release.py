import hashlib
import importlib.util
import json
import zipfile
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "build_release", Path(__file__).resolve().parents[1] / "scripts/build_release.py"
)
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


def write(root, name, data):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data, encoding="utf-8")


def test_release_inventory_and_reproducibility(tmp_path):
    root = tmp_path / "repo"
    write(root, "pyproject.toml", '[project]\nversion = "1.2.3"\n')
    write(root, "src/ai_edda/static/index.html", "ready")
    write(root, "src/ai_edda/app.py", "source")
    write(root, "src/ai_edda/__pycache__/secret.py", "excluded")
    write(root, "web/src/app.ts", "web")
    write(root, "web/node_modules/pkg/index.js", "excluded")
    write(root, "fixtures/example.json", "{}")
    write(root, "projects/private.json", "{}")
    archive = release.assemble(root, tmp_path / "out")
    first = archive.read_bytes()
    assert release.assemble(root, tmp_path / "out").read_bytes() == first
    with zipfile.ZipFile(archive) as z:
        names = z.namelist()
        assert names == sorted(names)
        assert "src/ai_edda/static/index.html" in names
        assert "web/src/app.ts" in names
        assert not any("secret" in n or "node_modules" in n or "projects" in n for n in names)
    manifest = json.loads((archive.parent / "ai-edda-1.2.3-manifest.json").read_text())
    assert manifest["archive"]["sha256"] == hashlib.sha256(first).hexdigest()
    assert manifest["files"]["web/src/app.ts"] == hashlib.sha256(b"web").hexdigest()


def test_release_includes_windows_install_instructions_and_hash(tmp_path):
    root = tmp_path / "repo"
    write(root, "pyproject.toml", '[project]\nversion = "1.2.3"\n')
    write(root, "src/ai_edda/static/index.html", "ready")
    document = "# Install\n\nRun setup.ps1."
    write(root, "docs/INSTALL_WINDOWS.md", document)
    expected = (root / "docs/INSTALL_WINDOWS.md").read_bytes()
    archive = release.assemble(root, tmp_path / "out")
    with zipfile.ZipFile(archive) as z:
        assert z.read("docs/INSTALL_WINDOWS.md") == expected
    manifest = json.loads((archive.parent / "ai-edda-1.2.3-manifest.json").read_text())
    assert manifest["files"]["docs/INSTALL_WINDOWS.md"] == hashlib.sha256(expected).hexdigest()


def test_missing_static_and_unsafe_output(tmp_path):
    write(tmp_path, "pyproject.toml", '[project]\nversion = "1"\n')
    with pytest.raises(ValueError, match="static index is missing"):
        release.assemble(tmp_path, tmp_path / "out")
    write(tmp_path, "src/ai_edda/static/index.html", "ready")
    with pytest.raises(ValueError, match="outside included"):
        release.assemble(tmp_path, tmp_path / "src/ai_edda/output")


@pytest.mark.parametrize("inside_root", [False, True], ids=["external", "excluded-inside-root"])
@pytest.mark.parametrize(
    ("linked_directory", "filename"),
    [
        ("web/schema", "owner.json"),
        ("scripts", "build_release.py"),
        ("src/ai_edda/linked", "secret.py"),
    ],
    ids=["tree-root", "explicit-file-ancestor", "nested-tree-directory"],
)
def test_inventory_rejects_windows_junction_ancestors(
    tmp_path, inside_root, linked_directory, filename
):
    import sys

    if sys.platform != "win32":
        pytest.skip("Windows junction behavior")
    import _winapi

    root = tmp_path / "repo"
    write(root, "pyproject.toml", '[project]\nversion = "1.0"\n')
    write(root, "src/ai_edda/static/index.html", "ready")
    private = root / ".tools" if inside_root else tmp_path / "private"
    write(private, filename, "private data must not be released")
    link = root / linked_directory
    link.parent.mkdir(parents=True, exist_ok=True)
    _winapi.CreateJunction(str(private), str(link))
    assert link.is_junction()
    assert not link.is_symlink(), "This fixture must test a junction rather than a symbolic link"
    archive = release.assemble(root, tmp_path / "out")
    with zipfile.ZipFile(archive) as z:
        assert f"{linked_directory}/{filename}" not in z.namelist()


@pytest.mark.parametrize("version", ["x/../../escaped", r"x\..\escaped", "x:stream", "", 123])
def test_version_cannot_escape_output(tmp_path, version):
    write(tmp_path, "pyproject.toml", "[project]\nversion = " + json.dumps(version) + "\n")
    write(tmp_path, "src/ai_edda/static/index.html", "ready")
    output = tmp_path / "out"
    with pytest.raises(ValueError, match="safe filename component"):
        release.assemble(tmp_path, output)
    assert not output.exists()


@pytest.mark.parametrize("inside_root", [False, True], ids=["external", "excluded-inside-root"])
def test_main_rejects_linked_wheel_inputs_before_build(tmp_path, monkeypatch, inside_root):
    import sys

    if sys.platform != "win32":
        pytest.skip("Windows junction behavior")
    import _winapi

    root = tmp_path / "repo"
    write(root, "pyproject.toml", '[project]\nversion = "1.0"\n')
    write(root, "src/ai_edda/static/index.html", "ready")
    write(root, ".tools/uv.exe", "not executed")
    private = root / ".tools/private" if inside_root else tmp_path / "private"
    write(private, "owner.json", "private credentials")
    _winapi.CreateJunction(str(private), str(root / "src/ai_edda/linked"))
    calls = []
    monkeypatch.setattr(release, "ROOT", root)
    monkeypatch.setattr(release.subprocess, "run", lambda *args, **kwargs: calls.append(args))
    monkeypatch.setattr(sys, "argv", ["build_release.py", "--output", str(tmp_path / "out")])
    assert release.main() == 1
    assert calls == [], "The build backend must not receive linked private inputs"


def test_frontend_regression_inventory(tmp_path):
    write(tmp_path, "scripts/frontend-checks.json", json.dumps(["web/tests/regression/check.mjs"]))
    for name in (
        "scripts/check_frontend.mjs",
        "scripts/frontend-checks.md",
        "web/tests/regression/check.mjs",
        "tests/test_example.py",
        "web/tests/helper.cjs",
        "docs/scratch/private.mjs",
        "docs/scratch/log.txt",
        "tests/private.json",
        "web/tests/node_modules/secret.cjs",
    ):
        write(tmp_path, name, "content")
    names = {p.relative_to(tmp_path).as_posix() for p in release.inventory(tmp_path)}
    assert {
        "scripts/check_frontend.mjs",
        "scripts/frontend-checks.json",
        "scripts/frontend-checks.md",
        "web/tests/regression/check.mjs",
        "tests/test_example.py",
        "web/tests/helper.cjs",
    } <= names
    assert not names & {
        "docs/scratch/private.mjs",
        "docs/scratch/log.txt",
        "tests/private.json",
        "web/tests/node_modules/secret.cjs",
    }


@pytest.mark.parametrize(
    "name",
    [
        "../private.mjs",
        "web/tests/regression/../private.mjs",
        "C:/private.mjs",
        "/web/tests/regression/check.mjs",
        "web/tests/regression/check.txt",
        r"web/tests/regression/..\private.mjs",
    ],
)
def test_frontend_manifest_rejects_unsafe_paths(tmp_path, name):
    write(tmp_path, "scripts/frontend-checks.json", json.dumps([name]))
    with pytest.raises(ValueError, match="Invalid frontend manifest path"):
        release.inventory(tmp_path)


def test_frontend_manifest_rejects_junction(tmp_path):
    import sys

    if sys.platform != "win32":
        pytest.skip("Windows junction behavior")
    import _winapi

    root = tmp_path / "repo"
    write(root, "scripts/frontend-checks.json", json.dumps(["web/tests/regression/check.mjs"]))
    write(tmp_path / "private", "check.mjs", "private")
    (root / "web/tests").mkdir(parents=True)
    _winapi.CreateJunction(str(tmp_path / "private"), str(root / "web/tests/regression"))
    with pytest.raises(ValueError, match="linked frontend check"):
        release.inventory(root)


def test_demo_release_inventory(tmp_path):
    included = (
        ".editorconfig", ".gitattributes", ".python-version",
        ".github/workflows/checks.yml", "web/demo-public/mockServiceWorker.js",
        "web/demo-public/demo-cover.jpg", "web/src/demo/app.tsx",
        "web/src/demo/style.css", "web/tests/demo/check.mjs",
    )
    excluded = (
        "src/scratch/private.py", ".agents/private.py",
        "web/demo-public/private-settings.json", "web/src/demo/node_modules/secret.ts",
        "music/song.mp3", "models/model.json",
    )
    for name in (*included, *excluded):
        write(tmp_path, name, "content")
    names = {p.relative_to(tmp_path).as_posix() for p in release.inventory(tmp_path)}
    assert set(included) <= names
    assert not set(excluded) & names
