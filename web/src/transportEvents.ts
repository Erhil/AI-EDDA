import type { Chart, TimingGrid } from './generated/ui-contract';
import type { ClickEvent } from './clickTrack';
import { finalTime, inverseTime, timeAt } from './timeline';

export function transportEvents(grid: TimingGrid, chart: Chart, duration: number): ClickEvent[] {
  const events: ClickEvent[] = [];
  for (const event of chart.events ?? []) {
    const timeSec = finalTime(grid, event.time_ref);
    if (!Number.isFinite(timeSec) || timeSec < 0 || timeSec >= duration) continue;
    for (const note of event.notes ?? []) if (Number.isInteger(note.lane) && note.lane >= 0 && note.lane <= 3)
      events.push({ timeSec, kind: 'hit', lane: note.lane as 0 | 1 | 2 | 3 });
  }
  const meter = grid.meter ?? { numerator: 4, denominator: 4 };
  const step = 4 / meter.denominator, origin = meter.bar_origin_beat_q ?? 0;
  const first = Math.ceil((inverseTime(grid, 0) - origin) / step - 1e-9);
  const last = Math.floor((inverseTime(grid, duration) - origin) / step + 1e-9);
  if (!Number.isFinite(first) || !Number.isFinite(last) || !Number.isSafeInteger(first) || !Number.isSafeInteger(last) || !Number.isFinite(step) || step <= 0 || !Number.isInteger(meter.numerator) || meter.numerator <= 0 || last - first > 100000) throw new Error('Некорректная или слишком плотная сетка метронома');
  for (let i = first; i <= last; i++) {
    const timeSec = timeAt(grid, origin + i * step);
    if (Number.isFinite(timeSec) && timeSec >= 0 && timeSec < duration) events.push({ timeSec, kind: i % meter.numerator === 0 ? 'downbeat' : 'beat' });
  }
  return events;
}
