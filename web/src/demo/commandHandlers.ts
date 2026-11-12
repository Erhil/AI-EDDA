import { http, HttpResponse } from 'msw';
import { applyDemoEdit } from './edit';
import type { createDemoStore } from './store';

const object = (x: unknown): x is Record<string, unknown> => !!x && typeof x === 'object' && !Array.isArray(x);
const canonical = (x: unknown): string => JSON.stringify(x, (_key, value: unknown) => object(value) ? Object.fromEntries(Object.entries(value).sort(([a], [b]) => a.localeCompare(b))) : value);
export function createCommandHandlers(store: ReturnType<typeof createDemoStore>) {
  return [http.post('*/api/projects/:id/commands', async ({ params, request }) => {
    const error = (status: number, detail: string) => HttpResponse.json({ detail }, { status });
    const projectId = String(params.id);
    try {
      const body: unknown = await request.json();
      if (!object(body) || typeof body.command_id !== 'string' || !body.command_id || typeof body.kind !== 'string' || !object(body.payload) || !Number.isInteger(body.base_project_revision) || !Number.isInteger(body.expected_grid_revision) || !object(body.expected_chart_revisions) || !Object.values(body.expected_chart_revisions).every(Number.isInteger)) return error(422, 'Invalid demo command');
      const state = store.read();
      const current = state.projects.find(v => v.project.id === projectId);
      if (!current) return error(404, 'Project not found');
      const key = JSON.stringify([projectId, body.command_id]);
      const content = canonical(body);
      const receipt = state.receipts[key];
      if (object(receipt)) return receipt.content === content ? HttpResponse.json(receipt.view as never) : error(409, 'REQUEST_ID_CONFLICT');
      const heads = Object.fromEntries(current.charts.map(c => [c.id, c.revision]));
      if (body.base_project_revision !== current.project.revision || body.expected_grid_revision !== current.grid.revision || canonical(body.expected_chart_revisions) !== canonical(heads)) return error(409, 'STALE_REVISION');
      const result = applyDemoEdit(current, body.kind, body.payload, state.analysisRefs[projectId] ?? []);
      result.view.project.revision = (current.project.revision ?? 0) + 1;
      if (result.chartId) {
        const chart = result.view.charts.find(c => c.id === result.chartId)!;
        chart.revision = (chart.revision ?? 0) + 1;
      }
      result.view.project.chart_heads = result.view.charts.map(c => ({ id: c.id!, revision: c.revision! }));
      store.update(draft => {
        const index = draft.projects.findIndex(v => v.project.id === projectId);
        draft.projects[index] = result.view;
        draft.receipts[key] = { content, view: result.view };
      });
      return HttpResponse.json(result.view);
    } catch (e) { return error(422, `Demo command failed: ${e instanceof Error ? e.message : 'invalid request'}`); }
  }), http.post('*/api/projects/:id/preview', () => errorPreview())];
}
function errorPreview() { return HttpResponse.json({ detail: 'Demo previews are not supported' }, { status: 422 }); }


