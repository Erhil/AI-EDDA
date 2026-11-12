import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path

from .analysis_catalog import _receipt
from .arrangement import SingleLaneProvider
from .arrangement_domain import ArrangementContext, ArrangementRequest, ChartDraft
from .asset_catalog import _digest, _safe
from .generation_drafts import assemble_draft
from .project_store import _id, _json


def _hash(value):
    return hashlib.sha256(_json(value)).hexdigest()


def _validate(draft_id, context, result):
    if not isinstance(context, dict) or not isinstance(result, dict) or set(result) != {"draft"}:
        raise ValueError("Invalid generation snapshot")
    if _id(context.get("draft_id")) != draft_id:
        raise ValueError("Generation draft ID mismatch")
    request = ArrangementRequest.model_validate(context["request"])
    arrangement = ArrangementContext.model_validate(context["context"])
    draft = ChartDraft.model_validate(result["draft"])
    _check_generation_reassembly(draft_id, request, arrangement, draft, result["draft"])
    return draft.model_dump(mode="json")



def _check_generation_reassembly(draft_id, request, arrangement, draft, draft_value, *, cancel=None):
    if cancel is not None:
        cancel()
    expected = assemble_draft(
        request, arrangement, draft.generation_run.output, SingleLaneProvider().describe()
    )
    if cancel is not None:
        cancel()
    expected = expected.model_copy(update={"id": draft_id})
    if _json(expected.model_dump(mode="json")) != _json(draft_value):
        raise ValueError("Generation draft mismatch")
    if cancel is not None:
        cancel()


def _check_generation_publication(
    receipt, *, context_sha256, result_sha256, public_bytes, artifact_digests, cancel=None
):
    if cancel is not None:
        cancel()
    if (
        not isinstance(receipt, dict)
        or set(receipt) != {"job_id", "context_sha256", "result_sha256", "artifacts", "public_result"}
        or not isinstance(receipt["job_id"], str)
        or not receipt["job_id"]
        or any(
            not isinstance(receipt[k], str)
            or len(receipt[k]) != 64
            or any(c not in "0123456789abcdef" for c in receipt[k])
            for k in ("context_sha256", "result_sha256")
        )
        or not isinstance(receipt["artifacts"], dict)
        or not isinstance(receipt["public_result"], dict)
    ):
        raise ValueError("Invalid publication receipt")
    if set(receipt["artifacts"]) != {"context.json", "draft.json"}:
        raise ValueError("Invalid generation artifact set")
    if receipt["artifacts"] != artifact_digests:
        raise ValueError("Generation artifact hash mismatch")
    if (
        context_sha256 != receipt["context_sha256"]
        or result_sha256 != receipt["result_sha256"]
        or public_bytes != _json(receipt["public_result"])
    ):
        raise ValueError("Invalid generation receipt")
    if cancel is not None:
        cancel()


def resolve_generation_with_context(project_dir, draft_id):
    draft_id = _id(draft_id)
    root = _safe(Path(project_dir) / "generation" / draft_id)
    receipt = _receipt(root)
    if set(receipt["artifacts"]) != {"context.json", "draft.json"}:
        raise ValueError("Invalid generation artifact set")
    for name, digest in receipt["artifacts"].items():
        if _digest(_safe(root / name)) != digest:
            raise ValueError("Generation artifact hash mismatch")
    context = json.loads(_safe(root / "context.json").read_text(encoding="utf-8"))
    result = {"draft": json.loads(_safe(root / "draft.json").read_text(encoding="utf-8"))}
    public = _validate(draft_id, context, result)
    _check_generation_publication(
        receipt, context_sha256=_hash(context), result_sha256=_hash(result),
        public_bytes=_json(public), artifact_digests=receipt["artifacts"],
    )
    return public, context["context"]


def resolve_generation(project_dir, draft_id):
    return resolve_generation_with_context(project_dir, draft_id)[0]


def reconcile_generation(project_dir, draft_id, job_id, context, result):
    root = _safe(Path(project_dir) / "generation" / _id(draft_id))
    if not root.exists():
        return False, None
    receipt = _receipt(root)
    if (receipt["job_id"], receipt["context_sha256"], receipt["result_sha256"]) != (
        str(job_id),
        _hash(context),
        _hash(result),
    ):
        raise ValueError("Generation publication conflict")
    public = resolve_generation(project_dir, draft_id)
    if _json(public) != _json(result["draft"]):
        raise ValueError("Generation publication conflict")
    return True, public


def publish_generation(project_dir, draft_id, job_id, context, result):
    draft_id = _id(draft_id)
    found, public = reconcile_generation(project_dir, draft_id, job_id, context, result)
    if found:
        return public
    if not str(job_id):
        raise ValueError("Invalid job ID")
    public = _validate(draft_id, context, result)
    catalog = _safe(Path(project_dir) / "generation")
    catalog.mkdir(exist_ok=True)
    temp = Path(tempfile.mkdtemp(prefix=".staging-", dir=catalog))
    try:
        for name, value in (("context.json", context), ("draft.json", public)):
            with (temp / name).open("wb") as f:
                f.write(_json(value))
                f.flush()
                os.fsync(f.fileno())
        receipt = {
            "job_id": str(job_id),
            "context_sha256": _hash(context),
            "result_sha256": _hash(result),
            "artifacts": {name: _digest(temp / name) for name in ("context.json", "draft.json")},
            "public_result": public,
        }
        with (temp / "publication.json").open("wb") as f:
            f.write(_json(receipt))
            f.flush()
            os.fsync(f.fileno())
        try:
            os.rename(temp, catalog / draft_id)
        except OSError:
            found, public = reconcile_generation(project_dir, draft_id, job_id, context, result)
            if found:
                return public
            raise
        return public
    finally:
        shutil.rmtree(temp, ignore_errors=True)
