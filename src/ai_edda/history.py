from dataclasses import dataclass, replace
from typing import Literal

from .project_store import ProjectSnapshot


def _grid(g):
    # Segment review is derived state. Timing and origin remain owned together.
    segments = tuple(segment.model_copy(update={"review": None}) for segment in g.segments)
    return (segments, g.meter, g.anchors)


def _chart(c):
    return (c.id, c.metadata, c.target_profile, c.provenance, c.events)


@dataclass(frozen=True)
class HistoryRecord:
    before: ProjectSnapshot
    after: ProjectSnapshot

    @classmethod
    def between(cls, before: ProjectSnapshot, after: ProjectSnapshot):
        return cls(before, after)

    def restore(
        self, current: ProjectSnapshot, direction: Literal["undo", "redo"]
    ) -> ProjectSnapshot:
        if direction not in ("undo", "redo"):
            raise ValueError("invalid direction")
        source, target = (
            (self.after, self.before) if direction == "undo" else (self.before, self.after)
        )

        def conflict():
            raise ValueError("UNDO_CONFLICT")

        p, sp, tp = current.project, source.project, target.project
        updates = {}
        meta = {}
        for field in ("title", "artist", "mapper", "subtitle", "explicit"):
            s, t = getattr(sp.metadata, field), getattr(tp.metadata, field)
            if s != t:
                if getattr(p.metadata, field) != s:
                    conflict()
                meta[field] = t
        if meta:
            updates["metadata"] = p.metadata.model_copy(update=meta)
        for field in ("audio", "cover", "analysis", "plan_inputs"):
            s, t = getattr(sp, field), getattr(tp, field)
            if s != t:
                if getattr(p, field) != s:
                    conflict()
                updates[field] = t
        grid = current.grid
        grid_changed = _grid(source.grid) != _grid(target.grid)
        if grid_changed:
            if _grid(grid) != _grid(source.grid):
                conflict()
            grid = grid.model_copy(
                update={
                    "segments": target.grid.segments,
                    "meter": target.grid.meter,
                    "anchors": target.grid.anchors,
                    "review": "needs_review",
                }
            )
        sm = {c.id: c for c in source.charts}
        tm = {c.id: c for c in target.charts}
        charts = list(current.charts)
        for cid in dict.fromkeys((*tm, *sm)):
            s, t = sm.get(cid), tm.get(cid)
            idx = next((i for i, c in enumerate(charts) if c.id == cid), None)
            c = charts[idx] if idx is not None else None
            if s is None or t is None:
                if s is None:
                    if c is not None:
                        conflict()
                    pos = next(i for i, item in enumerate(target.charts) if item.id == cid)
                    charts.insert(
                        min(pos, len(charts)), t.model_copy(update={"review": "needs_review"})
                    )
                else:
                    if c is None or _chart(c) != _chart(s):
                        conflict()
                    charts.pop(idx)
                continue
            changed = {}
            for field in ("events", "target_profile"):
                if getattr(s, field) != getattr(t, field):
                    if c is None or getattr(c, field) != getattr(s, field):
                        conflict()
                    changed[field] = getattr(t, field)
            cm = {}
            for field in ("name", "rank", "speed"):
                if getattr(s.metadata, field) != getattr(t.metadata, field):
                    if c is None or getattr(c.metadata, field) != getattr(s.metadata, field):
                        conflict()
                    cm[field] = getattr(t.metadata, field)
            if cm:
                changed["metadata"] = c.metadata.model_copy(update=cm)
            if changed:
                changed["review"] = "needs_review"
                charts[idx] = c.model_copy(update=changed)
        if grid_changed:
            charts = [c.model_copy(update={"review": "needs_review"}) for c in charts]
        return replace(
            current, project=p.model_copy(update=updates), grid=grid, charts=tuple(charts)
        )
