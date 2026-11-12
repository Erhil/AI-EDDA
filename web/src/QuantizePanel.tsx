import DiagnosticsList from './DiagnosticsList';
import { useEffect, useRef, useState } from 'react';
import type { ProjectView } from './generated/ui-contract';
import { getValidation, previewValidation, type ValidationResponse } from './validationClient';
import { finalTime } from './timeline';

type Target = { chartId: string; kind: 'event' | 'note'; id: string; view: ProjectView };
type Props = { onNavigate?: (target: Target) => void; view: ProjectView; chartId: string; eventIds: string[]; disabled: boolean; mutate: (kind: string, payload: Record<string, unknown>, base: ProjectView) => Promise<unknown> };
type Preview = { key: string; base: ProjectView; payload: { chart_id: string; event_ids: string[]; grid_division: number }; response: ValidationResponse };
const revisionKey = (v: ProjectView) => JSON.stringify([v.project.id, v.project.revision, v.project.grid_revision, v.project.chart_heads, v.grid.revision, v.charts.map(c => [c.id, c.revision])]);
export default function QuantizePanel({ view, chartId, eventIds, disabled, mutate, onNavigate }: Props) {
  const [rowPage, setRowPage] = useState(0);
  const [division, setDivision] = useState(4);
  const [current, setCurrent] = useState<ValidationResponse | null>(null);
  const [result, setResult] = useState<Preview | null>(null);
  const [busy, setBusy] = useState(false);
  const [applying, setApplying] = useState(false);
  const [error, setError] = useState('');
  const ids = [...new Set(eventIds)].sort();
  const canonical = revisionKey(view);
  const key = JSON.stringify([canonical, chartId, ids, division]);
  const latest = useRef(key); const generation = useRef(0); const request = useRef<AbortController | null>(null);
  const operation = useRef<object | null>(null);
  if (latest.current !== key) { latest.current = key; generation.current++; request.current?.abort(); request.current = null; }
  useEffect(() => { setResult(null); setBusy(false); setError(''); setRowPage(0); }, [key]);
  useEffect(() => {
    const controller = new AbortController(); let active = true;
    setCurrent(null);
    if (view.project.id) void getValidation(view.project.id, controller.signal).then(r => { if (active && revisionKey(r.view) === canonical) setCurrent(r); }).catch(e => { if (active && !controller.signal.aborted) setError(String(e)); });
    return () => { active = false; controller.abort(); };
  }, [canonical]);
  useEffect(() => () => { generation.current++; request.current?.abort(); operation.current = null; }, []);
  const preview = () => {
    if (disabled || !ids.length || !view.project.id) return;
    request.current?.abort(); const controller = new AbortController(); request.current = controller;
    const token = ++generation.current; const base = structuredClone(view);
    const payload = { chart_id: chartId, event_ids: [...ids], grid_division: division };
    setResult(null); setError(''); setBusy(true);
    void previewValidation(view.project.id, 'quantize_events', payload, controller.signal).then(response => {
      if (generation.current !== token || controller.signal.aborted) return;
      if (revisionKey(response.view) !== revisionKey(base)) throw new Error('Состояние проекта изменилось. Повторите предпросмотр.');
      setResult({ key, base, payload, response });
    }).catch(e => { if (generation.current === token && !controller.signal.aborted) setError(String(e)); }).finally(() => { if (generation.current === token && !controller.signal.aborted) { request.current = null; setBusy(false); } });
  };
  const shown = result?.key === key ? result : null;
  const apply = () => {
    if (disabled || !shown || operation.current) return;
    const op = {}; operation.current = op; setApplying(true);
    void Promise.resolve().then(() => mutate('quantize_events', shown.payload, shown.base)).catch(e => { if (operation.current === op && latest.current === key) setError(String(e)); }).finally(() => { if (operation.current === op) { operation.current = null; setApplying(false); } });
  };
  return <section><h3>Квантование</h3><h4>Текущая диагностика</h4><DiagnosticsList key={canonical} response={current} onNavigate={onNavigate}/><label>Деление<select value={division} disabled={disabled} onChange={e => setDivision(Number(e.target.value))}>{[1, 2, 4, 8].map(n => <option key={n} value={n}>{n}</option>)}</select></label><button disabled={disabled || busy || !ids.length} onClick={preview}>Предпросмотр</button>{shown && <><h4>Диагностика предпросмотра</h4><DiagnosticsList key={shown.key} response={shown.response} onNavigate={onNavigate}/>{shown.payload.event_ids.slice(rowPage * 50, rowPage * 50 + 50).map(id => { const before = shown.base.charts.find(c => c.id === chartId)?.events?.find(e => e.id === id); const after = shown.response.view.charts.find(c => c.id === chartId)?.events?.find(e => e.id === id); return before && after ? <p key={id}>{id}: {finalTime(shown.base.grid, before.time_ref).toFixed(3)} → {finalTime(shown.response.view.grid, after.time_ref).toFixed(3)} с</p> : null; })}<p>Событий: {shown.payload.event_ids.length} <button disabled={rowPage === 0} onClick={() => setRowPage(rowPage - 1)}>Назад</button> {rowPage + 1} / {Math.ceil(shown.payload.event_ids.length / 50)} <button disabled={(rowPage + 1) * 50 >= shown.payload.event_ids.length} onClick={() => setRowPage(rowPage + 1)}>Далее</button></p><button disabled={disabled || applying} onClick={apply}>Применить показанные изменения</button></>}{error && <p role="alert">{error}</p>}</section>;
}
