import type { ChartDraft, GeneratorCapabilities, MusicalPlan, ProjectView } from '../generated/ui-contract';
import { createFixtures } from './fixtures';
const id = (n: number) => `10000000-0000-4000-8000-${String(n).padStart(12, '0')}`;
export const demoCapabilities: GeneratorCapabilities = { backend_id: 'demo', backend_version: '1', modes: ['single_lane'], target_fields: ['allowed_subdivisions'] };
export function demoPlan(view: ProjectView) {
  const p = view.project, audio = p.audio!;
  const plan: MusicalPlan = { id: id(100 + Number(p.id!.slice(-1))), revision: 1, audio_asset_id: audio.path.split('/')[1], audio_sha256: audio.sha256, analysis: p.analysis!, grid_revision: view.grid.revision!, sections: [{ start_sec: 0, end_sec: audio.duration_sec }] };
  return { project_id: p.id, project_revision: p.revision, grid_revision: view.grid.revision, analysis: p.analysis ?? null, plan_inputs: p.plan_inputs ?? null, plan };
}
export function seededDrafts() {
  return createFixtures().map((view, i) => {
    const base = view.charts[0], plan = demoPlan(view).plan, audio = view.project.audio!;
    const target = { id: id(300 + i), version: 1, name: 'Single lane', allowed_subdivisions: [2] };
    const events = [0, 1].map((n) => ({ id: id(500 + i * 10 + n), time_ref: { kind: 'beat' as const, beat_q: 3 + n * 2 }, notes: [{ id: id(600 + i * 10 + n), lane: 0 }] }));
    const request = { id: id(400 + i), project_id: view.project.id!, generator_id: 'demo', generator_version: '1', audio_asset_id: audio.path.split('/')[1], audio_sha256: audio.sha256, analysis: view.project.analysis!, plan: { id: plan.id!, revision: plan.revision! }, grid_revision: view.grid.revision!, chart: { id: base.id!, revision: base.revision! }, target, range_q: { start_q: 0, end_q: 8 }, mode: 'single_lane' as const, lane: 0, seed: 1 };
    const draft: ChartDraft = { id: id(200 + i), base_chart: request.chart, chart: { ...base, events: [...(base.events ?? []), ...events] }, diff: { added_event_ids: events.map(e => e.id), removed_event_ids: [], changed_event_ids: [] }, generation_run: { id: id(700 + i), request, capabilities: demoCapabilities, output: { events, target, seed: 1, backend_id: 'demo', backend_version: '1' } } };
    return { projectId: view.project.id!, draft, base_chart: base, grid: view.grid };
  });
}

