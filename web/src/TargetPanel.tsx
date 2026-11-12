import { useEffect, useRef, useState } from 'react';
import type { GeneratorCapabilities, ProjectView, TargetProfile } from './generated/ui-contract';
import { capabilities } from './generationClient';
import { getTarget } from './targetClient';

type Props = { view: ProjectView; selectedChartId: string; disabled: boolean; mutate: (kind: string, payload: Record<string, unknown>, base?: ProjectView) => Promise<unknown>; preview: (kind: string, payload: Record<string, unknown>) => Promise<ProjectView> };
export default function TargetPanel({ view, selectedChartId, disabled, mutate, preview }: Props) {
  const chart = view.charts.find(c => c.id === selectedChartId);
  const key = JSON.stringify([view.project.id, view.project.revision, selectedChartId, chart?.revision]);
  const current = useRef(key); current.current = key;
  const lifetime = useRef(0); const previewLock = useRef(false); const applyLock = useRef(false);
  const [target, setTarget] = useState<TargetProfile | null>(null); const [caps, setCaps] = useState<GeneratorCapabilities | null>(null);
  const [name, setName] = useState(''); const [divisions, setDivisions] = useState<number[]>([1]); const [rank, setRank] = useState('');
  const [loaded, setLoaded] = useState(false); const [busy, setBusy] = useState(false); const [error, setError] = useState('');
  const [result, setResult] = useState<{ payload: Record<string, unknown>; base: ProjectView; key: string } | null>(null);
  useEffect(() => {
    const generation = ++lifetime.current;
    previewLock.current = false; applyLock.current = false;
    const ac = new AbortController(); setLoaded(false); setResult(null); setBusy(false); setError('');
    if (!chart || !view.project.id) return () => ac.abort();
    void Promise.all([getTarget(view.project.id, selectedChartId, ac.signal), capabilities(ac.signal)]).then(([data, c]) => {
      if (ac.signal.aborted || current.current !== key) return;
      if (data.project_id !== view.project.id || data.project_revision !== view.project.revision || data.chart_id !== selectedChartId || data.chart_revision !== chart.revision || JSON.stringify(data.target_ref) !== JSON.stringify(chart.target_profile ?? null)) throw new Error('Состояние карты изменилось. Обновите проект.');
      setTarget(data.target); setCaps(c); setName(data.target?.name ?? ''); setDivisions(data.target?.allowed_subdivisions ?? [1]); setRank(data.target?.intended_rank == null ? '' : String(data.target.intended_rank)); setLoaded(true);
    }).catch(e => { if (!ac.signal.aborted && current.current === key) setError(String(e)); });
    return () => { ac.abort(); if (lifetime.current === generation) { lifetime.current++; previewLock.current = false; applyLock.current = false; } };
  }, [key]);
  const run = async (clear: boolean) => {
    if (previewLock.current || !loaded || disabled) return;
    previewLock.current = true;
    const generation = lifetime.current;
    const source = view; const sourceKey = key;
    const fields = caps?.target_fields ?? [];
    if (!clear && fields.includes('allowed_subdivisions') && divisions.length === 0) { setError('Выберите хотя бы одно разрешённое деление.'); previewLock.current = false; return; }
    const next: TargetProfile | null = clear ? null : { ...(target ?? {}), id: target?.id ?? crypto.randomUUID(), version: target ? target.version + 1 : 1, name, ...(fields.includes('allowed_subdivisions') ? { allowed_subdivisions: divisions } : {}), intended_rank: rank === '' ? null : Number(rank) };
    const payload = { chart_id: selectedChartId, target: next }; setBusy(true); setError(''); setResult(null);
    try { const response = await preview('set_chart_target', payload); if (lifetime.current !== generation || current.current !== sourceKey) return; if (response.project.id !== source.project.id || response.project.revision !== source.project.revision || response.project.grid_revision !== source.project.grid_revision || JSON.stringify(response.project.chart_heads) !== JSON.stringify(source.project.chart_heads)) throw new Error('Состояние проекта изменилось. Повторите предпросмотр.'); setResult({ payload, base: source, key: sourceKey }); }
    catch (e) { if (lifetime.current === generation && current.current === sourceKey) setError(String(e)); } finally { if (lifetime.current === generation) { previewLock.current = false; if (current.current === sourceKey) setBusy(false); } }
  };
  const changed = () => setResult(null);
  return <section><h3>Цель генерации</h3>{!loaded ? <p>{error || 'Загрузка цели…'}</p> : <><p>{target ? `Сохранена: ${target.name}, версия ${target.version}` : 'Цель не задана.'}</p><label>Название<input disabled={disabled || busy} value={name} onChange={e => { setName(e.target.value); changed(); }}/></label>{caps?.target_fields?.includes('allowed_subdivisions') && <fieldset disabled={disabled || busy}><legend>Разрешённые деления</legend>{[1, 2, 4].map(n => <label key={n}><input type="checkbox" checked={divisions.includes(n)} onChange={e => { setDivisions(e.target.checked ? [...divisions, n].sort((a, b) => a - b) : divisions.filter(x => x !== n)); changed(); }}/>{n}</label>)}</fieldset>}{target?.allowed_subdivisions && !caps?.target_fields?.includes('allowed_subdivisions') && <p>Этот генератор не поддерживает изменение делений. Сохранённое значение будет передано без изменений.</p>}<label>Предпочтительный ранг карты<input type="number" min="0" step="1" disabled={disabled || busy} value={rank} onChange={e => { setRank(e.target.value); changed(); }}/></label><p>Это пожелание. Текущий генератор его не учитывает и не меняет ранг карты.</p><button disabled={disabled || busy || !name.trim() || (rank !== '' && (!Number.isInteger(Number(rank)) || Number(rank) < 0))} onClick={() => void run(false)}>Предпросмотр цели</button>{target && <button disabled={disabled || busy} onClick={() => void run(true)}>Предпросмотр удаления цели</button>}{result?.key === key && <><p>Будет {result.payload.target ? 'сохранена новая версия цели' : 'удалена цель карты'}.</p><button disabled={disabled || busy || applyLock.current} onClick={() => { if (applyLock.current || disabled) return; applyLock.current = true; const generation = lifetime.current; setBusy(true); void mutate('set_chart_target', result.payload, result.base).catch(e => { if (lifetime.current === generation && current.current === key) setError(String(e)); }).finally(() => { if (lifetime.current === generation) { applyLock.current = false; if (current.current === key) setBusy(false); } }); }}>Применить показанные изменения</button></>}</>}{loaded && error && <p role="alert">{error}</p>}</section>;
}
