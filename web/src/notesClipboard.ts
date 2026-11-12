import type { Chart, HitEvent, TimingGrid } from './generated/ui-contract';
import { finalTime, inverseTime } from './timeline.ts';

export interface ClipboardEntry { event: HitEvent; beat: number; second: number }
export type PasteMode = 'beats' | 'seconds';

export function copyNotes(chart: Chart, grid: TimingGrid, noteIds: readonly string[]): ClipboardEntry[] {
  const selected = new Set(noteIds);
  if (!selected.size) throw new RangeError('empty selection');
  const known = new Set((chart.events ?? []).flatMap(e => (e.notes ?? []).map(n => n.id)));
  if ([...selected].some(id => !known.has(id))) throw new RangeError('unknown note ID');
  return (chart.events ?? []).flatMap(event => {
    const notes = (event.notes ?? []).filter(n => n.id !== undefined && selected.has(n.id));
    if (!notes.length) return [];
    const second = finalTime(grid, event.time_ref);
    const beat = inverseTime(grid, second);
    if (!Number.isFinite(second) || !Number.isFinite(beat)) throw new RangeError('invalid clipboard coordinates');
    return [{ event: structuredClone({ ...event, notes }), beat, second }];
  });
}

export function buildPastePayload(chart: Chart, grid: TimingGrid, entries: readonly ClipboardEntry[], targetSeconds: number, mode: PasteMode) {
  if (!chart.id || !entries.length || !Number.isFinite(targetSeconds) || (mode !== 'beats' && mode !== 'seconds')) throw new RangeError('invalid paste');
  const target = mode === 'beats' ? inverseTime(grid, targetSeconds) : targetSeconds;
  if (!Number.isFinite(target) || entries.some(e => !Number.isFinite(e.beat) || !Number.isFinite(e.second))) throw new RangeError('invalid paste coordinates');
  const positions = entries.map(e => mode === 'beats' ? e.beat : e.second);
  const anchor = Math.min(...positions);
  if (positions.some(pos => !Number.isFinite(target + pos - anchor))) throw new RangeError('invalid paste coordinates');
  return { chart_id: chart.id, events: structuredClone(entries), target, mode };
}
