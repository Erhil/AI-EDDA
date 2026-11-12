import { useEffect, useRef, useState } from 'react';
import { getPlan } from './generationClient';
import type { MusicalAnchor, MusicalPlan, ProjectView } from './generated/ui-contract';

type Props = { view: ProjectView; selectedChartId: string; disabled: boolean; preview: (kind: string, payload: Record<string, unknown>) => Promise<ProjectView>; mutate: (kind: string, payload: Record<string, unknown>, base?: ProjectView) => Promise<unknown>; onSeek?: (seconds: number) => void };
type Row = { anchor: MusicalAnchor; time: string; tolerance: string };
export default function MusicalAnchorPanel({ view, selectedChartId, disabled, preview, mutate, onSeek }: Props) {
  const key = JSON.stringify([view.project, view.grid.revision, view.charts.map(c => [c.id, c.revision]), selectedChartId]);
  const current = useRef(key); current.current = key;
  const lifetime = useRef(0); const lock = useRef(false);
  const [plan, setPlan] = useState<MusicalPlan | null>(null); const [rows, setRows] = useState<Row[]>([]);
  const [error, setError] = useState(''); const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<{ payload: Record<string, unknown>; base: ProjectView; key: string } | null>(null);
  useEffect(() => {
    const generation = ++lifetime.current; const ac = new AbortController(); lock.current = false; setPlan(null); setResult(null); setError(''); setBusy(false);
    const p = view.project;
    if (p.id && p.analysis && p.audio) void getPlan(p.id, ac.signal).then(data => {
      if (ac.signal.aborted || lifetime.current !== generation || current.current !== key) return;
      if (data.project_id !== p.id || data.project_revision !== p.revision || data.grid_revision !== p.grid_revision || JSON.stringify(data.analysis) !== JSON.stringify(p.analysis) || JSON.stringify(data.plan_inputs) !== JSON.stringify(p.plan_inputs ?? null) || JSON.stringify(data.plan.analysis) !== JSON.stringify(p.analysis) || data.plan.grid_revision !== p.grid_revision || data.plan.audio_sha256 !== p.audio?.sha256 || data.plan.audio_asset_id !== p.audio?.path.match(/^audio\/([^/]+)\/song\.ogg$/)?.[1]) throw new Error('Состояние проекта изменилось. Обновите проект.');
      setPlan(data.plan); setRows((data.plan.musical_anchors ?? []).map(anchor => ({ anchor, time: String(anchor.time_sec), tolerance: String(anchor.tolerance_sec ?? 0) })));
    }).catch(e => { if (!ac.signal.aborted && lifetime.current === generation && current.current === key) setError(String(e)); });
    return () => { ac.abort(); if (lifetime.current === generation) lifetime.current++; };
  }, [key]);
  const change = (next: Row[]) => { setRows(next); setResult(null); };
  const run = async () => {
    if (lock.current || disabled || !plan) return; lock.current = true;
    const generation = lifetime.current; const base = view;
    const anchors = rows.map(r => ({ ...r.anchor, time_sec: Number(r.time), tolerance_sec: Number(r.tolerance) }));
    const ids = new Set<string>(); const charts = new Set(view.charts.map(c => c.id));
    if (rows.some((r, i) => !r.time.trim() || !r.tolerance.trim() || !Number.isFinite(anchors[i].time_sec) || anchors[i].time_sec < 0 || anchors[i].time_sec >= (view.project.audio?.duration_sec ?? 0) || !Number.isFinite(anchors[i].tolerance_sec) || anchors[i].tolerance_sec < 0 || !r.anchor.id || ids.has(r.anchor.id) || (ids.add(r.anchor.id), false) || new Set(r.anchor.target_scope ?? []).size !== (r.anchor.target_scope ?? []).length || (r.anchor.target_scope ?? []).some(id => !charts.has(id)))) { setError('Проверьте время, допуск и выбранные карты.'); lock.current = false; return; }
    const payload = { anchors: structuredClone(anchors) }; setBusy(true); setError(''); setResult(null);
    try { const next = await preview('set_musical_anchors', payload); if (generation !== lifetime.current || current.current !== key) return; if (JSON.stringify(next.project) !== JSON.stringify({ ...base.project, plan_inputs: next.project.plan_inputs }) || JSON.stringify(next.grid) !== JSON.stringify(base.grid) || JSON.stringify(next.charts) !== JSON.stringify(base.charts)) throw new Error('Состояние проекта изменилось.'); setResult({ payload, base, key }); }
    catch (e) { if (generation === lifetime.current && current.current === key) setError(String(e)); } finally { if (generation === lifetime.current) { lock.current = false; setBusy(false); } }
  };
  return <section><h3>Музыкальные опорные точки</h3>{!plan ? <p>{error || (view.project.analysis && view.project.audio ? 'Загрузка плана…' : 'Для плана нужны аудио и анализ.')}</p> : <><p>План всей песни: разделов {plan.sections.length}, ритмических позиций {plan.rhythm_sites?.length ?? 0}.</p><p>Мягкие точки сохраняются как предпочтения. Текущий генератор времени их не учитывает. Обязательные точки применяются к выбранным картам.</p>{rows.map((r, i) => <fieldset key={r.anchor.id}><legend>Точка {i + 1}</legend><label>Время, с<input type="number" step="any" value={r.time} disabled={disabled || busy} onChange={e => change(rows.map((x, j) => i === j ? { ...x, time: e.target.value } : x))}/></label><label>Допуск, с<input type="number" step="any" value={r.tolerance} disabled={disabled || busy} onChange={e => change(rows.map((x, j) => i === j ? { ...x, tolerance: e.target.value } : x))}/></label><label>Сила<select value={r.anchor.strength ?? 'soft'} disabled={disabled || busy} onChange={e => change(rows.map((x, j) => i === j ? { ...x, anchor: { ...x.anchor, strength: e.target.value as 'soft' | 'required' } } : x))}><option value="soft">Мягкая</option><option value="required">Обязательная</option></select></label><p>Карты: пустой выбор означает все карты.</p>{view.charts.map((c, index) => <label key={c.id}><input type="checkbox" disabled={disabled || busy} checked={(r.anchor.target_scope ?? []).includes(c.id!)} onChange={e => change(rows.map((x, j) => i === j ? { ...x, anchor: { ...x.anchor, target_scope: e.target.checked ? [...(x.anchor.target_scope ?? []), c.id!] : (x.anchor.target_scope ?? []).filter(id => id !== c.id) } } : x))}/>{c.metadata?.name?.trim() || ('Карта ' + (index + 1))}</label>)}{onSeek && <button onClick={() => onSeek(Number(r.time))}>Перейти</button>}<button disabled={disabled || busy} onClick={() => change(rows.filter((_, j) => i !== j))}>Удалить</button></fieldset>)}<button disabled={disabled || busy} onClick={() => change([...rows, { anchor: { id: crypto.randomUUID(), time_sec: 0, tolerance_sec: 0, strength: 'soft', target_scope: [], provenance: { source_origin: 'manual' } }, time: '0', tolerance: '0' }])}>Добавить точку</button><button disabled={disabled || busy} onClick={() => void run()}>Предпросмотр</button>{result?.key === key && <button disabled={disabled || busy} onClick={() => { if (lock.current) return; lock.current = true; const generation = lifetime.current; setBusy(true); void mutate('set_musical_anchors', result.payload, result.base).catch(e => { if (generation === lifetime.current && current.current === key) setError(String(e)); }).finally(() => { if (generation === lifetime.current) { lock.current = false; setBusy(false); } }); }}>Применить показанные изменения</button>}{error && <p role="alert">{error}</p>}</>}</section>;
}
