import type { ObjectRef, ProjectView, TimeRef } from '../generated/ui-contract';
import { inverseTime, timeAt } from '../timeline';

const fail = (message: string): never => { throw new Error(message); };
const finite = (x: unknown): x is number => typeof x === 'number' && Number.isFinite(x);
const id = () => crypto.randomUUID();
const validTime = (r: unknown): r is TimeRef => {
  if (!r || typeof r !== 'object') return false;
  const t = r as Record<string, unknown>;
  return t.kind === 'audio' ? finite(t.time_sec) && t.time_sec >= 0 : t.kind === 'beat' && finite(t.beat_q) && t.beat_q >= 0 && (t.micro_offset_sec === undefined || finite(t.micro_offset_sec));
};
export function applyDemoEdit(input: ProjectView, kind: string, payload: Record<string, unknown>, analysisRefs: ObjectRef[]): { view: ProjectView; chartId?: string } {
  const view = structuredClone(input);
  if (kind === 'select_analysis') {
    const ref = analysisRefs.find(r => r.id === payload.analysis_id) ?? fail('unknown analysis ID');
    if (view.project.analysis?.id !== ref.id || view.project.analysis?.revision !== ref.revision) view.project.plan_inputs = null;
    view.project.analysis = structuredClone(ref);
    return { view };
  }
  if (kind === 'update_song_metadata') {
    for (const [key, value] of Object.entries(payload)) {
      if (!['title', 'artist', 'mapper', 'subtitle', 'explicit'].includes(key) || (key === 'explicit' ? typeof value !== 'boolean' : typeof value !== 'string')) fail('invalid song metadata');
    }
    view.project.metadata = { ...view.project.metadata, ...payload };
    return { view };
  }
  if (!['update_chart_metadata', 'create_event', 'delete_notes', 'move_notes'].includes(kind)) fail(`unsupported edit kind: ${kind}`);
  const chartId = typeof payload.chart_id === 'string' ? payload.chart_id : fail('chart ID required');
  const chart = view.charts.find(c => c.id === chartId) ?? fail('unknown chart ID');
  if (kind === 'update_chart_metadata') {
    for (const [key, value] of Object.entries(payload)) {
      if (key === 'chart_id') continue;
      if (key === 'name' ? typeof value !== 'string' : key === 'rank' ? !Number.isInteger(value) || (value as number) < 1 || (value as number) > 10 : key === 'speed' ? !finite(value) || value <= 0 : true) fail('invalid chart metadata');
    }
    const { chart_id: _, ...changes } = payload;
    chart.metadata = { ...chart.metadata, ...changes };
    return { view, chartId };
  }
  const operation = id();
  const modified = (p: typeof chart.provenance) => ({ ...p, last_modified_by: 'manual' as const, last_modified_operation_id: operation });
  if (kind === 'create_event') {
    const lanes = payload.lanes;
    if (!validTime(payload.time_ref) || !Array.isArray(lanes) || !lanes.length || lanes.some(l => !Number.isInteger(l) || l < 0 || l > 3) || new Set(lanes).size !== lanes.length) fail('invalid event');
    const provenance = { created_by: 'manual' as const, operation_id: operation };
    chart.events = [...(chart.events ?? []), { id: id(), time_ref: payload.time_ref as TimeRef, provenance, locks: { existence: true, time: true, membership: true }, notes: (lanes as number[]).map(lane => ({ id: id(), lane, provenance: { ...provenance }, locks: { existence: true, lane: true } })) }];
  } else {
    const ids = payload.note_ids;
    if (!Array.isArray(ids) || ids.some(n => typeof n !== 'string') || new Set(ids).size !== ids.length) fail('invalid note IDs');
    const selected = new Set(ids as string[]);
    const known = new Set((chart.events ?? []).flatMap(e => (e.notes ?? []).map(n => n.id)));
    if ([...selected].some(n => !known.has(n))) fail('unknown note ID');
    const delta = payload.delta_lane ?? 0, mode = payload.time_mode ?? null, shift = payload.time_delta ?? 0;
    if (kind === 'move_notes' && (!Number.isInteger(delta) || !finite(shift) || ![null, 'beats', 'seconds'].includes(mode as string | null) || (mode === null && shift !== 0))) fail('invalid move');
    chart.events = (chart.events ?? []).flatMap(event => {
      const notes = event.notes ?? [], affected = notes.filter(n => n.id && selected.has(n.id));
      if (!affected.length) return [event];
      if (kind === 'move_notes' && mode !== null && affected.length !== notes.length) fail('partial event time move requires split');
      const kept = notes.filter(n => kind !== 'delete_notes' || !n.id || !selected.has(n.id)).map(n => ({ ...n, lane: n.lane + (kind === 'move_notes' && n.id && selected.has(n.id) ? delta as number : 0), provenance: modified(n.provenance), locks: { existence: true, lane: true } }));
      if (kept.some(n => n.lane < 0 || n.lane > 3) || new Set(kept.map(n => n.lane)).size !== kept.length) fail('invalid lanes');
      if (!kept.length) return [];
      let ref = event.time_ref;
      if (kind === 'move_notes' && mode === 'beats') ref = { kind: 'beat', beat_q: (ref.kind === 'beat' ? ref.beat_q : inverseTime(view.grid, ref.time_sec)) + (shift as number), micro_offset_sec: ref.kind === 'beat' ? ref.micro_offset_sec : 0 };
      if (kind === 'move_notes' && mode === 'seconds') ref = { kind: 'audio', time_sec: (ref.kind === 'audio' ? ref.time_sec : timeAt(view.grid, ref.beat_q) + (ref.micro_offset_sec ?? 0)) + (shift as number) };
      if (!validTime(ref)) fail('invalid time');
      return [{ ...event, time_ref: ref, notes: kept, provenance: modified(event.provenance), locks: { existence: true, time: true, membership: true } }];
    });
  }
  chart.review = 'needs_review';
  return { view, chartId };
}
