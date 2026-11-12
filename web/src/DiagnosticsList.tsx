import { useState } from 'react';
import type { ValidationResponse } from './validationClient';
import type { ProjectView } from './generated/ui-contract';

type Target = { chartId: string; kind: 'event' | 'note'; id: string; view: ProjectView };
export default function DiagnosticsList({ response, onNavigate }: { response: ValidationResponse | null; onNavigate?: (target: Target) => void }) {
  const [page, setPage] = useState(0);
  const [offsets, setOffsets] = useState<Record<number, number>>({});
  const diagnostics = response?.diagnostics ?? [];
  const p = Math.min(page, Math.max(0, Math.ceil(diagnostics.length / 20) - 1));
  return <div><p>Диагностик: {diagnostics.length}</p>{diagnostics.slice(p * 20, p * 20 + 20).map((d, index) => {
    const i = p * 20 + index; const events = d.event_ids ?? []; const notes = d.note_ids ?? []; const count = events.length + notes.length;
    const offset = Math.min(offsets[i] ?? 0, Math.max(0, Math.ceil(count / 50) - 1));
    return <div key={i}><p>{d.chart_id}: {d.severity}: {d.message}</p><p>Затронуто: {count}</p>{Array.from({ length: Math.min(50, count - offset * 50) }, (_, n) => {
      const at = offset * 50 + n; const kind = at < events.length ? 'event' : 'note'; const id = kind === 'event' ? events[at] : notes[at - events.length];
      return <button key={`${kind}:${at}`} onClick={() => response && onNavigate?.({ chartId: d.chart_id, kind, id, view: response.view })}>{kind}: {id}</button>;
    })}{count > 50 && <p><button disabled={offset === 0} onClick={() => setOffsets(o => ({ ...o, [i]: offset - 1 }))}>Назад</button> {offset + 1} / {Math.ceil(count / 50)} <button disabled={(offset + 1) * 50 >= count} onClick={() => setOffsets(o => ({ ...o, [i]: offset + 1 }))}>Далее</button></p>}</div>;
  })}{diagnostics.length > 20 && <p><button disabled={p === 0} onClick={() => setPage(p - 1)}>Назад</button> {p + 1} / {Math.ceil(diagnostics.length / 20)} <button disabled={(p + 1) * 20 >= diagnostics.length} onClick={() => setPage(p + 1)}>Далее</button></p>}</div>;
}
