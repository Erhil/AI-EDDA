import { test } from 'node:test';
import { strict as assert } from 'node:assert';
import { copyNotes, buildPastePayload } from '../src/notesClipboard.ts';

const grid = { segments: [{start_time_sec: 0, start_beat_q: 0, bpm_quarter: 120, grid_division: 4}, {start_time_sec: 2, start_beat_q: 4, bpm_quarter: 60, grid_division: 4}] };
const chart = () => ({id:'source', events:[{id:'e1', time_ref:{kind:'beat',beat_q:3,micro_offset_sec:0.75}, locks:{time:true}, provenance:{source_ids:['old']}, notes:[{id:'n1',lane:0,locks:{lane:true}},{id:'n2',lane:1}]},{id:'e2',time_ref:{kind:'audio',time_sec:1},notes:[{id:'n3',lane:2}]}]});

test('copy preserves selected notes and exact coordinates in a detached snapshot', () => {
  const source = chart();
  const entries = copyNotes(source, grid, ['n1','n3']);
  assert.deepEqual(entries.map(e => [e.beat,e.second]), [[4.25,2.25],[2,1]]);
  assert.deepEqual(entries[0].event.notes.map(n => n.id), ['n1']);
  source.events[0].provenance.source_ids.push('changed');
  source.events[0].notes[0].locks.lane = false;
  assert.deepEqual(entries[0].event.provenance.source_ids, ['old']);
  assert.equal(entries[0].event.notes[0].locks.lane, true);
});

test('paste converts destination seconds only in beats mode and preserves source entries', () => {
  const entries = copyNotes(chart(), grid, ['n1','n3']);
  const beats = buildPastePayload({id:'destination'}, grid, entries, 3, 'beats');
  const seconds = buildPastePayload({id:'destination'}, grid, entries, 3, 'seconds');
  assert.equal(beats.target, 5);
  assert.equal(seconds.target, 3);
  assert.equal(beats.chart_id, 'destination');
  assert.deepEqual(beats.events.map(e => e.event.id), ['e1','e2']);
  beats.events[0].event.notes[0].lane = 3;
  assert.equal(entries[0].event.notes[0].lane, 0);
});

test('invalid selections and coordinates fail without changing the source', () => {
  const source = chart(); const before = structuredClone(source);
  assert.throws(() => copyNotes(source, grid, []));
  assert.throws(() => copyNotes(source, grid, ['n1','missing']));
  const invalid = chart(); invalid.events[0].time_ref.beat_q = Infinity;
  assert.throws(() => copyNotes(invalid, grid, ['n1']));
  assert.throws(() => buildPastePayload({id:'dest'}, grid, copyNotes(source, grid, ['n1']), Infinity, 'beats'));
  assert.deepEqual(source, before);
});
