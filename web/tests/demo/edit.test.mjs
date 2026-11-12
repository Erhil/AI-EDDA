import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { strict as assert } from 'node:assert';
import { test } from 'node:test';
const ts = createRequire(import.meta.url)('../../node_modules/typescript');
function load(url) {
  let s = ts.transpileModule(readFileSync(url, 'utf8'), { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } }).outputText;
  s = s.replace(/from ['"](\.\.?\/[^'"]+)['"]/g, (_, p) => `from '${load(new URL(p + '.ts', url))}'`);
  return `data:text/javascript;base64,${Buffer.from(s).toString('base64')}`;
}
const { applyDemoEdit } = await import(load(new URL('../../src/demo/edit.ts', import.meta.url)));
const { createFixtures } = await import(load(new URL('../../src/demo/fixtures.ts', import.meta.url)));
test('create, move, and delete preserve the input and lock edits', () => {
  const original = createFixtures()[0], chart_id = original.charts[0].id;
  let view = applyDemoEdit(original, 'create_event', { chart_id, time_ref: { kind: 'beat', beat_q: 12 }, lanes: [2] }, []).view;
  let event = view.charts[0].events.at(-1), note = event.notes[0];
  assert.equal(event.provenance.created_by, 'manual'); assert.equal(event.locks.time, true);
  view = applyDemoEdit(view, 'move_notes', { chart_id, note_ids: [note.id], delta_lane: -1, time_mode: 'seconds', time_delta: 1 }, []).view;
  event = view.charts[0].events.at(-1); assert.equal(event.notes[0].lane, 1); assert.equal(event.time_ref.time_sec, 7); assert.equal(event.notes[0].locks.lane, true);
  view = applyDemoEdit(view, 'delete_notes', { chart_id, note_ids: [note.id] }, []).view;
  assert.equal(view.charts[0].events.length, 4); assert.equal(view.charts[0].review, 'needs_review'); assert.equal(original.charts[0].events.length, 4);
});
test('metadata, selection, and invalid requests', () => {
  const original = createFixtures()[0], chart_id = original.charts[0].id;
  assert.equal(applyDemoEdit(original, 'update_song_metadata', { title: 'New' }, []).view.project.metadata.title, 'New');
  assert.equal(applyDemoEdit(original, 'update_chart_metadata', { chart_id, rank: 10, speed: 2 }, []).view.charts[0].metadata.rank, 10);
  const ref = { id: 'analysis', revision: 2 }; original.project.plan_inputs = { id: 'plan', revision: 1 };
  assert.equal(applyDemoEdit(original, 'select_analysis', { analysis_id: ref.id }, [ref]).view.project.plan_inputs, null);
  assert.throws(() => applyDemoEdit(original, 'update_chart_metadata', { chart_id, rank: 11 }, []));
  assert.throws(() => applyDemoEdit(original, 'create_event', { chart_id, time_ref: { kind: 'audio', time_sec: -1 }, lanes: [0] }, []));
  assert.throws(() => applyDemoEdit(original, 'move_notes', { chart_id, note_ids: [original.charts[0].events[0].notes[0].id], delta_lane: 0, time_mode: 'beats', time_delta: 1 }, []));
  assert.throws(() => applyDemoEdit(original, 'delete_notes', { chart_id, note_ids: ['unknown'] }, []));
  assert.throws(() => applyDemoEdit(original, 'unknown', {}, []));
});
