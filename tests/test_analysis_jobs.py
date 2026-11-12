from types import SimpleNamespace

import pytest

from ai_edda import analysis_jobs


def test_handlers_use_workspace_name(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(analysis_jobs, "publish_analysis", lambda *args: calls.append(args) or "ok")
    handler = analysis_jobs.make_analysis_handlers(SimpleNamespace(_dir=lambda value: tmp_path))[
        "analysis"
    ]
    context = {"project_id": "project", "analysis_id": "analysis"}
    assert handler["publish"](context, {}, tmp_path / "job") == "ok"
    assert calls[0][2] == "job"
    assert handler["worker"] is analysis_jobs.analysis_worker


def test_rejects_invalid_device_before_checkpoint(tmp_path):
    store = SimpleNamespace(_dir=lambda value: tmp_path)
    ident = "00000000-0000-0000-0000-000000000001"
    with pytest.raises(ValueError, match="device"):
        analysis_jobs.reserve_analysis(
            None, store, ident, ident, ident, {}, tmp_path / "missing", "cpu", True
        )
