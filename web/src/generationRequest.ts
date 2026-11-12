import type { ProjectView, ArrangementRequest, ObjectRef } from './generated/ui-contract';
import type { capabilities, getPlan } from './generationClient';
import { inverseTime } from './timeline';
import type { TargetResponse } from './targetClient';

export type GenerationForm = { lane: string; subdivision: string; startSec: string; endSec: string; seed: string };
export type GenerationCapture = { view: ProjectView; selectedChartId: string; form: GenerationForm; requestId: string; targetId: string };
export function captureGeneration(view: ProjectView, selectedChartId: string, form: GenerationForm): GenerationCapture {
  return { view: structuredClone(view), selectedChartId, form: { ...form }, requestId: crypto.randomUUID(), targetId: crypto.randomUUID() };
}
const uuid = (v: unknown): v is string => typeof v === 'string' && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(v);
const revision = (v: unknown): v is number => Number.isSafeInteger(v) && (v as number) >= 0;
const ref = (v: ObjectRef | null | undefined): v is ObjectRef => !!v && uuid(v.id) && revision(v.revision);
const same = (a: ObjectRef | null | undefined, b: ObjectRef | null | undefined) => a == null ? b == null : b != null && a.id === b.id && a.revision === b.revision;
const number = (s: string, name: string) => { const n = Number(s); if (!s.trim() || !Number.isFinite(n)) throw new Error(`Invalid ${name}`); return n; };
export function buildGenerationRequest(c: GenerationCapture, caps: Awaited<ReturnType<typeof capabilities>>, response: Awaited<ReturnType<typeof getPlan>>, targetResponse?: TargetResponse | null): ArrangementRequest {
  const { project: p, grid, charts } = c.view;
  const chart = charts.find(x => x.id === c.selectedChartId);
  const audio = p.audio; const parts = audio?.path.split('/');
  if (!uuid(p.id) || !revision(p.revision) || !ref(p.analysis) || (p.plan_inputs != null && !ref(p.plan_inputs)) || !chart || !uuid(chart.id) || !revision(chart.revision) || chart.grid_revision !== grid.revision) throw new Error('Missing or invalid project sources');
  if (!audio || !parts || parts.length !== 3 || parts[0] !== 'audio' || parts[2] !== 'song.ogg' || !uuid(parts[1]) || !/^[a-f0-9]{64}$/i.test(audio.sha256) || !Number.isFinite(audio.duration_sec) || audio.duration_sec <= 0) throw new Error('Invalid audio');
  if (!revision(grid.revision) || p.grid_revision !== grid.revision || grid.review !== 'approved' || !grid.segments.length || grid.segments.some(s => s.review !== 'approved' || !Number.isFinite(s.start_time_sec) || !Number.isFinite(s.start_beat_q) || !Number.isFinite(s.bpm_quarter) || s.bpm_quarter <= 0)) throw new Error('Grid is not approved');
  const lane = number(c.form.lane, 'lane'), subdivision = number(c.form.subdivision, 'subdivision'), start = number(c.form.startSec, 'start'), end = number(c.form.endSec, 'end'), seed = number(c.form.seed, 'seed');
  if (!Number.isInteger(lane) || lane < 0 || lane > 3 || ![1, 2, 4].includes(subdivision) || !Number.isSafeInteger(seed) || start < Math.max(0, grid.segments[0].start_time_sec) || end > audio.duration_sec || end <= start) throw new Error('Invalid generation settings');
  const start_q = inverseTime(grid, start), end_q = inverseTime(grid, end);
  if (!Number.isFinite(start_q) || !Number.isFinite(end_q) || end_q <= start_q) throw new Error('Invalid generation range');
  const plan = response.plan;
  if (response.project_id !== p.id || response.project_revision !== p.revision || response.grid_revision !== grid.revision || !same(response.analysis, p.analysis) || !same(response.plan_inputs, p.plan_inputs) || !uuid(plan.id) || !revision(plan.revision) || plan.audio_asset_id !== parts[1] || plan.audio_sha256 !== audio.sha256 || !same(plan.analysis, p.analysis) || plan.grid_revision !== grid.revision) throw new Error('Generation sources are stale');
  if (!caps.backend_id || !caps.backend_version || !caps.modes?.includes('single_lane') || !caps.target_fields?.includes('allowed_subdivisions')) throw new Error('Unsupported generator');
  if (chart.target_profile && (!ref(chart.target_profile) || !targetResponse || targetResponse.project_id !== p.id || targetResponse.project_revision !== p.revision || targetResponse.chart_id !== chart.id || targetResponse.chart_revision !== chart.revision || !same(targetResponse.target_ref, chart.target_profile) || !targetResponse.target || targetResponse.target.id !== chart.target_profile.id || targetResponse.target.version !== chart.target_profile.revision)) throw new Error('Generation target is stale');
  const target = chart.target_profile ? structuredClone(targetResponse!.target!) : { id: c.targetId, version: 1, name: 'Single lane', allowed_subdivisions: [subdivision] };
  return { id: c.requestId, project_id: p.id, generator_id: caps.backend_id, generator_version: caps.backend_version, audio_asset_id: parts[1], audio_sha256: audio.sha256, analysis: p.analysis, plan: { id: plan.id, revision: plan.revision }, grid_revision: grid.revision, chart: { id: chart.id, revision: chart.revision }, target, range_q: { start_q, end_q }, mode: 'single_lane', lane, seed };
}
