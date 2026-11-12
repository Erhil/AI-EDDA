import { useState } from 'react';
import type { TimingGrid } from './generated/ui-contract';
import { finalTime, type IndexedNote } from './timeline';

type Props = { chartId?: string; selected: IndexedNote[]; grid: TimingGrid; disabled: boolean; mutate: (kind: string, payload: Record<string, unknown>) => Promise<unknown>; onSeek?: (seconds: number) => void };
export default function NoteInspector({ chartId, selected, grid, disabled, mutate, onSeek }: Props) {
  const [page, setPage] = useState(0);
  const [error, setError] = useState('');
  const events = [...new Map(selected.filter(x => x.event.id).map(x => [x.event.id!, x.event])).values()];
  const currentPage = Math.min(page, Math.max(0, Math.ceil(events.length / 50) - 1));
  const send = (payload: Record<string, unknown>) => { if (disabled || !chartId) return; setError(''); void mutate('set_locks', { chart_id: chartId, ...payload }).catch(e => setError(String(e))); };
  return <section aria-label="Выбранные события"><h3>Выбранные события</h3>{events.slice(currentPage * 50, currentPage * 50 + 50).map(event => {
    const time = finalTime(grid, event.time_ref);
    const eventLocks = { time: !!event.locks?.time, existence: !!event.locks?.existence, membership: !!event.locks?.membership };
    return <div key={event.id}><div>Событие {event.id?.slice(0, 8)} · <button onClick={() => onSeek?.(time)}>{time.toFixed(3)} с</button> · {event.time_ref.kind === 'beat' ? `beat ${event.time_ref.beat_q} (offset ${event.time_ref.micro_offset_sec ?? 0} с)` : `audio ${event.time_ref.time_sec} с`} · created_by: {event.provenance?.created_by ?? '—'} · source: {event.provenance?.source_origin ?? '—'} {event.provenance?.source_ids?.join(', ')}</div>
      {(['time', 'existence', 'membership'] as const).map(key => <label key={key}><input type="checkbox" disabled={disabled || !chartId} checked={eventLocks[key]} onChange={e => send({ event_ids: [event.id], event_locks: { ...eventLocks, [key]: e.target.checked } })} />{key}</label>)}
      {selected.filter(x => x.event.id === event.id && x.note.id).map(({ note }) => { const noteLocks = { lane: !!note.locks?.lane, existence: !!note.locks?.existence }; return <div key={note.id}>Нота {note.id?.slice(0, 8)} · дорожка {note.lane + 1} · created_by: {note.provenance?.created_by ?? '—'} · source: {note.provenance?.source_origin ?? '—'} {note.provenance?.source_ids?.join(', ')} {(['lane', 'existence'] as const).map(key => <label key={key}><input type="checkbox" disabled={disabled || !chartId} checked={noteLocks[key]} onChange={e => send({ note_ids: [note.id], note_locks: { ...noteLocks, [key]: e.target.checked } })} />{key}</label>)}</div>; })}</div>;
  })}<button disabled={currentPage === 0} onClick={() => setPage(currentPage - 1)}>Назад</button><span>{currentPage + 1} / {Math.max(1, Math.ceil(events.length / 50))}</span><button disabled={(currentPage + 1) * 50 >= events.length} onClick={() => setPage(currentPage + 1)}>Далее</button><div role="alert">{error}</div></section>;
}
