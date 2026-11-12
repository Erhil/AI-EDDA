import type { TimingGrid, TimeRef, HitEvent, ChartNote } from './generated/ui-contract';

function segmentAt<T>(items: readonly T[], value: number, key: (item: T) => number): T {
  let lo = 0, hi = items.length;
  while (lo < hi) { const mid = (lo + hi) >>> 1; if (key(items[mid]) <= value) lo = mid + 1; else hi = mid; }
  return items[Math.max(0, lo - 1)];
}
export function timeAt(grid: TimingGrid, q: number): number {
  const s = segmentAt(grid.segments, q, x => x.start_beat_q);
  return s.start_time_sec + (q - s.start_beat_q) * 60 / s.bpm_quarter;
}
export function inverseTime(grid: TimingGrid, seconds: number): number {
  const s = segmentAt(grid.segments, seconds, x => x.start_time_sec);
  return s.start_beat_q + (seconds - s.start_time_sec) * s.bpm_quarter / 60;
}
export function finalTime(grid: TimingGrid, ref: TimeRef): number {
  return ref.kind === 'audio' ? ref.time_sec : timeAt(grid, ref.beat_q) + (ref.micro_offset_sec ?? 0);
}
export function snapTime(seconds: number, grid: TimingGrid, division: number | null): TimeRef {
  if (division === null) return { kind: 'audio', time_sec: seconds };
  if (!Number.isFinite(division) || division <= 0) throw new RangeError('division must be positive');
  return { kind: 'beat', beat_q: Math.round(inverseTime(grid, seconds) * division) / division };
}
export interface IndexedNote { event: HitEvent; note: ChartNote; time: number; lane: number }
export function buildNoteIndex(grid: TimingGrid, events: readonly HitEvent[]): IndexedNote[] {
  return events.flatMap(event => (event.notes ?? []).map(note => ({ event, note, time: finalTime(grid, event.time_ref), lane: note.lane }))).sort((a, b) => a.time - b.time || a.lane - b.lane);
}
export function visibleNotes(index: readonly IndexedNote[], start: number, end: number): IndexedNote[] {
  if (end < start) return [];
  const bound = (value: number, upper: boolean) => { let lo = 0, hi = index.length; while (lo < hi) { const mid = (lo + hi) >>> 1; if (index[mid].time < value || (upper && index[mid].time === value)) lo = mid + 1; else hi = mid; } return lo; };
  return index.slice(bound(start, false), bound(end, true));
}
