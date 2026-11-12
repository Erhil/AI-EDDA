"""Connect import workers to coordinator publication and recovery."""

from .import_worker import import_worker
from .project_imports import publish_project_import, reconcile_project_import


def _project_context(context):
    keys = {"request_id", "archive_kind", "file_name", "file_size"}
    if not isinstance(context, dict) or set(context) not in (keys, keys | {"prior_receipt"}):
        raise ValueError("Invalid import context")
    projected = {"request": {key: context[key] for key in keys}}
    if "prior_receipt" in context:
        projected["prior_receipt"] = context["prior_receipt"]
    return projected


def make_import_handlers(store):
    def publish(context, result, workspace):
        return publish_project_import(store, _project_context(context), result, workspace)

    def reconcile(job_id, context, result):
        return reconcile_project_import(store, job_id, _project_context(context), result)

    return {"project_import": {"worker": import_worker, "publish": publish, "reconcile": reconcile}}
