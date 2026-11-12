import { useEffect, useRef, useState } from 'react';
import DraftDetails from './DraftDetails';
import { inspectDraft, rejectDraft } from './generationClient';
import type { Chart, ProjectView, TimingGrid } from './generated/ui-contract';
import type { WriterHeaders } from './uploads';

type Inspection = Awaited<ReturnType<typeof inspectDraft>>;
export type DraftAudition = { projectId: string; draftId: string; chart: Chart; grid: TimingGrid; audioAssetId: string; audioSha256: string };
type Props = { view: ProjectView; draftId: string; disabled: boolean; writerHeaders: () => WriterHeaders | null; mutate: (kind: string, payload: Record<string, unknown>, base?: ProjectView) => Promise<unknown>; onAudition?: (value: DraftAudition) => void; onSeek?: (seconds: number) => void; onDecisionStart?: () => void };
const normalized = (value: unknown): string => {
  if (Array.isArray(value)) return `[${value.map(normalized).join(',')}]`;
  if (value && typeof value === 'object') return `{${Object.entries(value).sort(([a], [b]) => a.localeCompare(b)).map(([k, v]) => `${JSON.stringify(k)}:${normalized(v)}`).join(',')}}`;
  return JSON.stringify(value) ?? 'null';
};
const chartContent = (chart: Chart) => { const { revision: _revision, grid_revision: _grid, ...rest } = chart; return normalized(rest); };

export default function DraftPanel({ view, draftId, disabled, writerHeaders, mutate, onAudition, onSeek, onDecisionStart }: Props) {
  const projectId = view.project.id ?? '';
  const revision = view.project.revision;
  const key = `${projectId}:${draftId}:${revision}`;
  const [result, setResult] = useState<{ key: string; value: Inspection } | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [reload, setReload] = useState(0);
  const [pending, setPending] = useState<{ owner: string; token: string } | null>(null);
  const pendingRef = useRef<{ owner: string; token: string } | null>(null);
  pendingRef.current = pending;
  const lock = useRef<{ owner: string; token: string } | null>(null);
  const mounted = useRef(true);
  const rejectOperation = useRef<{ owner: string; token: string; controller: AbortController } | null>(null);
  const identity = useRef('');
  const decision = useRef({ key: '', id: '' });
  const attempt = useRef<{ projectId: string; draftId: string; revision: number; chart: Chart } | null>(null);
  const current = useRef(key); current.current = key;
  const decisionKey = `${projectId}:${draftId}`;
  const currentDecision = useRef(decisionKey);
  currentDecision.current = decisionKey;
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; rejectOperation.current?.controller.abort(); rejectOperation.current = null; lock.current = null; }; }, []);
  useEffect(() => {
    const operation = rejectOperation.current;
    if (operation && operation.owner !== decisionKey) {
      operation.controller.abort();
      if (rejectOperation.current?.token === operation.token) rejectOperation.current = null;
      if (lock.current?.token === operation.token) lock.current = null;
      if (pendingRef.current?.token === operation.token) { pendingRef.current = null; setPending(null); }
    }
  }, [decisionKey]);
  const inspection = result?.key === key ? result.value : null;
  useEffect(() => {
    const controller = new AbortController();
    setResult(null); setError('');
    if (!projectId || !draftId) { setLoading(false); return () => controller.abort(); }
    setLoading(true);
    void inspectDraft(projectId, draftId, controller.signal).then(value => {
      if (!controller.signal.aborted && current.current === key) setResult({ key, value });
    }).catch(e => { if (!controller.signal.aborted && current.current === key) setError(String(e)); })
      .finally(() => { if (!controller.signal.aborted && current.current === key) setLoading(false); });
    return () => controller.abort();
  }, [projectId, draftId, revision, reload, key]);
  const busy = pending?.owner === decisionKey;
  if (identity.current !== decisionKey) { identity.current = decisionKey; attempt.current = null; }
  const applied = !!attempt.current && attempt.current.projectId === projectId && attempt.current.draftId === draftId && revision !== undefined && revision > attempt.current.revision && Object.values(view.charts).some(chart => chart.id === attempt.current!.chart.id && chartContent(chart) === chartContent(attempt.current!.chart));
  const request = inspection?.draft.generation_run.request;
  const audio = view.project.audio;
  const parts = audio?.path.split('/');
  const sameAudio = !!request && !!audio && parts?.length === 3 && parts[0] === 'audio' && parts[2] === 'song.ogg' && parts[1] === request.audio_asset_id && audio.sha256 === request.audio_sha256;
  async function apply() {
    if (lock.current?.owner === decisionKey || disabled || !inspection?.applicable || inspection.rejected || revision === undefined) return;
    onDecisionStart?.();
    const operation = { owner: decisionKey, token: crypto.randomUUID() };
    lock.current = operation; setPending(operation);
    attempt.current = { projectId, draftId, revision, chart: inspection.draft.chart };
    try { await mutate('apply_draft', { draft_id: draftId }, view); }
    finally { if (mounted.current) { if (lock.current?.token === operation.token) lock.current = null; setPending(value => value?.token === operation.token ? null : value); } }
  }
  async function reject() {
    if (lock.current?.owner === decisionKey || disabled || !inspection || inspection.rejected) return;
    const headers = writerHeaders(); if (!headers) return;
    onDecisionStart?.();
    if (decision.current.key !== decisionKey) decision.current = { key: decisionKey, id: crypto.randomUUID() };
    const operation = { owner: decisionKey, token: crypto.randomUUID() };
    const controller = new AbortController();
    rejectOperation.current = { ...operation, controller };
    lock.current = operation; setPending(operation); setError('');
    const live = () => mounted.current && !controller.signal.aborted && currentDecision.current === operation.owner && rejectOperation.current?.token === operation.token && lock.current?.token === operation.token;
    try { await rejectDraft(projectId, draftId, decision.current.id, headers, controller.signal); if (live()) { setResult(null); setReload(n => n + 1); } }
    catch (e) { if (live()) setError(String(e)); }
    finally { if (live()) { rejectOperation.current = null; lock.current = null; setPending(value => value?.token === operation.token ? null : value); } }
  }
  return <section><h2>Черновик</h2>
    {(loading || (!inspection && !error)) && <p>Загрузка черновика…</p>}
    {error && <p role="alert">Не удалось выполнить запрос: {error} <button onClick={() => setReload(n => n + 1)}>Повторить загрузку</button></p>}
    {applied && <p>Черновик применён.</p>}
    {inspection && <><DraftDetails key={decisionKey} draft={inspection.draft} baseChart={inspection.base_chart} grid={inspection.grid} reasons={inspection.reasons} onSeek={sameAudio ? onSeek : undefined} />
      {inspection.rejected && <p>Черновик отклонён.</p>}
      <button disabled={disabled || busy || !inspection.applicable || inspection.rejected || applied} onClick={() => void apply()}>Применить</button>
      <button disabled={disabled || busy || inspection.rejected || applied} onClick={() => void reject()}>Отклонить</button>
      {onAudition && <button disabled={!sameAudio} onClick={() => { if (sameAudio && request) onAudition({ projectId, draftId, chart: inspection.draft.chart, grid: inspection.grid, audioAssetId: request.audio_asset_id, audioSha256: request.audio_sha256 }); }}>Прослушать</button>}
    </>}
  </section>;
}
