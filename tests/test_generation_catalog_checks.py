import hashlib
import json
import runpy
from copy import deepcopy
from pathlib import Path

import pytest

from ai_edda.generation_catalog import _check_generation_publication, _check_generation_reassembly


def test_publication_checks_receipt_and_hashes():
    digest = "a" * 64
    artifacts = {"context.json": digest, "draft.json": digest}
    receipt = {
        "job_id": "job",
        "context_sha256": digest,
        "result_sha256": digest,
        "artifacts": artifacts,
        "public_result": {"id": "draft"},
    }
    args = dict(
        context_sha256=digest,
        result_sha256=digest,
        public_bytes=b'{"id":"draft"}',
        artifact_digests=artifacts,
    )
    _check_generation_publication(receipt, **args)
    with pytest.raises(ValueError):
        _check_generation_publication({**receipt, "job_id": ""}, **args)
    with pytest.raises(ValueError):
        _check_generation_publication(receipt, **{**args, "result_sha256": "b" * 64})
    with pytest.raises(ValueError):
        _check_generation_publication(receipt, **{**args, "public_bytes": b"{}"})
    with pytest.raises(ValueError):
        _check_generation_publication(receipt, **{**args, "artifact_digests": {}})


def test_checks_preserve_cancellation_identity():
    error = OSError("cancel")

    def cancel():
        raise error

    with pytest.raises(OSError) as caught:
        _check_generation_reassembly(None, None, None, None, None, cancel=cancel)
    assert caught.value is error
    with pytest.raises(OSError) as caught:
        _check_generation_publication(
            None,
            context_sha256=None,
            result_sha256=None,
            public_bytes=None,
            artifact_digests=None,
            cancel=cancel,
        )
    assert caught.value is error


def test_reassembly_compares_complete_draft(monkeypatch):
    from types import SimpleNamespace

    from ai_edda import generation_catalog

    class Expected:
        def model_copy(self, *, update):
            assert update == {"id": "draft"}
            return self

        def model_dump(self, *, mode):
            assert mode == "json"
            return {"id": "draft", "content": 1}

    monkeypatch.setattr(generation_catalog, "assemble_draft", lambda *args: Expected())
    draft = SimpleNamespace(generation_run=SimpleNamespace(output=None))
    _check_generation_reassembly("draft", None, None, draft, {"id": "draft", "content": 1})
    with pytest.raises(ValueError, match="draft mismatch"):
        _check_generation_reassembly("draft", None, None, draft, {"id": "draft", "content": 2})


@pytest.fixture
def real_publication(tmp_path, monkeypatch):
    integration = Path(__file__).resolve().parent / "integration"
    monkeypatch.syspath_prepend(str(integration))
    helpers = runpy.run_path(str(integration / "test_native_generation_review.py"))
    media = helpers["media"].__wrapped__(tmp_path)
    inputs = helpers["inputs"].__wrapped__(media)
    return helpers["generation"].__wrapped__(inputs), helpers


def publication_values(data, draft_id):
    from ai_edda.arrangement_domain import ArrangementContext, ArrangementRequest, ChartDraft

    root = data["root"] / "generation" / draft_id
    envelope = json.loads((root / "context.json").read_bytes())
    raw = json.loads((root / "draft.json").read_bytes())
    receipt = json.loads((root / "publication.json").read_bytes())
    artifacts = {
        name: hashlib.sha256((root / name).read_bytes()).hexdigest()
        for name in ("context.json", "draft.json")
    }
    return (
        envelope,
        raw,
        receipt,
        artifacts,
        ArrangementRequest.model_validate(envelope["request"]),
        ArrangementContext.model_validate(envelope["context"]),
        ChartDraft.model_validate(raw),
    )


def test_real_invalid_and_rejected_publications_use_pure_checks(real_publication, monkeypatch):
    from ai_edda import generation_catalog as catalog
    from ai_edda.arrangement_domain import ArrangementContext, ArrangementRequest, ChartDraft

    data, _ = real_publication
    for draft_id in (data["draft_id"], data["rejected_id"]):
        envelope, raw, receipt, artifacts, request, arrangement, draft = publication_values(
            data, draft_id
        )
        public, context = catalog.resolve_generation_with_context(data["root"], draft_id)
        assert public == raw and context == envelope["context"]
        before = deepcopy((envelope, raw, receipt))
        args = dict(
            context_sha256=catalog._hash(envelope),
            result_sha256=catalog._hash({"draft": raw}),
            public_bytes=catalog._json(public),
            artifact_digests=artifacts,
        )

        def forbidden(*args, **kwargs):
            pytest.fail("Pure generation check performed IO or reparsed a model set")

        with monkeypatch.context() as patch:
            for name in ("open", "read_bytes", "read_text"):
                patch.setattr(Path, name, forbidden)
            for model in (ArrangementRequest, ArrangementContext, ChartDraft):
                patch.setattr(model, "model_validate", forbidden)
            catalog._check_generation_reassembly(draft_id, request, arrangement, draft, raw)
            catalog._check_generation_publication(receipt, **args)
        assert (envelope, raw, receipt) == before


def test_wrapped_result_hash_and_repaired_hashes_do_not_replace_reassembly(real_publication):
    from ai_edda import generation_catalog as catalog
    from ai_edda.arrangement_domain import ChartDraft

    data, helpers = real_publication
    draft_id = data["draft_id"]
    envelope, raw, receipt, artifacts, request, arrangement, draft = publication_values(
        data, draft_id
    )
    args = dict(
        context_sha256=catalog._hash(envelope),
        result_sha256=catalog._hash({"draft": raw}),
        public_bytes=catalog._json(raw),
        artifact_digests=artifacts,
    )
    assert catalog._hash(raw) != args["result_sha256"]
    with pytest.raises(ValueError):
        catalog._check_generation_publication(
            receipt, **{**args, "result_sha256": catalog._hash(raw)}
        )
    changed = deepcopy(raw)
    changed["chart"]["review"] = "approved"
    with pytest.raises(ValueError, match="draft mismatch"):
        catalog._check_generation_reassembly(
            draft_id, request, arrangement, ChartDraft.model_validate(changed), changed
        )
    directory = data["root"] / "generation" / draft_id
    helpers["write"](directory / "draft.json", changed)
    helpers["repair_publication"](directory)
    with pytest.raises(ValueError, match="draft mismatch"):
        catalog.resolve_generation_with_context(data["root"], draft_id)


@pytest.mark.parametrize("error_type", [OSError, TypeError, RecursionError])
def test_pure_checks_preserve_late_cancellation_identity(real_publication, error_type):
    from ai_edda import generation_catalog as catalog

    data, _ = real_publication
    draft_id = data["draft_id"]
    envelope, raw, receipt, artifacts, request, arrangement, draft = publication_values(
        data, draft_id
    )
    for operation, stops in (("reassembly", (2, 3)), ("publication", (2,))):
        for stop in stops:
            calls = 0
            error = error_type("late cancel")

            def cancel():
                nonlocal calls
                calls += 1
                if calls == stop:
                    raise error

            with pytest.raises(error_type) as caught:
                if operation == "reassembly":
                    catalog._check_generation_reassembly(
                        draft_id, request, arrangement, draft, raw, cancel=cancel
                    )
                else:
                    catalog._check_generation_publication(
                        receipt,
                        context_sha256=catalog._hash(envelope),
                        result_sha256=catalog._hash({"draft": raw}),
                        public_bytes=catalog._json(raw),
                        artifact_digests=artifacts,
                        cancel=cancel,
                    )
            assert caught.value is error


@pytest.mark.parametrize(
    "change",
    [
        {"extra": None},
        {"job_id": ""},
        {"job_id": 1},
        {"context_sha256": "A" * 64},
        {"result_sha256": "a" * 63},
        {"artifacts": []},
        {"public_result": None},
    ],
)
def test_publication_check_keeps_exact_receipt_contract(change):
    digest = "a" * 64
    artifacts = {"context.json": digest, "draft.json": digest}
    receipt = {
        "job_id": "arbitrary nonempty job",
        "context_sha256": digest,
        "result_sha256": digest,
        "artifacts": artifacts,
        "public_result": {},
    }
    with pytest.raises(ValueError):
        _check_generation_publication(
            {**receipt, **change},
            context_sha256=digest,
            result_sha256=digest,
            public_bytes=b"{}",
            artifact_digests=artifacts,
        )
