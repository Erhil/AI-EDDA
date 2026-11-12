import { useState } from 'react';
import type { Chart, ChartDraft, HitEvent, TimingGrid } from './generated/ui-contract';
import { finalTime } from './timeline';

type Props = { draft: ChartDraft; baseChart: Chart; grid: TimingGrid; reasons?: string[]; onSeek?: (seconds: number) => void };
type Entry = { kind: string; id: string; before?: HitEvent; after?: HitEvent; error?: string };
const PAGE_SIZE = 50;

export default function DraftDetails({ draft, baseChart, grid, reasons = [], onSeek }: Props) {
  const [page, setPage] = useState(0);
  const diff = draft.diff;
  const before = new Map((baseChart.events ?? []).map(event => [event.id, event]));
  const after = new Map((draft.chart.events ?? []).map(event => [event.id, event]));
  const entries: Entry[] = [
    ...(diff?.added_event_ids ?? []).map(id => ({ kind: 'Добавлено', id, after: after.get(id), error: after.has(id) ? undefined : 'Событие отсутствует в черновике' })),
    ...(diff?.removed_event_ids ?? []).map(id => ({ kind: 'Удалено', id, before: before.get(id), error: before.has(id) ? undefined : 'Событие отсутствует в исходной карте' })),
    ...(diff?.changed_event_ids ?? []).map(id => ({ kind: 'Изменено', id, before: before.get(id), after: after.get(id), error: before.has(id) && after.has(id) ? undefined : 'Событие отсутствует в исходной карте или черновике' })),
  ];
  const currentPage = Math.min(page, Math.max(0, Math.ceil(entries.length / PAGE_SIZE) - 1));
  function eventDetails(event: HitEvent, label: string) {
    let time: number | undefined;
    try { const value = finalTime(grid, event.time_ref); if (Number.isFinite(value)) time = value; } catch { /* Invalid grid or time reference. */ }
    return <div><strong>{label}: </strong>время {time === undefined ? 'недоступно' : `${time.toFixed(3)} с`}; дорожки {(event.notes ?? []).map(note => note.lane + 1).join(', ') || 'нет'}; происхождение <code>{JSON.stringify({ event: event.provenance ?? null, notes: (event.notes ?? []).map(note => ({ id: note.id, provenance: note.provenance ?? null })) })}</code> {time !== undefined && onSeek && <button onClick={() => onSeek(time!)}>Перейти</button>}</div>;
  }
  return <section><h3>Сравнение черновика</h3>
    <p>Добавлено: {diff?.added_event_ids?.length ?? 0}; удалено: {diff?.removed_event_ids?.length ?? 0}; изменено: {diff?.changed_event_ids?.length ?? 0}.</p>
    <p>Провайдер: {draft.generation_run.output.backend_id}; версия: {draft.generation_run.output.backend_version}; seed: {draft.generation_run.output.seed}.</p>
    {reasons.map((reason, index) => <p role="alert" key={index}>{reason}</p>)}
    <h4>Диагностика проверки</h4>{!draft.validation?.length && !draft.generation_run.output.diagnostics?.length ? <p>Диагностических сообщений нет.</p> : <><h5>Проверка карты</h5>{draft.validation?.length ? <ul>{draft.validation.map((diagnostic, index) => <li key={`chart-${index}`}>{diagnostic.severity}: {diagnostic.code}: {diagnostic.message}; события: {(diagnostic.event_ids ?? []).join(', ') || 'нет'}; ноты: {(diagnostic.note_ids ?? []).join(', ') || 'нет'}</li>)}</ul> : <p>Сообщений нет.</p>}<h5>Проверка провайдера</h5>{draft.generation_run.output.diagnostics?.length ? <ul>{draft.generation_run.output.diagnostics.map((diagnostic, index) => <li key={`provider-${index}`}>{diagnostic.severity}: {diagnostic.code}: {diagnostic.message}; события: {(diagnostic.event_ids ?? []).join(', ') || 'нет'}; ноты: {(diagnostic.note_ids ?? []).join(', ') || 'нет'}</li>)}</ul> : <p>Сообщений нет.</p>}</>}
    {!diff && <p role="alert">Список изменений отсутствует.</p>}
    {diff && entries.length === 0 && <p>Изменений нет.</p>}
    {entries.slice(currentPage * PAGE_SIZE, (currentPage + 1) * PAGE_SIZE).map((entry, index) => <div key={`${entry.kind}-${entry.id}-${index}`}><h4>{entry.kind}: {entry.id || 'ID отсутствует'}</h4>{entry.error && <p role="alert">{entry.error}</p>}{entry.before && eventDetails(entry.before, 'До')}{entry.after && eventDetails(entry.after, 'После')}</div>)}
    {entries.length > PAGE_SIZE && <nav aria-label="Страницы изменений"><button disabled={currentPage === 0} onClick={() => setPage(currentPage - 1)}>Назад</button> Страница {currentPage + 1} из {Math.ceil(entries.length / PAGE_SIZE)} <button disabled={(currentPage + 1) * PAGE_SIZE >= entries.length} onClick={() => setPage(currentPage + 1)}>Далее</button></nav>}
  </section>;
}
