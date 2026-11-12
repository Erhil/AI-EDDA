import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { strict as assert } from 'node:assert';
import { test } from 'node:test';

const require = createRequire(new URL('../../../web/package.json', import.meta.url));
const ts = require('typescript');
const asModule = source => `data:text/javascript;base64,${Buffer.from(ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } }).outputText).toString('base64')}`;
const apiUrl = asModule(readFileSync(new URL('../../../web/src/api.ts', import.meta.url), 'utf8'));
const clientSource = readFileSync(new URL('../../../web/src/generationClient.ts', import.meta.url), 'utf8').replace("from './api'", `from '${apiUrl}'`);
const client = await import(asModule(clientSource));
const { getTarget } = await import(asModule(readFileSync(new URL('../../../web/src/targetClient.ts', import.meta.url), 'utf8').replace("from './api'", `from '${apiUrl}'`)));
const { ApiError } = await import(apiUrl);
const freeze = value => { if (value && typeof value === 'object') { Object.values(value).forEach(freeze); Object.freeze(value); } return value; };
const arrangement = freeze({ id: 'request-id', project_id: 'project', generator_id: 'single_lane', generator_version: '1', audio_asset_id: 'audio', audio_sha256: 'a'.repeat(64), analysis: { id: 'analysis', revision: 3 }, plan: { id: 'plan', revision: 7 }, chart: { id: 'chart', revision: 2 }, grid_revision: 5, target: { id: 'target', version: 1, name: 'Test', allowed_subdivisions: [1, 2] }, range_q: { start_q: 0, end_q: 16 }, mode: 'single_lane', lane: 0, reference_chart: null, reference_influence: null, seed: 99 });
const originalBody = JSON.stringify(arrangement);
const headers = freeze({ 'X-Tab-Id': 'tab', 'X-Writer-Lease': 'lease' });
const project = 'project /?#%ю';
const draft = 'draft /?#%ю';
const base = `/api/projects/${encodeURIComponent(project)}`;
const cases = [
  { path: '/api/generation-capabilities', run: signal => client.capabilities(signal) },
  { path: `${base}/musical-plan`, run: signal => client.getPlan(project, signal) },
  { path: `${base}/generation-jobs`, body: arrangement, run: signal => client.startGeneration(project, arrangement, headers, signal) },
  { path: `${base}/drafts/${encodeURIComponent(draft)}`, run: signal => client.inspectDraft(project, draft, signal) },
  { path: `${base}/drafts/${encodeURIComponent(draft)}/reject`, body: { decision_id: 'decision' }, run: signal => client.rejectDraft(project, draft, 'decision', headers, signal) },
];

test('all endpoint paths, methods, bodies, headers, credentials and signals', async () => {
  const previous = globalThis.fetch;
  try {
    for (const item of cases) {
      const controller = new AbortController();
      const payload = { marker: item.path };
      globalThis.fetch = async (path, init) => {
        assert.equal(path, item.path);
        assert.equal(init.method, item.body ? 'POST' : 'GET');
        assert.equal(init.credentials, 'same-origin');
        assert.equal(init.signal, controller.signal);
        assert.equal(init.body, item.body ? JSON.stringify(item.body) : undefined);
        assert.deepEqual(init.headers, item.body ? { ...headers, 'Content-Type': 'application/json' } : undefined);
        return { ok: true, json: async () => payload };
      };
      assert.equal(await item.run(controller.signal), payload);
    }
    assert.equal(JSON.stringify(arrangement), originalBody);
    assert.deepEqual(headers, { 'X-Tab-Id': 'tab', 'X-Writer-Lease': 'lease' });
  } finally { globalThis.fetch = previous; }
});

test('HTTP status and string or structured detail survive for every endpoint', async () => {
  const previous = globalThis.fetch;
  try {
    for (const item of cases) for (const status of [401, 404, 409, 422, 503]) for (const detail of ['error detail', [{ loc: ['body', 'seed'], msg: 'invalid' }]]) {
      globalThis.fetch = async () => ({ ok: false, status, json: async () => ({ detail }) });
      await assert.rejects(item.run(), error => error instanceof ApiError && error.status === status && error.detail === detail);
    }
    globalThis.fetch = async () => ({ ok: false, status: 502, statusText: 'Bad Gateway', json: async () => { throw new SyntaxError('HTML body'); } });
    await assert.rejects(client.capabilities(), error => error instanceof ApiError && error.status === 502 && error.detail === 'Bad Gateway');
  } finally { globalThis.fetch = previous; }
});

test('abort propagates during fetch, success body and error body for every endpoint', async () => {
  const previous = globalThis.fetch;
  try {
    for (const item of cases) for (const phase of ['fetch', 'success-body', 'error-body']) {
      const controller = new AbortController();
      const aborted = new DOMException('Aborted', 'AbortError');
      const wait = () => new Promise((resolve, reject) => controller.signal.addEventListener('abort', () => reject(aborted), { once: true }));
      globalThis.fetch = async () => phase === 'fetch' ? wait() : { ok: phase === 'success-body', status: 409, json: wait };
      const result = item.run(controller.signal);
      await new Promise(resolve => setImmediate(resolve));
      controller.abort();
      await assert.rejects(result, error => error === aborted && !(error instanceof ApiError));
    }
  } finally { globalThis.fetch = previous; }
});

test('target client encodes both IDs, uses session credentials, preserves HTTP errors and propagates every abort phase',async()=>{
 const previous=globalThis.fetch;try{
  const controller=new AbortController(),payload={target_ref:null,target:null};
  globalThis.fetch=async(path,init)=>{assert.equal(path,`${base}/charts/${encodeURIComponent(draft)}/target`);assert.equal(init.credentials,'same-origin');assert.equal(init.signal,controller.signal);assert(!init.method||init.method==='GET');return{ok:true,json:async()=>payload};};assert.equal(await getTarget(project,draft,controller.signal),payload);
  for(const status of [401,404,422,503])for(const detail of ['Invalid request',[{msg:'bad field'}]]){globalThis.fetch=async()=>({ok:false,status,json:async()=>({detail})});await assert.rejects(getTarget(project,draft),error=>error instanceof ApiError&&error.status===status&&error.detail===detail);}
  globalThis.fetch=async()=>({ok:false,status:502,statusText:'Bad Gateway',json:async()=>{throw new SyntaxError('bad JSON');}});await assert.rejects(getTarget(project,draft),error=>error instanceof ApiError&&error.detail==='Bad Gateway');
  for(const phase of ['fetch','success-body','error-body']){const c=new AbortController(),aborted=new DOMException('Aborted','AbortError'),wait=()=>new Promise((resolve,reject)=>c.signal.addEventListener('abort',()=>reject(aborted),{once:true}));globalThis.fetch=async()=>phase==='fetch'?wait():{ok:phase==='success-body',status:409,json:wait};const pending=getTarget(project,draft,c.signal);await new Promise(resolve=>setImmediate(resolve));c.abort();await assert.rejects(pending,error=>error===aborted);}
 }finally{globalThis.fetch=previous;}
});
