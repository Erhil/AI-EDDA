import { http, HttpResponse } from 'msw';
import type { createDemoStore } from './store';

export function createProjectHandlers(store: ReturnType<typeof createDemoStore>) {
  const leases = new Map<string, { tab: string; id: string; expires: number }>();
  let authenticated = true;
  const find = (id: string) => store.read().projects.find(p => p.project.id === id);
  const missing = () => HttpResponse.json({ detail: 'Not found' }, { status: 404 });
  return [
    http.get('*/api/session', () => HttpResponse.json({ authenticated })),
    http.post('*/api/session', () => { authenticated = true; return HttpResponse.json({ authenticated }); }),
    http.delete('*/api/session', () => { leases.clear(); authenticated = false; return HttpResponse.json({ authenticated }); }),
    http.get('*/api/projects', () => HttpResponse.json(store.read().projects.map(p => p.project))),
    http.get('*/api/projects/:id', ({ params }) => { const p = find(String(params.id)); return p ? HttpResponse.json(p) : missing(); }),
    http.get('*/api/projects/:id/charts/:chartId/target', ({ params }) => {
      const p = find(String(params.id)); const c = p?.charts.find(c => c.id === params.chartId);
      if (!p || !c) return missing();
      return HttpResponse.json({ project_id: p.project.id, project_revision: p.project.revision, chart_id: c.id, chart_revision: c.revision, target_ref: c.target_profile ?? null, target: null });
    }),
    http.get('*/api/projects/:id/grid-suggestions', ({ params }) => {
      const p = find(String(params.id)); if (!p) return missing();
      return HttpResponse.json({ project_id: p.project.id, project_revision: p.project.revision, grid_revision: p.grid.revision, analysis: p.project.analysis ?? null, suggestions: [] });
    }),
    http.post('*/api/projects/:id/writer-lease', async ({ params, request }) => {
      const id = String(params.id); if (!find(id)) return missing();
      const body = await request.json() as { tab_id?: string; action?: string; lease_id?: string };
      if (!body.tab_id || !['acquire', 'heartbeat', 'takeover'].includes(body.action ?? '')) return HttpResponse.json({ detail: 'Invalid request' }, { status: 422 });
      const old = leases.get(id); const current = old && old.expires > Date.now() ? old : undefined;
      if ((body.action === 'heartbeat' && (!current || current.tab !== body.tab_id || current.id !== body.lease_id)) || (body.action === 'acquire' && current && current.tab !== body.tab_id)) return HttpResponse.json({ detail: 'Writer lease unavailable' }, { status: 409 });
      const lease = { tab: body.tab_id, id: body.action !== 'takeover' && current ? current.id : crypto.randomUUID(), expires: Date.now() + 30000 }; leases.set(id, lease);
      return HttpResponse.json({ lease_id: lease.id, ttl: 30, heartbeat_interval: 10 });
    }),
  ];
}

