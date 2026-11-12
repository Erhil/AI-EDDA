import { strict as assert } from 'node:assert';
import { test } from 'node:test';
import { setupServer } from 'msw/node';
import { load } from './sharedloader.mjs';
const { createDemoStore } = await load('../../src/demo/store.ts');
const { createJobHandlers, createDemoRuntime } = await load('../../src/demo/jobHandlers.ts');
const { createDraftHandlers } = await load('../../src/demo/draftHandlers.ts');
const client = await load('../../src/generationClient.ts');
const jobs = await load('../../src/jobsClient.ts');
const { captureGeneration, buildGenerationRequest } = await load('../../src/generationRequest.ts');
test('generation publishes a captured draft and rejects unsupported requests', async () => {
 const storage = { getItem: () => null, setItem() {}, removeItem() {} };
 const store = createDemoStore(storage), runtime = createDemoRuntime(); let scenario = 'normal', now = 100000;
 const oldNow = Date.now, oldFetch = globalThis.fetch; Date.now = () => now;
 const server = setupServer(...createJobHandlers(store, () => scenario, runtime), ...createDraftHandlers(store, runtime)); server.listen({ onUnhandledRequest: 'error' });
 const intercepted = globalThis.fetch; globalThis.fetch = (url, options) => intercepted(new URL(url, 'http://localhost'), options);
 try {
  const view = store.read().projects[0], id = view.project.id, chart = view.charts[0];
  const caps = await client.capabilities(), plan = await client.getPlan(id);
  const capture = captureGeneration(view, chart.id, { lane: '0', subdivision: '1', startSec: '0', endSec: String(view.project.audio.duration_sec), seed: '0' });
  const request = buildGenerationRequest(capture, caps, plan);
  for (const invalid of [{ ...request, lane: 1 }, { ...request, target: null }, { ...request, range_q: null }]) await assert.rejects(client.startGeneration(id, invalid, {}), e => e.status === 422);
  const started = await client.startGeneration(id, request, {});
  assert.equal((await jobs.getJob(started.job_id)).state, 'running');
  store.update(s => { s.projects[0].charts[0].name = 'edited'; });
  now += 2500; assert.equal((await jobs.getJob(started.job_id)).state, 'completed');
  const inspected = await client.inspectDraft(id, started.draft_id);
  assert.deepEqual(inspected.draft.generation_run.request, request); assert.deepEqual(inspected.base_chart, chart);
  for (const mode of ['slow', 'error']) { scenario = mode; const run = await client.startGeneration(id, request, {}); if (mode === 'slow') await jobs.cancelJob(run.job_id); now += 2500; assert.equal((await jobs.getJob(run.job_id)).state, mode === 'slow' ? 'cancelled' : 'failed'); await assert.rejects(client.inspectDraft(id, run.draft_id)); }
 } finally { server.close(); Date.now = oldNow; globalThis.fetch = oldFetch; }
});
