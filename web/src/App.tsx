import RawObservations from './RawObservations';
import { useCallback, useEffect, useRef, useState } from 'react';
import { api, ApiError } from './api';
import { useProject } from './useProject';
import type { SongProject, SongMetadata, Chart, ProjectView } from './generated/ui-contract';
import TimelineEditor from './TimelineEditor';
import { WaveformView } from './WaveformView';
import AudioTransport, { type AudioTransportHandle } from './AudioTransport';
import AudioImportPanel from './AudioImportPanel';
import ArchiveImportPanel from './ArchiveImportPanel';
import SavedJobsPanel from './SavedJobsPanel';
import CoverPanel from './CoverPanel';
import AnalysisPanel from './AnalysisPanel';
import SavedArtifactsPanel from './SavedArtifactsPanel';
import GenerationPanel from './GenerationPanel';
import ExportPanel from './ExportPanel';
import TargetPanel from './TargetPanel';
import DraftPanel, { type DraftAudition } from './DraftPanel';
import GridPanel from './GridPanel';
import MusicalAnchorPanel from './MusicalAnchorPanel';
import GridSuggestionsPanel from './GridSuggestionsPanel';
import { finalTime } from './timeline';
import './style.css';
import type { ClipboardEntry } from './notesClipboard';

const message = (e: unknown) => e instanceof ApiError ? `${e.status}: ${typeof e.detail === 'string' ? e.detail : JSON.stringify(e.detail)}` : e instanceof Error ? e.message : String(e);
function SongForm({ metadata, disabled, save }: { metadata: SongMetadata; disabled: boolean; save: (v: SongMetadata) => void }) {
  const [value, setValue] = useState(metadata);
  const dirty = useRef(new Set<keyof SongMetadata>());
  const canonical = useRef(metadata);
  useEffect(() => {
    const previous = canonical.current;
    (['title', 'artist', 'mapper', 'subtitle', 'explicit'] as const).forEach(key => {
      if (Object.is(previous[key], metadata[key])) return;
      if (dirty.current.has(key) && Object.is(value[key], metadata[key])) dirty.current.delete(key);
      if (!dirty.current.has(key)) setValue(current => ({ ...current, [key]: metadata[key] }));
    });
    canonical.current = metadata;
  }, [metadata, value]);
  const change = <K extends keyof SongMetadata>(key: K, next: SongMetadata[K]) => {
    setValue(current => ({ ...current, [key]: next }));
    if (Object.is(next, canonical.current[key])) dirty.current.delete(key);
    else dirty.current.add(key);
  };
  return <form onSubmit={e => { e.preventDefault(); save({ ...metadata, ...Object.fromEntries([...dirty.current].map(key => [key, value[key]])) }); }}><h3>Песня</h3>{(['title', 'artist', 'mapper', 'subtitle'] as const).map((key, i) => <label key={key}>{['Название', 'Исполнитель', 'Автор карты', 'Подзаголовок'][i]}<input disabled={disabled} value={value[key] ?? ''} onChange={e => change(key, e.target.value)}/></label>)}<label className="check"><input type="checkbox" disabled={disabled} checked={value.explicit ?? false} onChange={e => change('explicit', e.target.checked)}/> Explicit</label><button disabled={disabled}>Сохранить данные песни</button></form>;
}
function ChartForm({ chart, disabled, save }: { chart: Chart; disabled: boolean; save: (v: Record<string, unknown>) => void }) {
  const [name, setName] = useState(chart.metadata?.name ?? ''); const [rank, setRank] = useState(chart.metadata?.rank?.toString() ?? ''); const [speed, setSpeed] = useState(chart.metadata?.speed?.toString() ?? '');
  const dirty = useRef(new Set<'name' | 'rank' | 'speed'>());
  const canonical = useRef({ name: chart.metadata?.name ?? '', rank: chart.metadata?.rank?.toString() ?? '', speed: chart.metadata?.speed?.toString() ?? '' });
  useEffect(() => {
    const next = { name: chart.metadata?.name ?? '', rank: chart.metadata?.rank?.toString() ?? '', speed: chart.metadata?.speed?.toString() ?? '' };
    (['name', 'rank', 'speed'] as const).forEach(key => {
      if (canonical.current[key] === next[key]) return;
      const current = key === 'name' ? name : key === 'rank' ? rank : speed;
      if (dirty.current.has(key) && current === next[key]) dirty.current.delete(key);
      if (!dirty.current.has(key)) (key === 'name' ? setName : key === 'rank' ? setRank : setSpeed)(next[key]);
    });
    canonical.current = next;
  }, [chart.metadata, name, rank, speed]);
  const change = (key: 'name' | 'rank' | 'speed', next: string) => {
    (key === 'name' ? setName : key === 'rank' ? setRank : setSpeed)(next);
    if (next === canonical.current[key]) dirty.current.delete(key); else dirty.current.add(key);
  };
  return <form onSubmit={e => { e.preventDefault(); const patch: Record<string, unknown> = {}; if (dirty.current.has('name')) patch.name = name; if (dirty.current.has('rank')) patch.rank = rank === '' ? null : Number(rank); if (dirty.current.has('speed')) patch.speed = speed === '' ? null : Number(speed); save(patch); }}><h3>Параметры карты</h3><label>Название<input disabled={disabled} value={name} onChange={e => change('name', e.target.value)}/></label><label>Rank<input type="number" min="1" max="10" disabled={disabled} value={rank} onChange={e => change('rank', e.target.value)}/></label><label>Speed<input type="number" step="any" min="0.01" disabled={disabled} value={speed} onChange={e => change('speed', e.target.value)}/></label><button disabled={disabled}>Сохранить карту</button></form>;
}
export default function App() {
  const [auth, setAuth] = useState<boolean | null>(null); const [password, setPassword] = useState(''); const [err, setErr] = useState<unknown>(null); const generation = useRef(0);
  useEffect(() => { const gen = ++generation.current; fetch('/api/session', { credentials: 'same-origin' }).then(async r => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); }).then(v => { if (generation.current === gen) setAuth(v.authenticated); }).catch(e => { if (generation.current === gen) { setErr(e); setAuth(false); } }); return () => { generation.current++; }; }, []);
  const lostSession = () => { generation.current++; setAuth(false); setErr(null); };
  if (auth === null) return <main className="login">Проверка сеанса…</main>;
  if (!auth) return <main className="login"><form onSubmit={async e => { e.preventDefault(); const gen = generation.current; try { await api.login(password); if (generation.current !== gen) return; setPassword(''); setErr(null); setAuth(true); } catch (e) { if (generation.current === gen) setErr(e); } }}><h1>AI-EDDA</h1><p>Вход в редактор</p><label>Пароль<input type="password" value={password} onChange={e => setPassword(e.target.value)} autoFocus/></label><button>Войти</button>{err != null && <p role="alert">Ошибка: {message(err)}</p>}</form></main>;
  return <Workspace key={generation.current} onSessionLost={lostSession} />;
}
function Workspace({ onSessionLost }: { onSessionLost: () => void }) {
  const [navigation, setNavigation] = useState<{ chartId: string; notes: string[] } | null>(null);
  const [clipboard, setClipboard] = useState<ClipboardEntry[]>([]);
  const [viewport, setViewport] = useState({ start: 0, span: 10 }); const [position, setPosition] = useState(0); const [loop, setLoop] = useState<[number, number] | null>(null);
  const [draftId, setDraftId] = useState<string | null>(null); const [audition, setAudition] = useState<DraftAudition | null>(null);
  const [recovery, setRecovery] = useState<{ project: string; kind: 'audio_import' | 'cover_import'; id: string; nonce: number } | null>(null);
  const transport = useRef<AudioTransportHandle>(null); const p = useProject(() => transport.current?.pause()); const [projects, setProjects] = useState<SongProject[]>([]); const [err, setErr] = useState<unknown>(null); const [title, setTitle] = useState(''); const [busy, setBusy] = useState(false); const [logoutBusy, setLogoutBusy] = useState(false); const creation = useRef<{ id: string; title: string } | null>(null); const lifetime = useRef(0); const listOrder = useRef(0);
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.code !== 'Space' || event.defaultPrevented || event.repeat || event.altKey || event.ctrlKey || event.metaKey || event.shiftKey || !transport.current) return;
      const target = event.target;
      if (target instanceof Element && (target.closest('input, textarea, select, button, a, [contenteditable]:not([contenteditable="false"])') || (target instanceof HTMLElement && target.isContentEditable))) return;
      event.preventDefault();
      transport.current.toggle();
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, []);
  useEffect(() => { const gen = ++lifetime.current; return () => { if (lifetime.current === gen) lifetime.current++; listOrder.current++; }; }, []);
  const active = (gen: number) => lifetime.current === gen;
  useEffect(() => { if (p.error instanceof ApiError && p.error.status === 401) onSessionLost(); }, [p.error, onSessionLost]);
  const refresh = async () => { const gen = lifetime.current; const order = ++listOrder.current; const current = () => active(gen) && listOrder.current === order; try { const result = await api.list(); if (current()) setProjects(result); } catch (e) { if (current() && e instanceof ApiError && e.status === 401) onSessionLost(); else if (current()) setErr(e); } };
  useEffect(() => { void refresh(); }, []);
  const create = async () => { if (!creation.current) creation.current = { id: crypto.randomUUID(), title }; const request = creation.current; const gen = lifetime.current; setBusy(true); try { const v = await api.create(request.id, { title: request.title }); if (!active(gen) || creation.current !== request) return; creation.current = null; setTitle(''); setErr(null); await refresh(); if (active(gen) && v.project.id) await p.open(v.project.id); } catch (e) { if (active(gen) && e instanceof ApiError && e.status === 401) onSessionLost(); else if (active(gen)) { setErr(e); if (e instanceof ApiError && e.status < 500) creation.current = null; } } finally { if (active(gen)) setBusy(false); } };
  const logout = async () => { const gen = lifetime.current; setLogoutBusy(true); try { await api.logout(); if (active(gen)) onSessionLost(); } catch (e) { if (active(gen) && e instanceof ApiError && e.status === 401) onSessionLost(); else if (active(gen)) { setErr(e); setLogoutBusy(false); } } };
  const stopAudition = useCallback(() => { transport.current?.pause(); setAudition(null); }, []);
  const mutate: typeof p.mutate = (...args) => { stopAudition(); return p.mutate(...args); };
  useEffect(() => { stopAudition(); }, [p.view?.project.revision, p.selectedChart, p.view?.project.id, p.view?.project.audio?.sha256, p.view?.project.audio?.path]);
  useEffect(() => { setDraftId(null); setClipboard([]); }, [p.view?.project.id]);
  const charts = p.view?.charts ?? []; const selected = charts.find(c => c.id === p.selectedChart) ?? charts[0] ?? null;
  const onGenerationDraft = useCallback((id: string | null) => { if (!id) stopAudition(); setDraftId(id); }, [stopAudition]);
  useEffect(() => { setViewport({ start: 0, span: 10 }); setPosition(0); setLoop(null); }, [p.view?.project.id, p.view?.project.audio?.sha256]);
  const audioDuration = p.view?.project.audio?.duration_sec;
  const timelineDuration = Number.isFinite(audioDuration) && (audioDuration ?? 0) > 0
    ? audioDuration!
    : Math.max(30, ...((selected?.events ?? []).map(event => finalTime(p.view!.grid, event.time_ref) + 10)));
  useEffect(() => { setNavigation(null); }, [p.view?.project.id, p.view?.project.revision, p.view?.grid.revision]);
  const navigate = (target: { chartId: string; kind: 'event' | 'note'; id: string; view: ProjectView }) => {
    if (!p.view || target.view.project.id !== p.view.project.id) return;
    const source = target.view.charts.find(c => c.id === target.chartId);
    const live = p.view?.charts.find(c => c.id === target.chartId);
    const event = source?.events?.find(e => target.kind === 'event' ? e.id === target.id : e.notes?.some(n => n.id === target.id));
    const liveEvent = live?.events?.find(e => e.id === event?.id && (target.kind === 'event' || e.notes?.some(n => n.id === target.id)));
    if (!event || !liveEvent) return;
    const seconds = finalTime(target.view.grid, event.time_ref);
    if (!Number.isFinite(seconds)) return;
    const notes = (liveEvent.notes ?? []).flatMap(n => n.id && (target.kind === 'event' || n.id === target.id) ? [n.id] : []);
    stopAudition(); p.selectChart(target.chartId); setNavigation({ chartId: target.chartId, notes });
    setViewport(v => ({ ...v, start: Math.max(0, seconds - v.span / 2) }));
    setPosition(seconds); transport.current?.seek(seconds);
  };
  const mutationsDisabled = !p.canWrite || p.status === 'saving' || p.canRetry;
  const status = !p.canWrite ? 'только чтение' : ({ saved: 'сохранено', saving: 'сохранение', error: 'ошибка', readonly: 'только чтение' }[p.status] ?? p.status);
  const hiddenResolvedLeaseError = p.canWrite && p.error instanceof ApiError && (p.error.detail === 'Writer lease unavailable' || p.error.detail === 'Writer lease required');
    return <main><header><h1>AI-EDDA</h1><span>Состояние: {status}</span><button disabled={logoutBusy} onClick={() => void logout()}>Выйти</button></header>
    <div className="layout"><aside><section><h2>Проекты</h2><form onSubmit={e => { e.preventDefault(); void create(); }}><input aria-label="Название проекта" disabled={busy || creation.current !== null} value={title} onChange={e => setTitle(e.target.value)}/><button disabled={busy || !title.trim()}>{creation.current ? 'Повторить создание' : 'Создать проект'}</button></form>{err != null && <p role="alert">Ошибка: {message(err)} <button onClick={() => void refresh()}>Повторить</button></p>}{projects.map(project => <button key={project.id} onClick={() => void p.open(project.id!)}>{project.metadata?.title ?? project.id}</button>)}</section><ArchiveImportPanel onComplete={() => void refresh()} onOpen={id => void p.open(id)} onSessionLost={onSessionLost}/><SavedJobsPanel currentProjectId={p.view?.project.id} writerHeaders={p.writerHeaders} onOpenProject={id => void p.open(id)} onSessionLost={onSessionLost} onRecover={(kind, id) => { if (p.view?.project.id) setRecovery({ project: p.view.project.id, kind, id, nonce: Date.now() }); }}/><section><h2>Карты</h2><button disabled={mutationsDisabled || !p.view} onClick={() => void mutate('create_chart', { name: 'Новая карта' })}>Создать карту</button><div className="chart-list">{charts.map((chart, index) => <div key={chart.id}><button className={selected?.id === chart.id ? 'active' : ''} onClick={() => p.selectChart(chart.id!)}>{chart.metadata?.name?.trim() || ('Карта ' + (index + 1))}</button><div className="actions"><button disabled={mutationsDisabled} onClick={() => void mutate('duplicate_chart', { chart_id: chart.id })}>Дублировать</button><button disabled={mutationsDisabled} onClick={() => void mutate('delete_chart', { chart_id: chart.id })}>Удалить</button></div></div>)}</div></section></aside><section className="workspace">
    {p.error != null && !hiddenResolvedLeaseError && <p role="alert">Ошибка проекта: {message(p.error)} {p.canRetry && <button onClick={() => { transport.current?.pause(); void p.retry(); }}>Повторить</button>}</p>}{p.view && !p.canWrite && <p>Только чтение <button onClick={() => void p.takeover()}>Забрать управление</button></p>}
    {p.view && selected ? <><h2>{selected.metadata?.name?.trim() || ('Карта ' + (charts.indexOf(selected) + 1))}</h2><p>Двойной щелчок добавляет ноту. Выберите ноту и перетащите её или используйте стрелки.</p>{p.view.project.audio && p.view.project.id && <AudioTransport key={`${p.view.project.id}:${p.view.project.audio.sha256}`} ref={transport} projectId={p.view.project.id} audio={p.view.project.audio} grid={audition?.grid ?? p.view.grid} chart={audition?.chart ?? selected} onPositionChange={setPosition} onLoopChange={setLoop}/>}{audition && <p>Прослушивание черновика <button onClick={stopAudition}>Выйти</button></p>}<TimelineEditor onNavigate={navigate} highlight={navigation?.chartId === selected.id ? navigation?.notes : undefined} view={p.view} entries={clipboard} onCopy={setClipboard} viewport={viewport} onViewportChange={setViewport} position={position} waveform={p.view.project.audio && p.view.project.id ? <><RawObservations view={p.view} viewport={viewport}/><WaveformView projectId={p.view.project.id} audio={p.view.project.audio} viewport={viewport} position={position} loop={loop ?? undefined} onSeek={seconds => transport.current?.seek(seconds)}/></> : undefined} onSeek={seconds => transport.current?.seek(seconds)} key={selected.id} chart={audition?.chart ?? selected} grid={audition?.grid ?? p.view.grid} duration={timelineDuration} disabled={mutationsDisabled || !!audition} mutate={mutate}/></> : <p className="empty">Выберите проект и карту</p>}</section><aside className="inspector">{p.view && <><GridSuggestionsPanel key={`grid-suggestions:${p.view.project.id}`} view={p.view} disabled={mutationsDisabled} mutate={mutate} preview={p.preview}/><GridPanel key={`grid:${p.view.project.id}`} view={p.view} disabled={mutationsDisabled} mutate={mutate} preview={p.preview}/><MusicalAnchorPanel key={`musical-anchors:${p.view.project.id}`} view={p.view} selectedChartId={selected?.id ?? ''} disabled={mutationsDisabled} mutate={mutate} preview={p.preview} onSeek={seconds => transport.current?.seek(seconds)}/><SavedArtifactsPanel key={`saved:${p.view.project.id}`} view={p.view} disabled={mutationsDisabled} mutate={mutate} onDraft={id => { stopAudition(); setDraftId(id); }}/><AnalysisPanel key={`analysis:${p.view.project.id}`} view={p.view} disabled={mutationsDisabled} writerHeaders={p.writerHeaders} mutate={mutate}/>{selected && <TargetPanel key={`target:${p.view.project.id}`} view={p.view} selectedChartId={selected.id!} disabled={mutationsDisabled} mutate={mutate} preview={p.preview}/>}<ExportPanel key={`export:${p.view.project.id}`} view={p.view} disabled={mutationsDisabled} writerHeaders={p.writerHeaders}/><GenerationPanel key={`generation:${p.view.project.id}`} view={p.view} selectedChartId={selected?.id ?? ''} disabled={mutationsDisabled} writerHeaders={p.writerHeaders} onDraft={onGenerationDraft}/>{draftId && <DraftPanel key={`draft:${p.view.project.id}`} view={p.view} draftId={draftId} disabled={mutationsDisabled} writerHeaders={p.writerHeaders} mutate={mutate} onDecisionStart={stopAudition} onSeek={seconds => transport.current?.seek(seconds)} onAudition={value => { stopAudition(); setAudition(value); }}/>}<CoverPanel key={`cover:${p.view.project.id}:${recovery?.project === p.view.project.id && recovery?.kind === 'cover_import' ? recovery?.nonce : ''}`} initialJobId={recovery?.project === p.view.project.id && recovery?.kind === 'cover_import' ? recovery?.id : undefined} view={p.view} disabled={mutationsDisabled} writerHeaders={p.writerHeaders} mutate={mutate}/><AudioImportPanel key={`audio-import:${p.view.project.id}:${recovery?.project === p.view.project.id && recovery?.kind === 'audio_import' ? recovery?.nonce : ''}`} initialJobId={recovery?.project === p.view.project.id && recovery?.kind === 'audio_import' ? recovery?.id : undefined} view={p.view} disabled={mutationsDisabled} writerHeaders={p.writerHeaders} mutate={mutate} preview={p.preview}/>{selected && <ChartForm key={`chart-form:${p.view.project.id}:${selected.id}`} chart={selected} disabled={mutationsDisabled} save={v => void mutate('update_chart_metadata', { chart_id: selected.id, ...v })}/>}<SongForm key={`song:${p.view.project.id}`} metadata={p.view.project.metadata ?? {}} disabled={mutationsDisabled} save={v => void mutate('update_song_metadata', v as Record<string, unknown>)}/></>}</aside></div>
  </main>;
}















