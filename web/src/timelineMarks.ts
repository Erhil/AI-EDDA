import type { TimingGrid } from './generated/ui-contract';
import { inverseTime } from './timeline';

// Display classification tolerance in quarter beats; this is not a game tolerance.
export const DISPLAY_TOLERANCE = 1e-6;
export function runeFamily(grid: TimingGrid, seconds: number): string {
  const q = inverseTime(grid, seconds);
  let low = 0;
  let high = grid.segments.length;
  while (low < high) {
    const middle = low + Math.floor((high - low) / 2);
    if (grid.segments[middle].start_time_sec <= seconds) low = middle + 1;
    else high = middle;
  }
  const segment = grid.segments[Math.max(0, low - 1)];
  const local = q - segment.start_beat_q;
  const fraction = ((local % 1) + 1) % 1;
  const choices: [number, string][] = [[0, 'whole'], [1, 'whole'], [.5, 'half'], [.25, 'quarter_first'], [.75, 'quarter_third'], [1/3, 'third_a'], [5/6, 'third_a'], [1/6, 'third_b'], [2/3, 'third_b']];
  const matches = choices.filter(([value]) => Math.abs(fraction - value) <= DISPLAY_TOLERANCE);
  return matches.length === 1 ? matches[0][1] : '?';
}
export function isBarStart(grid: TimingGrid, q: number): boolean {
  const meter = grid.meter ?? { numerator: 4, denominator: 4 };
  const length = meter.numerator * 4 / meter.denominator;
  const bars = (q - (meter.bar_origin_beat_q ?? 0)) / length;
  return Number.isFinite(bars) && Math.abs(bars - Math.round(bars)) < DISPLAY_TOLERANCE;
}
