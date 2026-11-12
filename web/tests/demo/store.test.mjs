import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { strict as assert } from 'node:assert';
import { test } from 'node:test';
const ts = createRequire(import.meta.url)('../../node_modules/typescript');
const encode = s => `data:text/javascript;base64,${Buffer.from(s).toString('base64')}`;
function load(url) {
  let source = ts.transpileModule(readFileSync(url, 'utf8'), { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } }).outputText;
  source = source.replace(/from ['"](\.\/fixtures)['"]/g, (_, path) => `from '${load(new URL(path + '.ts', url))}'`);
  return encode(source);
}
const { createDemoStore, recoverDemoStorage, DEMO_STORAGE_KEY } = await import(load(new URL('../../src/demo/store.ts', import.meta.url)));
function storage() { let data = new Map(); return { getItem: k => data.get(k) ?? null, setItem: (k,v) => data.set(k,v), removeItem: k => data.delete(k) }; }
test('fixtures have consistent revisions and populated charts', () => {
  const state = createDemoStore(storage()).read(); assert.equal(state.projects.length, 2);
  for (const { project, grid, charts } of state.projects) {
    assert.equal(project.grid_revision, grid.revision); assert.equal(project.audio.duration_sec, 30);
    assert.match(project.audio.path, /^audio\/00000000-0000-4000-8000-000000000777\/song\.ogg$/);
    assert.equal(project.audio.sha256, '5896f231d1940c4a3bb5b3ce3a7370186eae9262a24759da9cceffc29835d107');
    assert.match(project.cover.path, /^assets\/00000000-0000-4000-8000-000000000777\/cover\.jpg$/);
    assert.equal(project.cover.sha256, 'ac476388f7e58d7fdcb4b90975813de17f94c57e99ab8c625ad29267e5f347f9');
    assert.equal(charts.length, 2); assert.deepEqual(project.chart_heads, charts.map(c => ({ id: c.id, revision: c.revision })));
    for (const chart of charts) { assert.equal(chart.grid_revision, grid.revision); assert.equal(new Set(chart.events.flatMap(e => e.notes.map(n => n.lane))).size, 4); assert(chart.events.some(e => e.notes.length === 2)); }
  }
});
test('stored JSON rejects malformed data consumed by the editor', () => {
  const mutations = [
    state => { state.projects[0].charts[0] = null; },
    state => { state.projects[0].charts[0].events[0] = null; },
    state => { state.projects[0].charts[0].events[0].notes[0] = null; },
    state => { state.projects[0].charts[0].events[0].time_ref = { kind: 'beat', beat_q: Infinity }; },
    state => { state.projects[0].charts[0].events[0].provenance = null; },
    state => { state.projects[0].charts[0].metadata = null; },
    state => { state.projects[0].grid.segments[0] = null; },
    state => { state.projects[0].grid.segments = []; },
    state => { state.projects[0].grid.segments[0].bpm_quarter = NaN; },
    state => { state.projects[0].project.revision = 0; },
    state => { state.analysisRefs[state.projects[0].project.id] = [null]; },
    state => { state.draftDecisions.answer = null; },
  ];
  for (const mutate of mutations) {
    const s = storage(), state = createDemoStore(storage()).read(); mutate(state);
    s.setItem(DEMO_STORAGE_KEY, JSON.stringify(state));
    assert.throws(() => createDemoStore(s), e => e.reason === 'corrupt');
  }
});
test('save, reload, reset and failed writes', () => {
  const s = storage(), store = createDemoStore(s), initial = store.read();
  store.update(d => { d.projects[0].project.metadata.title = 'Changed'; });
  assert.equal(createDemoStore(s).read().projects[0].project.metadata.title, 'Changed');
  s.setItem = () => { throw Error('quota'); };
  assert.throws(() => store.update(d => { d.projects = []; }), e => e.reason === 'write');
  assert.equal(store.read().projects.length, 2); store.reset(); assert.deepEqual(store.read(), initial);
});
test('save and reload valid empty metadata and nullable chart values', () => {
  const s = storage(), store = createDemoStore(s);
  store.update(d => {
    const { project, charts } = d.projects[0];
    project.metadata.title = ''; project.metadata.artist = ''; project.metadata.mapper = '';
    charts[0].metadata.name = ''; charts[0].metadata.rank = null; charts[0].metadata.speed = null;
  });
  const reloaded = createDemoStore(s).read().projects[0];
  assert.equal(reloaded.project.metadata.title, ''); assert.equal(reloaded.project.metadata.artist, '');
  assert.equal(reloaded.project.metadata.mapper, ''); assert.equal(reloaded.charts[0].metadata.name, '');
  assert.equal(reloaded.charts[0].metadata.rank, null); assert.equal(reloaded.charts[0].metadata.speed, null);
});
test('corrupt storage requires explicit recovery', () => {
  const s = storage(); s.setItem(DEMO_STORAGE_KEY, '{');
  assert.throws(() => createDemoStore(s), e => e.reason === 'corrupt');
  recoverDemoStorage(s); assert.equal(createDemoStore(s).read().projects.length, 2);
});
