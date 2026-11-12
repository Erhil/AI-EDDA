import {readFileSync,existsSync} from 'node:fs';
import {createRequire} from 'node:module';
import {strict as assert} from 'node:assert';
import {test} from 'node:test';
const require=createRequire(import.meta.url),React=require('../../../web/node_modules/react');
const {act,create}=require('../../../web/node_modules/react-test-renderer'),ts=require('../../../web/node_modules/typescript');
globalThis.IS_REACT_ACT_ENVIRONMENT=true;globalThis.__archivePanelReact=React;
const encode=s=>`data:text/javascript;base64,${Buffer.from(ts.transpileModule(s,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext,jsx:ts.JsxEmit.React}}).outputText).toString('base64')}`,reactUrl=encode('export const {useState,useRef,useEffect}=globalThis.__archivePanelReact;'),cache=new Map();
function load(url){if(cache.has(url.href))return cache.get(url.href);let source=readFileSync(url,'utf8');source=source.replace(/from ['"]([^'"]+)['"]/g,(whole,path)=>{if(path==='react')return `from '${reactUrl}'`;if(!path.startsWith('.'))return whole;let child=new URL(path+'.ts',url);if(!existsSync(child))child=new URL(path+'.tsx',url);return `from '${load(child)}'`;});const result=encode('const React=globalThis.__archivePanelReact;\n'+source);cache.set(url.href,result);return result;}
const Panel=(await import(load(new URL('../../../web/src/ArchiveImportPanel.tsx',import.meta.url)))).default;
const text=node=>typeof node==='string'?node:(node?.children??[]).map(text).join('');
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return{promise,resolve,reject};};
const file=(name='archive.zip',size=24)=>({name,size});
const completed={id:'job',kind:'project_import',state:'completed',progress:null,context:{request_id:'00000000-0000-4000-8000-000000000001',archive_kind:'native',file_name:'archive.zip',file_size:24},publication_result:{project_id:'imported-project',revision:7,archive_kind:'native'}};
async function setup(){const oldFetch=globalThis.fetch,oldXHR=globalThis.XMLHttpRequest,oldSet=globalThis.setTimeout,oldClear=globalThis.clearTimeout,requests=[],uploads=[],timers=new Map(),completedCalls=[],opened=[],lost=[];let root,props={onComplete:()=>completedCalls.push('first'),onOpen:id=>opened.push(id),onSessionLost:()=>lost.push('first')};
 globalThis.fetch=(url,options)=>{const d=deferred();requests.push({url,options,...d});return d.promise;};class Xhr{constructor(){this.headers={};this.upload={};uploads.push(this);}open(...args){this.opened=args;}setRequestHeader(name,value){this.headers[name]=value;}send(body){this.body=body;}abort(){this.aborted=true;this.onabort();}}globalThis.XMLHttpRequest=Xhr;
 globalThis.setTimeout=(fn,ms,...args)=>{if(ms!==1000)return oldSet(fn,ms,...args);const id={};timers.set(id,fn);return id;};globalThis.clearTimeout=id=>{if(!timers.delete(id))oldClear(id);};await act(async()=>{root=create(React.createElement(Panel,props));});
 return{requests,uploads,timers,completedCalls,opened,lost,get text(){return text(root.toJSON());},get tree(){return root.root;},button(label){return root.root.findAllByType('button').find(button=>text(button)===label);},async click(label){await act(async()=>{this.button(label).props.onClick();});},async choose(value){await act(async()=>{root.root.findByProps({type:'file'}).props.onChange({target:{files:value?[value]:[]}});});},async kind(value){await act(async()=>{root.root.findByType('select').props.onChange({target:{value}});});},async update(next){props={...props,...next};await act(async()=>{root.update(React.createElement(Panel,props));});},async uploaded(index=uploads.length-1,status=202,payload={job_id:'job',upload_required:true,verifying:false}){await act(async()=>{const xhr=uploads[index];xhr.status=status;xhr.responseText=JSON.stringify(payload);xhr.onload();});},async answer(index,payload,status=200){await act(async()=>{requests[index].resolve({ok:status>=200&&status<300,status,json:async()=>payload});});},async tick(){assert.equal(timers.size,1);const [id,fn]=timers.entries().next().value;timers.delete(id);await act(async()=>{fn();});},async close(){if(root){await act(async()=>{root.unmount();});root=null;}globalThis.fetch=oldFetch;globalThis.XMLHttpRequest=oldXHR;globalThis.setTimeout=oldSet;globalThis.clearTimeout=oldClear;},async unmount(){await act(async()=>{root.unmount();});root=null;}};
}

test('owner import requires no project or lease; success refreshes once and opens only explicitly with latest callbacks',async()=>{const h=await setup();try{
 await h.choose(file());const start=h.button('Импортировать').props.onClick;await act(async()=>{start();start();});assert.equal(h.uploads.length,1);await h.uploaded();assert.equal(h.requests.length,1);assert.equal(h.requests[0].options.headers,undefined);await h.update({onComplete:()=>h.completedCalls.push('latest'),onOpen:id=>h.opened.push(`latest:${id}`)});assert.equal(h.requests.length,1);await h.answer(0,completed);assert.deepEqual(h.completedCalls,['latest']);assert.deepEqual(h.opened,[]);assert.equal(h.timers.size,0);await h.update({onComplete:()=>h.completedCalls.push('new-render')});assert.deepEqual(h.completedCalls,['latest']);await h.click('Открыть проект');assert.deepEqual(h.opened,['latest:imported-project']);
}finally{await h.close();}});

test('uncertain retry freezes File, kind and UUID; explicit reset captures a new operation',async()=>{const h=await setup();try{
 const original=file('first.zip');await h.kind('game');await h.choose(original);await h.click('Импортировать');const first=h.uploads[0];await act(async()=>{first.onerror();});assert(h.button('Повторить'));assert(h.tree.findByProps({type:'file'}).props.disabled);assert(h.tree.findByType('select').props.disabled);await h.click('Повторить');assert.equal(h.uploads[1].body,original);assert.deepEqual(h.uploads[1].headers,first.headers);await h.click('Новый импорт');assert(h.uploads[1].aborted);await h.choose(original);await h.click('Импортировать');assert.notEqual(h.uploads[2].headers['X-Request-Id'],first.headers['X-Request-Id']);
}finally{await h.close();}});

test('IMPORT_IN_PROGRESS attaches one poll; cancelled publication false remains polled until completed',async()=>{const h=await setup();try{
 await h.choose(file());await h.click('Импортировать');await h.uploaded(0,409,{detail:{code:'IMPORT_IN_PROGRESS',job_id:'existing-job'}});assert.equal(h.requests.length,1);assert.equal(h.requests[0].url,'/api/jobs/existing-job');assert.deepEqual(h.completedCalls,[]);await h.answer(0,{id:'existing-job',kind:'project_import',state:'publication_pending',progress:null});assert.equal(h.timers.size,1);await h.click('Отменить обработку');assert.equal(h.requests[1].url,'/api/jobs/existing-job/cancel');await h.answer(1,false);assert(h.text.includes('Публикацию уже нельзя отменить'));assert.equal(h.timers.size,1);await h.tick();await h.answer(2,{...completed,id:'existing-job'});assert.equal(h.completedCalls.length,1);assert.deepEqual(h.opened,[]);
}finally{await h.close();}});

test('local upload Cancel aborts the body and permits retry without calling server cancellation',async()=>{const h=await setup();try{
 await h.choose(file());await h.click('Импортировать');const id=h.uploads[0].headers['X-Request-Id'];await h.click('Отменить загрузку');assert(h.uploads[0].aborted);assert(h.button('Повторить'));assert.equal(h.requests.length,0);await h.click('Повторить');assert.equal(h.uploads[1].headers['X-Request-Id'],id);
}finally{await h.close();}});

for(const exit of ['reset','unmount'])for(const reply of ['success','failure'])test(`${exit} aborts polling and ignores late ${reply} without implicit server cancel/open`,async()=>{const h=await setup();try{
 await h.choose(file());await h.click('Импортировать');await h.uploaded();if(exit==='reset')await h.click('Новый импорт');else await h.unmount();assert(h.requests[0].options.signal.aborted);assert.equal(h.timers.size,0);if(reply==='success')await h.answer(0,completed);else await act(async()=>{h.requests[0].reject(new Error('obsolete'));});assert.equal(h.completedCalls.length,0);assert.deepEqual(h.opened,[]);assert.equal(h.requests.filter(r=>r.url.endsWith('/cancel')).length,0);
}finally{await h.close();}});

test('reset clears scheduled polling timers and stale upload completion cannot start another poll',async()=>{const h=await setup();try{
 await h.choose(file());await h.click('Импортировать');await h.click('Новый импорт');await h.uploaded(0);assert.equal(h.requests.length,0);await h.choose(file());await h.click('Импортировать');await h.uploaded(1);await h.answer(0,{id:'job',state:'running',progress:{phase:'verify'}});assert.equal(h.timers.size,1);await h.click('Новый импорт');assert.equal(h.timers.size,0);
}finally{await h.close();}});

test('interrupted state and coordinator failures are visible, retry uses the same UUID',async()=>{const h=await setup();try{
 await h.choose(file());await h.click('Импортировать');const id=h.uploads[0].headers['X-Request-Id'];await h.uploaded();await h.answer(0,{id:'job',kind:'project_import',state:'interrupted',progress:null,error:'verification failed',coordinator_error:'storage unavailable'});assert(h.text.includes('interrupted'));assert(h.text.includes('verification failed'));assert(h.text.includes('storage unavailable'));await h.click('Повторить');assert.equal(h.uploads[1].headers['X-Request-Id'],id);
}finally{await h.close();}});

test('completed job without publication result permits retry with the same request ID',async()=>{const h=await setup();try{
 await h.choose(file());await h.click('Импортировать');const id=h.uploads[0].headers['X-Request-Id'];await h.uploaded();await h.answer(0,{id:'job',kind:'project_import',state:'completed',progress:null});assert(h.button('Повторить'),'terminal no-result state must permit exact retry');await h.click('Повторить');assert.equal(h.uploads[1].headers['X-Request-Id'],id);assert.equal(h.completedCalls.length,0);
}finally{await h.close();}});

test('401 upload and poll responses use the current session-loss callback',async()=>{for(const stage of ['upload','poll']){const h=await setup();try{
 await h.choose(file());await h.click('Импортировать');await h.update({onSessionLost:()=>h.lost.push('latest')});if(stage==='upload')await h.uploaded(0,401,{detail:'Authentication required'});else{await h.uploaded();await h.answer(0,{detail:'Authentication required'},401);}assert.deepEqual(h.lost,['latest']);assert.deepEqual(h.opened,[]);
}finally{await h.close();}}});

for(const selected of [file('archive.txt'),file('empty.zip',0),file('huge.zip',1024**3+1)])test(`local file validation rejects ${selected.name}`,async()=>{const h=await setup();try{await h.choose(selected);assert(h.button('Импортировать').props.disabled,`${selected.name} must be rejected locally`);assert.equal(h.uploads.length,0);}finally{await h.close();}});

test('exact 1 GiB ZIP boundary is accepted locally',async()=>{const h=await setup();try{await h.choose(file('limit.ZIP',1024**3));assert(!h.button('Импортировать').props.disabled);}finally{await h.close();}});
