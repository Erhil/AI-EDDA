import React from 'react';
import { delay, http, HttpResponse } from 'msw';
import { setupWorker } from 'msw/browser';
import { createDemoStore } from './store';
import { createProjectHandlers } from './projectHandlers';
import { createMediaHandlers } from './mediaHandlers';
import { createCommandHandlers } from './commandHandlers';
import { createDemoRuntime, createJobHandlers } from './jobHandlers';
import { createDraftHandlers } from './draftHandlers';
import { DemoControls, type Scenario } from './DemoControls';
export async function startDemo() {
 const store = createDemoStore(localStorage); store.read();
 const runtime = createDemoRuntime();
 let scenario: Scenario = 'normal';
 const worker = setupWorker(
  http.get('/api/*', async ({ request }) => {
   if (new URL(request.url).pathname.includes('/session')) return;
   if (scenario === 'slow') await delay(1800);
   if (scenario === 'error' && /^\/api\/projects\/[^/]+$/.test(new URL(request.url).pathname)) return HttpResponse.json({ detail: 'Демо: ошибка загрузки проекта' }, { status: 503 });
  }),
  ...createProjectHandlers(store), ...createMediaHandlers(store), ...createCommandHandlers(store), ...createJobHandlers(store, () => scenario, runtime), ...createDraftHandlers(store, runtime),
  http.all('/api/*', () => HttpResponse.json({ detail: 'Операция не поддерживается в демо' }, { status: 422 })),
 );
 await worker.start();
 return React.createElement(DemoControls, { setScenario: (value: Scenario) => { scenario = value; } });
}

