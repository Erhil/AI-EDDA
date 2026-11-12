import {readFileSync} from 'node:fs';
import {createRequire} from 'node:module';
import {strict as assert} from 'node:assert';
import {test} from 'node:test';
const ts=createRequire(import.meta.url)('../../../web/node_modules/typescript');
const moduleUrl=source=>`data:text/javascript;base64,${Buffer.from(ts.transpileModule(source,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext}}).outputText).toString('base64')}`;
const apiUrl=moduleUrl(readFileSync(new URL('../../../web/src/api.ts',import.meta.url),'utf8'));
const {ApiError}=await import(apiUrl);
const client=await import(moduleUrl(readFileSync(new URL('../../../web/src/archiveClient.ts',import.meta.url),'utf8').replace("from './api'",`from '${apiUrl}'`)));
const requestId='00000000-0000-4000-8000-000000000001';
const file=new File(['archive bytes'],'song ю #%.zip',{type:'application/zip'});
function trackedSignal(){const controller=new AbortController(),signal=controller.signal,active=new Set(),originalAdd=signal.addEventListener.bind(signal),originalRemove=signal.removeEventListener.bind(signal);signal.addEventListener=(name,listener,options)=>{if(name==='abort')active.add(listener);return originalAdd(name,listener,options);};signal.removeEventListener=(name,listener,options)=>{if(name==='abort')active.delete(listener);return originalRemove(name,listener,options);};return{controller,signal,active};}
function xhrHarness(send){const old=globalThis.XMLHttpRequest,instances=[];class Xhr{constructor(){this.headers={};this.upload={};this.abortCalls=0;instances.push(this);}open(...args){this.opened=args;}setRequestHeader(name,value){this.headers[name]=value;}send(body){this.body=body;send?.(this);}abort(){this.abortCalls++;this.onabort?.();}respond(status,body,statusText=''){this.status=status;this.statusText=statusText;this.responseText=typeof body==='string'?body:JSON.stringify(body);this.onload();}}globalThis.XMLHttpRequest=Xhr;return{instances,restore(){globalThis.XMLHttpRequest=old;}};}

test('both archive kinds send raw File with only canonical import headers and report progress',async()=>{
 const h=xhrHarness();try{for(const kind of ['native','game']){const c=trackedSignal(),progress=[],pending=client.uploadProjectArchive(kind,requestId,file,(...args)=>progress.push(args),c.signal),xhr=h.instances.at(-1);assert.deepEqual(xhr.opened,['POST','/api/projects/imports']);assert.equal(xhr.body,file);assert.deepEqual(xhr.headers,{'Content-Type':'application/octet-stream','X-Request-Id':requestId,'X-Archive-Kind':kind,'X-File-Name':encodeURIComponent(file.name),'X-File-Size':String(file.size)});assert.equal(c.active.size,1);xhr.upload.onprogress({loaded:5,total:file.size});assert.deepEqual(progress,[[5,file.size]]);const response={job_id:'job',upload_required:true,verifying:kind==='native'};xhr.respond(202,response);assert.deepEqual(await pending,response);assert.equal(c.active.size,0);c.controller.abort();assert.equal(xhr.abortCalls,0);}
 }finally{h.restore();}
});

test('every XHR terminal path releases the abort listener and preserves original failure detail',async()=>{
 const h=xhrHarness();try{for(const mode of ['http','malformed-http','malformed-success','network','abort']){const c=trackedSignal(),pending=client.uploadProjectArchive('native',requestId,file,()=>{},c.signal),xhr=h.instances.at(-1);let expected;
  if(mode==='http'){expected={code:'IMPORT_IN_PROGRESS',job_id:'existing-job',extra:{preserve:true}};xhr.respond(409,{detail:expected});}if(mode==='malformed-http')xhr.respond(503,'HTML unavailable','Unavailable');if(mode==='malformed-success')xhr.respond(202,'not JSON');if(mode==='network')xhr.onerror();if(mode==='abort')c.controller.abort();
  await assert.rejects(pending,error=>mode==='http'?error instanceof ApiError&&error.status===409&&JSON.stringify(error.detail)===JSON.stringify(expected):mode==='malformed-http'?error instanceof ApiError&&error.status===503&&error.detail==='Unavailable':mode==='malformed-success'?error instanceof SyntaxError:mode==='network'?error instanceof TypeError:error.name==='AbortError');assert.equal(c.active.size,0,mode);const count=xhr.abortCalls;c.controller.abort();assert.equal(xhr.abortCalls,count,mode);
 }
 }finally{h.restore();}
});

test('pre-abort creates no XHR; synchronous send failure and synchronous completion both clean up',async()=>{
 let h=xhrHarness();try{const c=trackedSignal();c.controller.abort();await assert.rejects(client.uploadProjectArchive('game',requestId,file,()=>{},c.signal),error=>error.name==='AbortError');assert.equal(h.instances.length,0);assert.equal(c.active.size,0);}finally{h.restore();}
 const failure=new TypeError('synchronous send failure');h=xhrHarness(()=>{throw failure;});try{const c=trackedSignal();await assert.rejects(client.uploadProjectArchive('native',requestId,file,()=>{},c.signal),error=>error===failure);assert.equal(c.active.size,0);c.controller.abort();assert.equal(h.instances[0].abortCalls,0);}finally{h.restore();}
 h=xhrHarness(xhr=>xhr.respond(202,{job_id:'synchronous-job',upload_required:true,verifying:false}));try{const c=trackedSignal(),response=await client.uploadProjectArchive('native',requestId,file,()=>{},c.signal);assert.equal(response.job_id,'synchronous-job');assert.equal(c.active.size,0);}finally{h.restore();}
});

test('first XHR terminal event wins even if late load/error/abort events follow',async()=>{
 const h=xhrHarness();try{for(const first of ['load','abort','network']){const c=trackedSignal(),pending=client.uploadProjectArchive('game',requestId,file,()=>{},c.signal),xhr=h.instances.at(-1);if(first==='load')xhr.respond(202,{job_id:'first',upload_required:true,verifying:false});if(first==='abort')c.controller.abort();if(first==='network')xhr.onerror();xhr.respond(202,{job_id:'late',upload_required:true,verifying:false});xhr.onerror();xhr.onabort();if(first==='load')assert.equal((await pending).job_id,'first');else await assert.rejects(pending,error=>first==='abort'?error.name==='AbortError':error instanceof TypeError);assert.equal(c.active.size,0);}
 }finally{h.restore();}
});

test('job read/cancel encode IDs, use same-origin session without lease headers, and preserve errors/aborts',async()=>{
 const old=globalThis.fetch;try{for(const [method,suffix,run]of [['GET','',client.getImportJob],['POST','/cancel',client.cancelImportJob]]){
  const c=new AbortController();globalThis.fetch=async(path,init)=>{assert.equal(path,`/api/jobs/job%20%2F%3F%25${suffix}`);assert.equal(init.method,method);assert.equal(init.credentials,'same-origin');assert.equal(init.signal,c.signal);assert.equal(init.headers,undefined);assert.equal(init.body,undefined);return{ok:true,json:async()=>method==='GET'?{state:'completed',publication_result:{project_id:'new-project',revision:0,archive_kind:'native'}}:true};};await run('job /?%',c.signal);
  const detail={code:'IMPORT_IN_PROGRESS',job_id:'existing'};globalThis.fetch=async()=>({ok:false,status:409,json:async()=>({detail})});await assert.rejects(run('job'),error=>error instanceof ApiError&&error.status===409&&error.detail===detail);
  for(const phase of ['fetch','success-body','error-body']){const c=new AbortController(),aborted=new DOMException('Aborted','AbortError'),wait=()=>new Promise((resolve,reject)=>c.signal.addEventListener('abort',()=>reject(aborted),{once:true}));globalThis.fetch=async()=>phase==='fetch'?wait():{ok:phase==='success-body',status:503,json:wait};const pending=run('job',c.signal);await new Promise(resolve=>setImmediate(resolve));c.abort();await assert.rejects(pending,error=>error===aborted);}
 }
 }finally{globalThis.fetch=old;}
});
