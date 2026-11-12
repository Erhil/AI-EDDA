import { strict as assert } from 'node:assert';
import { test } from 'node:test';
import { setupServer } from 'msw/node';
import { http, HttpResponse } from 'msw';
import { load } from './sharedloader.mjs';
const { createJobHandlers } = await load('../../src/demo/jobHandlers.ts');
const { getJob, listJobs, cancelJob } = await load('../../src/jobsClient.ts');
const { getCatalog } = await load('../../src/catalogClient.ts');
test('analysis jobs follow elapsed time, cancellation and failure', async () => {
 const state = { projects: [{ project: { id: 'p', audio: { path: 'audio/a/song.ogg', sha256: 'hash', duration_sec: 100 } } }], analysisRefs: {} };
 const store = { read: () => state, update: fn => fn(state) };
 let scenario = 'normal', now = 100000; const realNow = Date.now, realFetch = globalThis.fetch;
 Date.now = () => now;
 const server = setupServer(...createJobHandlers(store, () => scenario), http.get('http://localhost/api/projects/:project/artifacts', () => HttpResponse.json({ kind: 'draft', items: [{ id: 'draft' }], next_cursor: null })));
 server.listen({ onUnhandledRequest: 'error' });
 const interceptedFetch = globalThis.fetch; globalThis.fetch = (url, options) => interceptedFetch(new URL(url, 'http://localhost'), options);
 const signal = new AbortController().signal;
 const start = async () => (await (await fetch('/api/projects/p/analysis-jobs', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ audio_asset_id: 'a', audio_hash: 'hash' }) })).json()).job_id;
 try {
  const id = await start(); for (let i = 0; i < 5; i++) assert.equal((await getJob(id, signal)).state, 'running');
  now += 2500; const done = await getJob(id, signal); assert.equal(done.state, 'completed'); assert.equal(state.analysisRefs.p.length, 1);
  assert.equal((await getCatalog('p', 'analysis', null, signal)).items[0].id, done.publication_result.id);
  assert.equal((await getCatalog('p', 'draft', null, signal)).items[0].id, 'draft');
  const obs = await fetch(`/api/projects/p/analysis/${done.publication_result.id}/observations?start_sec=0&end_sec=10`); assert.equal(obs.status, 200);
  assert.equal((await fetch(`/api/projects/p/analysis/${done.publication_result.id}/observations?start_sec=0&end_sec=61`)).status, 422);
  scenario = 'slow'; const slow = await start();
  const active = await listJobs(new URLSearchParams({ state: 'active', project_id: 'p', kind: 'analysis' }), signal);
  assert.ok(active.items.some(job => job.id === slow));
  assert.equal((await getJob(slow, signal)).context.project_id, 'p');
  now += 2500; assert.equal((await getJob(slow, signal)).state, 'running');
  assert.equal(await cancelJob(slow, signal), true);
  now += 15000; assert.equal((await getJob(slow, signal)).state, 'cancelled');
  scenario = 'error'; const failed = await start(); now += 2500; assert.equal((await getJob(failed, signal)).state, 'failed');
  assert.equal((await listJobs(new URLSearchParams({ state: 'failed', project_id: 'p', kind: 'analysis' }), signal)).items.length, 1);
  const terminal = await listJobs(new URLSearchParams({ state: 'terminal', project_id: 'p', kind: 'analysis' }), signal);
  assert.ok(terminal.items.some(job => job.id === failed));
  assert.ok(terminal.items.some(job => job.id === slow));
  assert.equal((await listJobs(new URLSearchParams({ state: 'running', project_id: 'p' }), signal)).items.length, 0);
  assert.equal(state.analysisRefs.p.length, 1);
 } finally { server.close(); Date.now = realNow; globalThis.fetch = realFetch; }
});



