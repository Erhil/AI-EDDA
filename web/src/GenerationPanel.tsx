import { useEffect, useRef, useState } from 'react';
import type { ProjectView } from './generated/ui-contract';
import type { WriterHeaders } from './uploads';
import { capabilities, getPlan } from './generationClient';
import { captureGeneration, buildGenerationRequest, type GenerationCapture, type GenerationForm } from './generationRequest';
import { GenerationControls } from './GenerationControls';
import { useGenerationJob } from './useGenerationJob';
import { getTarget } from './targetClient';

type Props = { view: ProjectView; selectedChartId: string; disabled: boolean; writerHeaders: () => WriterHeaders | null; onDraft: (id: string | null) => void };
const defaults = (view: ProjectView): GenerationForm => ({ lane: '0', subdivision: '1', seed: '0', startSec: String(Math.max(0, view.grid.segments[0]?.start_time_sec ?? 0)), endSec: String(view.project.audio?.duration_sec ?? '') });
const sources = (view: ProjectView, chartId: string) => JSON.stringify({ project: view.project, grid: view.grid, chart: view.charts.find(c => c.id === chartId), chartId });
export default function GenerationPanel({ view, selectedChartId, disabled, writerHeaders, onDraft }: Props) {
  const [form, setForm] = useState(() => defaults(view));
  const [capture, setCapture] = useState<GenerationCapture | null>(null);
  const captureRef = useRef<GenerationCapture | null>(null);
  const [preparing, setPreparing] = useState(false); const [error, setError] = useState('');
  const attempt = useRef(0); const controller = useRef<AbortController | null>(null);
  const current = useRef({ view, selectedChartId, disabled, writerHeaders }); current.current = { view, selectedChartId, disabled, writerHeaders };
  const job = useGenerationJob(view.project.id ?? '', writerHeaders);
  useEffect(() => () => { attempt.current++; controller.current?.abort(); }, []);
  useEffect(() => { if (job.job?.state === 'completed' && job.draftId) onDraft(job.draftId); }, [job.job?.state, job.draftId, onDraft]);
  const reset = () => { onDraft(null); attempt.current++; controller.current?.abort(); controller.current = null; captureRef.current = null; setCapture(null); setPreparing(false); setError(''); job.reset(); setForm(defaults(current.current.view)); };
  const prepare = async (c: GenerationCapture) => {
    const n = ++attempt.current; controller.current?.abort(); const ac = new AbortController(); controller.current = ac; setPreparing(true); setError('');
    const valid = () => attempt.current === n && !ac.signal.aborted;
    try {
      const chart = c.view.charts.find(item => item.id === c.selectedChartId);
      const [caps, plan, target] = await Promise.all([capabilities(ac.signal), getPlan(c.view.project.id ?? '', ac.signal), chart?.target_profile ? getTarget(c.view.project.id ?? '', c.selectedChartId, ac.signal) : Promise.resolve(null)]);
      if (!valid()) return;
      const request = buildGenerationRequest(c, caps, plan, target);
      const now = current.current;
      if (sources(now.view, now.selectedChartId) !== sources(c.view, c.selectedChartId)) throw new Error('Источники изменились. Начните новый запрос.');
      if (now.disabled || !now.writerHeaders()) throw new Error('Нет доступа для записи.');
      await job.start(request);
    } catch (e) { if (valid()) setError(e instanceof Error ? e.message : String(e)); }
    finally { if (valid()) setPreparing(false); }
  };
  const start = () => { if (captureRef.current || disabled) return; const c = captureGeneration(view, selectedChartId, form); captureRef.current = c; setCapture(c); void prepare(c); };
  const retry = () => { if (job.request) void job.start(); else if (captureRef.current) void prepare(captureRef.current); };
  const terminal = ['completed', 'failed', 'cancelled', 'interrupted'].includes(job.job?.state ?? '');
  const prerequisite = disabled ? 'Нет доступа для записи.' : !selectedChartId ? 'Выберите карту.' : !view.project.analysis ? 'Выберите анализ.' : view.grid.review !== 'approved' ? 'Подтвердите сетку.' : '';
  return <GenerationControls storedTarget={!!view.charts.find(c => c.id === selectedChartId)?.target_profile} form={form} onChange={setForm} locked={!!capture} prerequisite={prerequisite} error={error} preparing={preparing} jobId={job.jobId} jobState={job.job?.state ?? null} jobError={job.error || (job.job?.error ? String(job.job.error) : '')} draftId={job.draftId} canStart={!capture && !prerequisite} canRetry={!!capture && !preparing && !job.busy && !job.jobId && !disabled} canCancel={!!job.jobId && !terminal && !disabled} canReset={!!capture} onStart={start} onRetry={retry} onCancel={() => void job.cancel()} onReset={reset} />;
}

