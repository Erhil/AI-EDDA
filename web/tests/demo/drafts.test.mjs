import { strict as assert } from 'node:assert';
import { test } from 'node:test';
import { setupServer } from 'msw/node';
import { load } from './sharedloader.mjs';
const { createDemoStore } = await load('../../src/demo/store.ts');
const { createDemoRuntime, createJobHandlers } = await load('../../src/demo/jobHandlers.ts');
const { createDraftHandlers } = await load('../../src/demo/draftHandlers.ts');
const client = await load('../../src/generationClient.ts');
const { getCatalog } = await load('../../src/catalogClient.ts');
test('seeded drafts can be inspected and rejected without changing charts', async () => {
 const store = createDemoStore({ getItem: () => null, setItem() {}, removeItem() {} }), runtime = createDemoRuntime();
 const server = setupServer(...createJobHandlers(store, undefined, runtime), ...createDraftHandlers(store, runtime));
 const oldFetch = globalThis.fetch; server.listen({ onUnhandledRequest: 'error' }); const intercepted = globalThis.fetch;
 globalThis.fetch = (url, options) => intercepted(new URL(url, 'http://localhost'), options);
 try {
  for (const view of store.read().projects) {
   const id = view.project.id, before = structuredClone(view.charts);
   const plan = await client.getPlan(id); assert.ok(plan.plan.id);
   const catalog = await getCatalog(id, 'draft'); assert.ok(catalog.items.length);
   const draftId = catalog.items[0].id, inspection = await client.inspectDraft(id, draftId);
   assert.ok(inspection.draft.generation_run.request); assert.ok(inspection.draft.generation_run.output.events.length);
   assert.ok(inspection.base_chart.events); assert.ok(inspection.grid.segments.length);
   await client.rejectDraft(id, draftId, crypto.randomUUID(), {});
   assert.equal((await client.inspectDraft(id, draftId)).rejected, true);
   assert.deepEqual(store.read().projects.find(p => p.project.id === id).charts, before);
  }
 } finally { server.close(); globalThis.fetch = oldFetch; }
});
