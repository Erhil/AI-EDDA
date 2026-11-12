import { http, HttpResponse } from 'msw';
import type { createDemoStore } from './store';
import { createDemoRuntime } from './jobHandlers';
import { demoCapabilities, demoPlan } from './jobFixtures';
export function createDraftHandlers(store: ReturnType<typeof createDemoStore>, runtime = createDemoRuntime()) {
  const drafts = runtime.drafts;
  const view = (id: string) => store.read().projects.find(p => p.project.id === id);
  const find = (p: string, d: string) => drafts.find(x => x.projectId === p && x.draft.id === d);
  const missing = () => HttpResponse.json({ detail: 'Not found' }, { status: 404 });
  return [
    http.get('/api/generation-capabilities', () => HttpResponse.json(demoCapabilities)),
    http.get('/api/projects/:project/musical-plan', ({ params }) => { const p = view(String(params.project)); return p && p.project.analysis && p.project.audio ? HttpResponse.json(demoPlan(p)) : missing(); }),
    http.get('/api/projects/:project/artifacts', ({ params, request }) => { if (new URL(request.url).searchParams.get('kind') !== 'draft') return; if (!view(String(params.project))) return missing(); return HttpResponse.json({ kind: 'draft', items: drafts.filter(x => x.projectId === params.project).map(({ draft }) => ({ id: draft.id, chart_id: draft.base_chart.id, base_chart_revision: draft.base_chart.revision, generator_id: draft.generation_run.request.generator_id, seed: draft.generation_run.request.seed, event_count: draft.generation_run.output.events?.length ?? 0, rejected: !!store.read().draftDecisions[draft.id!] })), next_cursor: null }); }),
    http.get('/api/projects/:project/drafts/:draft', ({ params }) => { const d = find(String(params.project), String(params.draft)); return d && view(String(params.project)) ? HttpResponse.json({ draft: d.draft, base_chart: d.base_chart, grid: d.grid, applicable: false, rejected: !!store.read().draftDecisions[d.draft.id!], reasons: ['demo application unsupported'] }) : missing(); }),
    http.post('/api/projects/:project/drafts/:draft/reject', async ({ params, request }) => { const d = find(String(params.project), String(params.draft)); if (!d || !view(String(params.project))) return missing(); const body = await request.json() as { decision_id?: string }; if (!body.decision_id) return HttpResponse.json({ detail: 'Missing decision_id' }, { status: 422 }); store.update(s => { s.draftDecisions[d.draft.id!] = body.decision_id!; }); return HttpResponse.json({ draft_id: d.draft.id, decision_id: body.decision_id, draft_sha256: '0'.repeat(64) }); }),
  ];
}
