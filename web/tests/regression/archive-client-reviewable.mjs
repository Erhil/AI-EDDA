import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { strict as assert } from 'node:assert';
import { test } from 'node:test';
const ts = createRequire(import.meta.url)('../../../web/node_modules/typescript');
const url = source => `data:text/javascript;base64,${Buffer.from(ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } }).outputText).toString('base64')}`;
const apiUrl = url(readFileSync(new URL('../../../web/src/api.ts', import.meta.url), 'utf8'));
const { ApiError } = await import(apiUrl);
const client = await import(url(readFileSync(new URL('../../../web/src/archiveClient.ts', import.meta.url), 'utf8').replace("from './api'", `from '${apiUrl}'`)));

test('upload sends the file, headers and progress; errors and abort clean up', async () => {
 const old = globalThis.XMLHttpRequest; let xhr;
 class Mock { constructor() { xhr = this; this.upload = {}; this.headers = {}; } open(...args) { this.opened = args; } setRequestHeader(k,v) { this.headers[k] = v; } send(file) { this.file = file; } abort() { this.onabort(); } }
 globalThis.XMLHttpRequest = Mock;
 try {
  const file = { name: 'a ю.zip', size: 42 }, c = new AbortController(), progress = [];
  let pending = client.uploadProjectArchive('game', 'id', file, (...v) => progress.push(v), c.signal);
  assert.deepEqual(xhr.opened, ['POST', '/api/projects/imports']);
  assert.deepEqual(xhr.headers, {'Content-Type':'application/octet-stream','X-Request-Id':'id','X-Archive-Kind':'game','X-File-Name':encodeURIComponent(file.name),'X-File-Size':'42'});
  assert.equal(xhr.file, file); xhr.upload.onprogress({loaded: 2,total: 42}); assert.deepEqual(progress, [[2,42]]);
  xhr.status = 202; xhr.responseText = '{"job_id":"j","upload_required":true,"verifying":false}'; xhr.onload(); assert.equal((await pending).job_id, 'j'); c.abort();
  for (const detail of [{code:'IMPORT_IN_PROGRESS',job_id:'j'}, {code:'REQUEST_ID_CONFLICT'}]) { pending = client.uploadProjectArchive('native','id',file,()=>{},new AbortController().signal); xhr.status=409; xhr.responseText=JSON.stringify({detail}); xhr.onload(); await assert.rejects(pending,e=>e instanceof ApiError && e.status===409 && e.detail.code===detail.code); }
  pending=client.uploadProjectArchive('native','id',file,()=>{},new AbortController().signal); xhr.status=500; xhr.statusText='Bad'; xhr.responseText='bad'; xhr.onload(); await assert.rejects(pending,e=>e.detail==='Bad');
  pending=client.uploadProjectArchive('native','id',file,()=>{},new AbortController().signal); xhr.onerror(); await assert.rejects(pending,TypeError);
  const aborted=new AbortController(); pending=client.uploadProjectArchive('native','id',file,()=>{},aborted.signal); aborted.abort(); await assert.rejects(pending,e=>e.name==='AbortError');
  await assert.rejects(client.uploadProjectArchive('native','id',file,()=>{},aborted.signal),e=>e.name==='AbortError');
 } finally { globalThis.XMLHttpRequest=old; }
});

test('job requests use session credentials and preserve errors and aborts', async () => {
 const old=globalThis.fetch; try {
  for (const [method, suffix, run] of [['GET','',client.getImportJob],['POST','/cancel',client.cancelImportJob]]) {
   const c=new AbortController(); globalThis.fetch=async (path,init)=>{ assert.equal(path,`/api/jobs/a%2Fb${suffix}`); assert.equal(init.method,method); assert.equal(init.credentials,'same-origin'); assert.equal(init.signal,c.signal); assert.equal(init.headers,undefined); return {ok:true,json:async()=>true}; }; assert.equal(await run('a/b',c.signal),true);
   globalThis.fetch=async()=>({ok:false,status:409,json:async()=>({detail:{code:'conflict'}})}); await assert.rejects(run('x'),e=>e instanceof ApiError&&e.status===409&&e.detail.code==='conflict');
   globalThis.fetch=async()=>({ok:false,status:502,statusText:'Bad',json:async()=>{throw new SyntaxError();}}); await assert.rejects(run('x'),e=>e.detail==='Bad');
   const error=new DOMException('Aborted','AbortError'); globalThis.fetch=async()=>({ok:false,json:async()=>{throw error;}}); await assert.rejects(run('x'),e=>e===error);
  }
 } finally { globalThis.fetch=old; }
});
