import builtins
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

import ai_edda.api.app as app_module
from ai_edda import doctor as doctor_module
from ai_edda import model_download
from ai_edda.access import OwnerAuth
from ai_edda.cli import app
from ai_edda.doctor import inspect


def initialized(tmp_path):
    static = tmp_path / "static"
    static.mkdir()
    (static / "index.html").write_text("frontend")
    config = tmp_path / "config"
    OwnerAuth.setup(config / "owner.json", "private-owner-password")
    return tmp_path / "projects", config, static


def snapshot(root):
    return {
        str(p.relative_to(root)): (p.read_bytes(), p.stat().st_mtime_ns)
        for p in root.rglob("*")
        if p.is_file()
    }


def test_missing_checkpoint_is_warning_and_paths_are_not_created(tmp_path):
    static = tmp_path / "static"
    static.mkdir()
    (static / "index.html").write_text("ok")
    config = tmp_path / "config"
    config.mkdir()
    OwnerAuth.setup(config / "owner.json", "test-password")
    project = tmp_path / "new-project"
    result = inspect(project, config, static)
    assert result["checkpoint"]["status"] == "missing"
    assert result["ok"]
    assert not project.exists()
    assert "secret" not in json.dumps(result)


def test_cuda_unavailable_is_failure(tmp_path, monkeypatch):
    static = tmp_path / "static"
    static.mkdir()
    (static / "index.html").write_text("ok")
    (tmp_path / "owner.json").write_text("{}")
    monkeypatch.setitem(
        sys.modules, "torch", SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: False))
    )
    result = inspect(tmp_path, tmp_path, static, "cuda")
    assert not result["ok"]
    assert any("CUDA" in item for item in result["errors"])


def test_cli_json_reports_missing_owner(tmp_path):
    result = CliRunner().invoke(app, ["doctor", "--config-root", str(tmp_path), "--json"])
    assert result.exit_code == 1
    assert json.loads(result.stdout)["owner_config_present"] is False


def test_human_mode_is_a_readable_summary_not_json(tmp_path):
    project, config, _ = initialized(tmp_path)
    result = CliRunner().invoke(
        app, ["doctor", "--project-root", str(project), "--config-root", str(config)]
    )
    assert result.exit_code == 0, result.output
    assert not result.stdout.lstrip().startswith("{"), (
        "default doctor output must be a human summary"
    )
    assert str(project) in result.stdout and str(config) in result.stdout
    assert "manual editor" in result.stdout.lower()


def test_json_contract_and_missing_optional_model_do_not_mutate_directories(tmp_path):
    project, config, static = initialized(tmp_path)
    before = snapshot(tmp_path)
    report = inspect(project, config, static)
    assert report["schema_version"] == 1 and report["ok"] is True
    assert report["paths"] == {
        "project_root": str(project.resolve()),
        "config_root": str(config.resolve()),
    }
    assert report["checkpoint"]["status"] == "missing"
    assert report["checkpoint"]["sha256"] is None
    assert isinstance(report["dependencies"], dict)
    assert report["available_ram_bytes"] > 0
    assert all(value > 0 for value in report["free_disk_bytes"].values())
    assert any("manual editor" in warning for warning in report["warnings"])
    assert "private-owner-password" not in json.dumps(report)
    assert not project.exists()
    assert snapshot(tmp_path) == before


@pytest.mark.parametrize("contents", ["not-json-private-secret", "{}"])
def test_invalid_owner_configuration_is_a_setup_error(tmp_path, contents):
    project, config, static = initialized(tmp_path)
    (config / "owner.json").write_text(contents)
    before = snapshot(tmp_path)
    result = inspect(project, config, static)
    assert result["ok"] is False, (
        "present but unusable owner configuration must not report readiness"
    )
    assert any("owner" in error.lower() for error in result["errors"])
    assert "not-json-private-secret" not in json.dumps(result)
    assert snapshot(tmp_path) == before


def test_project_root_file_is_a_setup_error(tmp_path):
    project, config, static = initialized(tmp_path)
    project.write_text("existing file must stay intact")
    before = snapshot(tmp_path)
    report = inspect(project, config, static)
    assert report["ok"] is False
    assert any("project" in error.lower() for error in report["errors"])
    assert snapshot(tmp_path) == before


def test_missing_frontend_is_required_failure_and_cli_exits_one(tmp_path, monkeypatch):
    project, config, static = initialized(tmp_path)
    (static / "index.html").unlink()
    monkeypatch.setattr(app_module, "STATIC", static)
    result = CliRunner().invoke(
        app, ["doctor", "--project-root", str(project), "--config-root", str(config), "--json"]
    )
    assert result.exit_code == 1
    assert json.loads(result.stdout)["frontend_present"] is False


def test_missing_core_dependency_is_required_failure(tmp_path, monkeypatch):
    project, config, static = initialized(tmp_path)
    original = doctor_module.metadata.version

    def version(name):
        if name == "fastapi":
            raise doctor_module.metadata.PackageNotFoundError(name)
        return original(name)

    monkeypatch.setattr(doctor_module.metadata, "version", version)
    result = inspect(project, config, static)
    assert result["ok"] is False
    assert result["dependencies"]["fastapi"] is None
    assert any("fastapi" in error for error in result["errors"])


def test_cpu_check_never_imports_torch_or_downloads(tmp_path, monkeypatch):
    project, config, static = initialized(tmp_path)
    original = builtins.__import__

    def guarded(name, *args, **kwargs):
        assert name != "torch", "CPU setup inspection must not load Torch"
        return original(name, *args, **kwargs)

    def no_network(*args, **kwargs):
        raise AssertionError("doctor must never download")

    monkeypatch.setattr(builtins, "__import__", guarded)
    monkeypatch.setattr(model_download, "urlopen", no_network)
    monkeypatch.setattr(model_download, "ensure_model", no_network)
    assert inspect(project, config, static, "cpu")["ok"]


def test_invalid_device_is_cli_usage_error(tmp_path):
    result = CliRunner().invoke(
        app, ["doctor", "--config-root", str(tmp_path), "--device", "gpu", "--json"]
    )
    assert result.exit_code == 2
    assert "cpu or cuda" in result.output
    assert not list(tmp_path.iterdir())


def test_download_cli_uses_explicit_and_default_config_paths(tmp_path, monkeypatch):
    calls = []

    def ensure(root):
        calls.append(Path(root))
        return Path(root) / "models" / "beat-this" / "final0.ckpt", "existing"

    monkeypatch.setattr(model_download, "ensure_model", ensure)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    runner = CliRunner()
    explicit = runner.invoke(app, ["download-model", "--config-root", str(tmp_path / "custom")])
    default = runner.invoke(app, ["download-model"])
    assert explicit.exit_code == default.exit_code == 0
    assert calls == [tmp_path / "custom", tmp_path / "local" / "AI-EDDA"]
    assert "existing" in explicit.output and "final0.ckpt" in explicit.output
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("failure", [OSError, ValueError])
def test_download_cli_reports_failure_without_retry_or_install(tmp_path, monkeypatch, failure):
    def fail(root):
        raise failure("injected failure")

    monkeypatch.setattr(model_download, "ensure_model", fail)
    result = CliRunner().invoke(app, ["download-model", "--config-root", str(tmp_path)])
    assert result.exit_code == 1
    assert "injected failure" in result.output
    assert not list(tmp_path.iterdir())


def test_human_cuda_result_uses_inspected_hardware_state(tmp_path, monkeypatch):
    project, config, _ = initialized(tmp_path)
    monkeypatch.setitem(
        sys.modules,
        "torch",
        SimpleNamespace(
            cuda=SimpleNamespace(
                is_available=lambda: True,
                get_device_properties=lambda index: SimpleNamespace(total_memory=24 * 1024**3),
            )
        ),
    )
    result = CliRunner().invoke(
        app,
        [
            "doctor",
            "--project-root",
            str(project),
            "--config-root",
            str(config),
            "--device",
            "cuda",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "CUDA: available" in result.stdout


def test_human_cpu_result_does_not_claim_unchecked_cuda_is_unavailable(tmp_path):
    project, config, _ = initialized(tmp_path)
    result = CliRunner().invoke(
        app, ["doctor", "--project-root", str(project), "--config-root", str(config)]
    )
    assert result.exit_code == 0, result.output
    assert "CUDA: unavailable" not in result.stdout, "CPU inspection does not check CUDA"


def test_human_output_handles_unavailable_disk_measurements(tmp_path, monkeypatch):
    project, config, _ = initialized(tmp_path)

    def cannot_inspect(path):
        raise OSError("disk measurement unavailable")

    monkeypatch.setattr(doctor_module.shutil, "disk_usage", cannot_inspect)
    result = CliRunner().invoke(
        app, ["doctor", "--project-root", str(project), "--config-root", str(config)]
    )
    assert result.exit_code == 0, result.exception
    assert "Cannot inspect free disk space" in result.stdout


@pytest.mark.parametrize("missing", ["numpy", "Pillow", "pydantic", "cryptography"])
def test_eager_runtime_dependencies_are_required_and_never_claim_editor_available(
    tmp_path, monkeypatch, missing
):
    project, config, static = initialized(tmp_path)
    original = doctor_module.metadata.version

    def version(name):
        if name.lower() == missing.lower():
            raise doctor_module.metadata.PackageNotFoundError(name)
        return original(name)

    monkeypatch.setattr(doctor_module.metadata, "version", version)
    report = inspect(project, config, static)
    assert report["ok"] is False, f"{missing} is imported during application startup"
    assert any(missing.lower() in error.lower() for error in report["errors"])
    assert not any("manual editor remains available" in warning for warning in report["warnings"])
