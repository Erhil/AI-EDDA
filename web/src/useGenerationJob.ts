import { useCallback, useEffect, useRef, useState } from 'react';
import { startGeneration } from './generationClient';
import { getJob, cancelJob, type WriterHeaders } from './uploads';
import type { ArrangementRequest } from './generated/ui-contract';

type GenerationJob = { state: string; error?: unknown; publication_result?: unknown };
const terminal = ['completed', 'failed', 'cancelled', 'interrupted'];

export function useGenerationJob(projectId: string, writerHeaders: () => WriterHeaders | null) {
  const [request, setRequest] = useState<Readonly<ArrangementRequest> | null>(null);
  const [jobId, setJobId] = useState('');
  const [draftId, setDraftId] = useState('');
  const [job, setJob] = useState<GenerationJob | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const saved = useRef<Readonly<ArrangementRequest> | null>(null);
  const generation = useRef(0);
  const controller = useRef<AbortController | null>(null);
  const mounted = useRef(false);
  const currentProject = useRef(projectId);
  currentProject.current = projectId;

  const reset = useCallback(() => {
    generation.current++; controller.current?.abort(); controller.current = null; saved.current = null;
    setRequest(null); setJobId(''); setDraftId(''); setJob(null); setBusy(false); setError('');
  }, []);
  useEffect(() => { mounted.current = true; reset(); return () => { mounted.current = false; generation.current++; controller.current?.abort(); }; }, [projectId, reset]);

  const start = useCallback(async (input?: ArrangementRequest) => {
    if (!saved.current && !input) { setError('Укажите параметры генерации.'); return; }
    const headers = writerHeaders();
    if (!headers) { setError('Нет доступа для записи.'); return; }
    if (!saved.current) {
      const copy = JSON.parse(JSON.stringify(input)) as ArrangementRequest;
      const freeze = (value: unknown): void => { if (value && typeof value === 'object') { Object.values(value).forEach(freeze); Object.freeze(value); } };
      freeze(copy); saved.current = copy; setRequest(copy);
    }
    const attempt = ++generation.current; const project = projectId;
    controller.current?.abort(); const c = new AbortController(); controller.current = c;
    setJobId(''); setDraftId(''); setJob(null); setBusy(true); setError('');
    const valid = () => mounted.current && generation.current === attempt && currentProject.current === project;
    try {
      const result = await startGeneration(project, saved.current, headers, c.signal);
      if (valid()) { setJobId(result.job_id); setDraftId(result.draft_id); }
    } catch (e) { if (valid()) setError(String(e)); }
    finally { if (valid()) setBusy(false); }
  }, [projectId, writerHeaders]);

  useEffect(() => {
    if (!jobId) return;
    const attempt = generation.current; const project = projectId; let active = true;
    let timer: ReturnType<typeof setTimeout>;
    const valid = () => active && mounted.current && generation.current === attempt && currentProject.current === project;
    const poll = async () => {
      try { const next: GenerationJob = await getJob(jobId); if (!valid()) return; setJob(next); if (!terminal.includes(next.state)) timer = setTimeout(poll, 1000); }
      catch (e) { if (valid()) { setError(String(e)); timer = setTimeout(poll, 1000); } }
    };
    void poll(); return () => { active = false; clearTimeout(timer); };
  }, [jobId, projectId]);

  const cancel = useCallback(async () => {
    if (!jobId) return;
    const headers = writerHeaders(); if (!headers) { setError('Нет доступа для записи.'); return; }
    const attempt = generation.current; const project = projectId;
    try { await cancelJob(jobId, headers); }
    catch (e) { if (mounted.current && generation.current === attempt && currentProject.current === project) setError(String(e)); }
  }, [jobId, projectId, writerHeaders]);
  return { start, reset, cancel, request, jobId, draftId, job, busy, error };
}
