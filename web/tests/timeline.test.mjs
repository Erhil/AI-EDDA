import { test } from 'node:test';
import { strict as assert } from 'node:assert';
import { timeAt, inverseTime, finalTime, snapTime, buildNoteIndex, visibleNotes } from '../src/timeline.ts';
const grid = { segments: [{ start_time_sec: 1, start_beat_q: 0, bpm_quarter: 120, grid_division: 4 }, { start_time_sec: 3, start_beat_q: 4, bpm_quarter: 60, grid_division: 4 }] };
test('tempo conversion and inverse', () => { assert.equal(timeAt(grid, -2), 0); assert.equal(timeAt(grid, 5), 4); for (const q of [-2, 0, 3, 4, 8]) assert.equal(inverseTime(grid, timeAt(grid, q)), q); });
test('offset and snapping', () => { assert.equal(finalTime(grid, {kind:'beat', beat_q:4, micro_offset_sec:0.1}), 3.1); assert.deepEqual(snapTime(3.3, grid, 2), {kind:'beat', beat_q:4.5}); assert.deepEqual(snapTime(3.3, grid, null), {kind:'audio', time_sec:3.3}); });
test('sorted bounded selection', () => { const events = [{time_ref:{kind:'audio', time_sec:4}, notes:[{lane:2}]}, {time_ref:{kind:'audio', time_sec:2}, notes:[{lane:1},{lane:0}]}]; const index = buildNoteIndex(grid, events); assert.deepEqual(index.map(x => x.lane), [0,1,2]); assert.equal(visibleNotes(index, 2, 2).length, 2); assert.equal(visibleNotes(index, 3, 4).length, 1); assert.equal(visibleNotes(index, 4, 3).length, 0); });
