import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { strict as assert } from 'node:assert';
import { test } from 'node:test';
import { setupServer } from 'msw/node';
const ts = createRequire(import.meta.url)('../../node_modules/typescript');
let source = ts.transpileModule(readFileSync(new URL('../../src/demo/projectHandlers.ts', import.meta.url), 'utf8'), { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } }).outputText.replace("'msw'", JSON.stringify(import.meta.resolve('msw')));
const { createProjectHandlers } = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);
test('project reads and leases use current state', async () => {
  const view = { project: { id: 'p', revision: 1, analysis: null }, grid: { revision: 2 }, charts: [{ id: 'c', revision: 3 }] };
  const store = { read: () => ({ projects: [view] }) };
  const server = setupServer(...createProjectHandlers(store)); server.listen({ onUnhandledRequest: 'error' });
  const get = async path => fetch(`http://localhost/api${path}`);
  const post = async (path, body) => fetch(`http://localhost/api${path}`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) });
  try {
    assert.equal((await (await get('/session')).json()).authenticated, true);
    const signedOut = await fetch('http://localhost/api/session', { method: 'DELETE' });
    assert.equal((await signedOut.json()).authenticated, false);
    assert.equal((await (await get('/session')).json()).authenticated, false);
    assert.equal((await (await post('/session', {})).json()).authenticated, true);
    assert.equal((await (await get('/session')).json()).authenticated, true);
    assert.equal((await (await get('/projects')).json())[0].id, 'p');
    assert.equal((await get('/projects/missing')).status, 404);
    assert.equal((await (await get('/projects/p/charts/c/target')).json()).target, null);
    view.project.revision = 4;
    assert.equal((await (await get('/projects/p/grid-suggestions')).json()).project_revision, 4);
    const lease = await (await post('/projects/p/writer-lease', { tab_id: 'a', action: 'acquire' })).json();
    assert.equal((await post('/projects/p/writer-lease', { tab_id: 'b', action: 'acquire' })).status, 409);
    assert.equal((await post('/projects/p/writer-lease', { tab_id: 'a', action: 'heartbeat', lease_id: lease.lease_id })).status, 200);
    assert.equal((await post('/projects/p/writer-lease', { tab_id: 'b', action: 'takeover' })).status, 200);
  } finally { server.close(); }
});
