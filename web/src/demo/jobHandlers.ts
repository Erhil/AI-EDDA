import { http, HttpResponse } from 'msw';
import type { createDemoStore } from './store';
import { demoPlan, seededDrafts, demoCapabilities } from './jobFixtures';
import { inverseTime } from '../timeline';
import type { ArrangementRequest } from '../generated/ui-contract';
type Store = ReturnType<typeof createDemoStore>;
type Scenario = 'normal' | 'slow' | 'error';
type Job = { id: string; kind: string; state: string; label: string; project_id: string; started: number; scenario: Scenario; publication_result?: { id: string; revision: number; draft_id?: string }; pending?: ReturnType<typeof seededDrafts>[number]; error?: string; progress?: number };
const isRecord = (value: unknown): value is Record<string, unknown> => typeof value === 'object' && value !== null && !Array.isArray(value);
export function createDemoRuntime() { return { jobs: new Map<string, Job>(), drafts: seededDrafts() }; }
export function createJobHandlers(store: Store, getScenario: () => Scenario = () => 'normal', runtime = createDemoRuntime()) {
  const jobs = runtime.jobs;
  const view = (id: string) => store.read().projects.find(p => p.project.id === id);
  const missing = () => HttpResponse.json({ detail: 'Not found' }, { status: 404 });
  function current(job: Job) {
    if (job.state === 'running') {
      const progress = Math.min(1, (Date.now() - job.started) / (job.scenario === 'slow' ? 15000 : 2500));
      job.progress = progress;
      if (progress >= 1) {
        if (job.scenario === 'error') { job.state = 'failed'; job.error = 'Simulated analysis failure'; }
        else {
          const ref = job.pending ? { id: job.pending.draft.id!, revision: 1, draft_id: job.pending.draft.id! } : { id: crypto.randomUUID(), revision: 1 };
          if (job.pending) runtime.drafts.push(job.pending);
          else store.update(s => { (s.analysisRefs[job.project_id] ??= []).push(ref); });
          job.publication_result = ref; job.state = 'completed';
        }
      }
    }
    return job;
  }
  return [
    http.post('/api/projects/:project/analysis-jobs', async ({ params, request }) => {
      const p = view(String(params.project)); if (!p) return missing();
      const body = await request.json() as Record<string, unknown>;
      if (body.audio_hash !== p.project.audio?.sha256 || body.audio_asset_id !== p.project.audio?.path.split('/')[1]) return HttpResponse.json({ detail: 'Audio does not match project' }, { status: 422 });
      const id = crypto.randomUUID(); jobs.set(id, { id, kind: 'analysis', state: 'running', label: 'Analysis', project_id: String(params.project), started: Date.now(), scenario: getScenario() });
      return HttpResponse.json({ job_id: id });
    }),
    http.post('/api/projects/:project/generation-jobs', async ({ params, request }) => {
      const p = view(String(params.project)); if (!p || !p.project.audio || !p.project.analysis) return missing();
      let body: unknown;
      try { body = await request.json(); } catch { return HttpResponse.json({ detail: 'Unsupported demo generation request' }, { status: 422 }); }
      if (!isRecord(body) || !isRecord(body.chart) || !isRecord(body.target) || !isRecord(body.range_q) || !isRecord(body.analysis) || !isRecord(body.plan)) return HttpResponse.json({ detail: 'Unsupported demo generation request' }, { status: 422 });
      const r = body as unknown as ArrangementRequest; const chart = p.charts.find(c => c.id === r.chart.id); const plan = demoPlan(p).plan;
      const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);
      if (!chart || r.project_id !== p.project.id || r.audio_asset_id !== p.project.audio.path.split('/')[1] || r.audio_sha256 !== p.project.audio.sha256 || !same(r.analysis, p.project.analysis) || !same(r.plan, { id: plan.id, revision: plan.revision }) || r.grid_revision !== p.grid.revision || r.chart.revision !== chart.revision || r.generator_id !== 'demo' || r.generator_version !== '1' || r.mode !== 'single_lane' || r.lane !== 0 || r.seed !== 0 || !same(r.target.allowed_subdivisions, [1]) || r.range_q.start_q !== inverseTime(p.grid, Math.max(0, p.grid.segments[0]?.start_time_sec ?? 0)) || r.range_q.end_q !== inverseTime(p.grid, p.project.audio.duration_sec)) return HttpResponse.json({ detail: 'Unsupported demo generation request' }, { status: 422 });
      const base = structuredClone(chart), grid = structuredClone(p.grid), captured = structuredClone(r); const draftId = crypto.randomUUID();
      const events = [0, 1].map(n => ({ id: crypto.randomUUID(), time_ref: { kind: 'beat' as const, beat_q: 3 + n * 2 }, notes: [{ id: crypto.randomUUID(), lane: 0 }] }));
      const pending: ReturnType<typeof seededDrafts>[number] = { projectId: String(params.project), base_chart: base, grid, draft: { id: draftId, base_chart: captured.chart, chart: { ...base, events: [...(base.events ?? []), ...events] }, diff: { added_event_ids: events.map(e => e.id), removed_event_ids: [], changed_event_ids: [] }, generation_run: { id: crypto.randomUUID(), request: captured, capabilities: demoCapabilities, output: { events, target: captured.target, seed: captured.seed, backend_id: 'demo', backend_version: '1' } } } };
      const id = crypto.randomUUID(); jobs.set(id, { id, kind: 'draft_generation', state: 'running', label: 'Generation', project_id: String(params.project), started: Date.now(), scenario: getScenario(), pending }); return HttpResponse.json({ job_id: id, draft_id: draftId });
    }),
    http.get('/api/jobs/:id', ({ params }) => { const job = jobs.get(String(params.id)); return job ? HttpResponse.json({ ...current(job), context: { project_id: job.project_id } }) : missing(); }),
    http.post('/api/jobs/:id/cancel', ({ params }) => { const job = jobs.get(String(params.id)); if (!job) return missing(); current(job); if (job.state === 'running') job.state = 'cancelled'; return HttpResponse.json(true); }),
    http.get('/api/jobs', ({ request }) => { const q = new URL(request.url).searchParams; const state = q.get('state'); const active = new Set(['queued', 'running']); const terminal = new Set(['completed', 'failed', 'cancelled', 'interrupted']); return HttpResponse.json({ items: [...jobs.values()].map(current).filter(j => (!q.get('project_id') || j.project_id === q.get('project_id')) && (!q.get('kind') || j.kind === q.get('kind')) && (!state || (state === 'active' ? active.has(j.state) : state === 'terminal' ? terminal.has(j.state) : j.state === state))), next_cursor: null }); }),
    http.get('/api/projects/:project/artifacts', ({ params, request }) => { const p = view(String(params.project)); if (!p) return missing(); const kind = new URL(request.url).searchParams.get('kind'); if (kind !== 'analysis') return; return HttpResponse.json({ kind, items: kind === 'analysis' ? (store.read().analysisRefs[String(params.project)] ?? []).map(r => ({ ...r, audio_asset_id: p.project.audio?.path.split('/')[1], audio_sha256: p.project.audio?.sha256 })) : [], next_cursor: null }); }),
    http.get('/api/projects/:project/analysis/:analysis/observations', ({ params, request }) => { const p = view(String(params.project)); const ref = store.read().analysisRefs[String(params.project)]?.find(r => r.id === params.analysis); if (!p || !ref || !p.project.audio) return missing(); const q = new URL(request.url).searchParams; const start = Number(q.get('start_sec')), end = Number(q.get('end_sec')); if (!Number.isFinite(start) || !Number.isFinite(end) || start < 0 || end <= start || end - start > 60 || end > p.project.audio.duration_sec) return HttpResponse.json({ detail: 'Invalid window' }, { status: 422 }); const beats = Array.from({ length: Math.max(0, Math.floor(end * 2) - Math.ceil(start * 2) + 1) }, (_, i) => (Math.ceil(start * 2) + i) / 2); return HttpResponse.json({ analysis_id: ref.id, analysis_revision: ref.revision, audio_asset_id: p.project.audio.path.split('/')[1], audio_hash: p.project.audio.sha256, start_sec: start, end_sec: end, beat_times: beats, downbeat_times: beats.filter(t => t % 2 === 0), onset_times: beats, times: beats, onset_envelope: beats.map(() => 0.5), rms: beats.map(() => 0.2), activity: beats.map(() => 0.4) }); }),
  ];
}

