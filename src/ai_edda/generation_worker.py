from uuid import UUID

from .arrangement import SingleLaneProvider
from .arrangement_domain import ArrangementContext, ArrangementRequest
from .generation_drafts import assemble_draft


def generation_worker(payload, progress, cancel):
    """Generate a draft from the parent-provided JSON snapshot."""

    def check():
        if cancel.is_set():
            raise RuntimeError("Generation cancelled")

    check()
    request = ArrangementRequest.model_validate(payload["request"])
    context = ArrangementContext.model_validate(payload["context"])
    draft_id = payload["draft_id"]
    UUID(draft_id)
    provider = SingleLaneProvider()
    capabilities = provider.describe()
    progress(0.1)
    check()
    output = provider.generate(request, context)
    check()
    progress(0.8)
    check()
    draft = assemble_draft(request, context, output, capabilities)
    draft = draft.model_copy(update={"id": draft_id})
    progress(1.0)
    check()
    return {"draft": draft.model_dump(mode="json")}
