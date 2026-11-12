import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { strict as assert } from 'node:assert';
import { test } from 'node:test';
import { setupServer } from 'msw/node';
const ts = createRequire(import.meta.url)('../../node_modules/typescript');
function load(url) {
 let s = ts.transpileModule(readFileSync(url, 'utf8'), { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } }).outputText;
 s = s.replace(/from ['"](\.\.?\/[^'"]+)['"]/g, (_, p) => `from '${load(new URL(p + '.ts', url))}'`).replace("'msw'", JSON.stringify(import.meta.resolve('msw')));
 return `data:text/javascript;base64,${Buffer.from(s).toString('base64')}`;
}
const { createCommandHandlers } = await import(load(new URL('../../src/demo/commandHandlers.ts', import.meta.url)));
const { createDemoStore } = await import(load(new URL('../../src/demo/store.ts', import.meta.url)));
test('commands commit once and reject invalid requests atomically', async () => {
 let fail = false; const storage = { getItem: () => null, setItem: () => { if (fail) throw Error('disk'); }, removeItem() {} };
 const store = createDemoStore(storage), server = setupServer(...createCommandHandlers(store)); server.listen({ onUnhandledRequest: 'error' });
 const initial = store.read().projects[0], id = initial.project.id;
 const envelope = (view, command_id, kind, payload) => ({ command_id, kind, payload, base_project_revision: view.project.revision, expected_grid_revision: view.grid.revision, expected_chart_revisions: Object.fromEntries(view.charts.map(c => [c.id,c.revision])) });
 const post = (body, path = 'commands') => fetch(`http://localhost/api/projects/${id}/${path}`, { method:'POST', headers:{'content-type':'application/json'}, body:JSON.stringify(body) });
 try {
  const cmd = envelope(initial, 'one', 'update_song_metadata', { title:'New' });
  const response = await post(cmd); assert.equal(response.status, 200); const updated = await response.json(); assert.equal(updated.project.metadata.title, 'New');
  assert.deepEqual(await (await post(cmd)).json(), updated);
  assert.equal((await post({...cmd, payload:{title:'Other'}})).status, 409);
  assert.equal((await post({...cmd, command_id:'stale'})).status, 409);
  assert.equal((await post(envelope(updated,'bad','unknown',{}))).status, 422);
  assert.equal((await post({}, 'preview')).status, 422);
  const note = envelope(updated,'note','create_event',{chart_id:updated.charts[0].id,time_ref:{kind:'beat',beat_q:12},lanes:[1]});
  const next = await (await post(note)).json(); assert.equal(next.charts[0].revision, updated.charts[0].revision + 1);
  const before = store.read(); fail = true;
  assert.equal((await post(envelope(next,'fail','update_song_metadata',{title:'Lost'}))).status, 422); assert.deepEqual(store.read(), before);
 } finally { server.close(); }
});

test('concurrent commands check revisions after reading their request bodies', async () => {
 const storage = { getItem: () => null, setItem() {}, removeItem() {} };
 const store = createDemoStore(storage), server = setupServer(...createCommandHandlers(store)); server.listen({ onUnhandledRequest: 'error' });
 const initial = store.read().projects[0], id = initial.project.id;
 const envelope = (view, command_id, title) => ({ command_id, kind:'update_song_metadata', payload:{title}, base_project_revision:view.project.revision, expected_grid_revision:view.grid.revision, expected_chart_revisions:Object.fromEntries(view.charts.map(c => [c.id,c.revision])) });
 const post = body => fetch(`http://localhost/api/projects/${id}/commands`, { method:'POST', headers:{'content-type':'application/json'}, body:JSON.stringify(body) });
 try {
  const [first, second] = await Promise.all([post(envelope(initial,'parallel-a','First')), post(envelope(initial,'parallel-b','Second'))]);
  assert.deepEqual([first.status, second.status].sort(), [200, 409]);
  const winner = first.status === 200 ? await first.json() : await second.json();
  assert.equal(store.read().projects[0].project.metadata.title, winner.project.metadata.title);
  assert.ok(['First','Second'].includes(winner.project.metadata.title));
  assert.equal(winner.project.revision, initial.project.revision + 1);

  const retryView = store.read().projects[0], same = envelope(retryView,'parallel-same','Same');
  const [retryA, retryB] = await Promise.all([post(same), post(same)]);
  assert.equal(retryA.status, 200); assert.equal(retryB.status, 200);
  assert.deepEqual(await retryA.json(), await retryB.json());
  assert.equal(store.read().projects[0].project.revision, retryView.project.revision + 1);
  assert.equal(store.read().projects[0].project.metadata.title, 'Same');
 } finally { server.close(); }
});
