import {readFileSync,existsSync} from 'node:fs';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import {strict as assert} from 'node:assert';
import {test} from 'node:test';
const require=createRequire(import.meta.url),React=require('../../../web/node_modules/react');
const {act,create}=require('../../../web/node_modules/react-test-renderer'),ts=require('../../../web/node_modules/typescript');
globalThis.IS_REACT_ACT_ENVIRONMENT=true;globalThis.__panelReviewReact=React;
const encode=s=>`data:text/javascript;base64,${Buffer.from(s).toString('base64')}`;
const reactUrl=encode('export const {useState,useRef,useEffect,useCallback}=globalThis.__panelReviewReact;');
const cache=new Map();
function load(url){
 if(cache.has(url.href))return cache.get(url.href);
 let output=ts.transpileModule(readFileSync(url,'utf8'),{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext,jsx:ts.JsxEmit.React}}).outputText;
 output=output.replace(/from ['"]([^'"]+)['"]/g,(whole,path)=>{
  if(path==='react')return `from '${reactUrl}'`;
  if(!path.startsWith('.'))return whole;
  let child=new URL(path+'.ts',url);if(!existsSync(child))child=new URL(path+'.tsx',url);
  return `from '${load(child)}'`;
 });
 const result=encode('const React=globalThis.__panelReviewReact;\n'+output);cache.set(url.href,result);return result;
}
const Panel=(await import(load(new URL('../../../web/src/GenerationPanel.tsx',import.meta.url)))).default;
const {GenerationControls}=await import(load(new URL('../../../web/src/GenerationControls.tsx',import.meta.url)));
const id=n=>`00000000-0000-4000-8000-${String(n).padStart(12,'0')}`;
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return{promise,resolve,reject};};
const caps={backend_id:'single_lane',backend_version:'1',modes:['single_lane'],target_fields:['allowed_subdivisions']};
function fixture(){return{project:{id:id(1),revision:1,grid_revision:1,analysis:{id:id(2),revision:0},plan_inputs:null,audio:{path:`audio/${id(3)}/song.ogg`,sha256:'a'.repeat(64),duration_sec:10,sample_rate:48000,channels:2,frames:480000,subtype:'VORBIS'}},grid:{revision:1,review:'approved',segments:[{start_time_sec:0,start_beat_q:0,bpm_quarter:120,grid_division:4,review:'approved'}]},charts:[{id:id(4),revision:0,grid_revision:1},{id:id(5),revision:0,grid_revision:1}]};}
function plan(view){return{project_id:view.project.id,project_revision:view.project.revision,grid_revision:view.grid.revision,analysis:view.project.analysis,plan_inputs:view.project.plan_inputs,plan:{id:id(6),revision:0,audio_asset_id:id(3),audio_sha256:view.project.audio.sha256,analysis:view.project.analysis,grid_revision:view.grid.revision,sections:[{start_sec:0,end_sec:10}]}};}
async function setup(){
 const oldFetch=globalThis.fetch,oldSet=globalThis.setTimeout,oldClear=globalThis.clearTimeout,oldUuid=crypto.randomUUID,ids=[],requests=[],timers=new Map();
 crypto.randomUUID=()=>{const value=oldUuid.call(crypto);ids.push(value);return value;};
 globalThis.fetch=(url,options)=>{const d=deferred();requests.push({url,options,...d});return d.promise;};
 globalThis.setTimeout=(fn,ms,...args)=>{if(ms!==1000)return oldSet(fn,ms,...args);const timer={};timers.set(timer,fn);return timer;};globalThis.clearTimeout=timer=>{if(!timers.delete(timer))oldClear(timer);};
 let props={view:fixture(),selectedChartId:id(4),disabled:false,writerHeaders:()=>({'X-Tab-Id':'tab','X-Writer-Lease':'lease'}),onDraft:()=>{}},root;
 await act(async()=>{root=create(React.createElement(Panel,{...props,key:props.view.project.id}));});
 const controls=()=>root.root.findByType(GenerationControls).props;
 return{requests,timers,ids,get ui(){return controls();},get view(){return props.view;},async call(action){await act(async()=>{controls()[action]();});},async update(next){props={...props,...next};await act(async()=>{root.update(React.createElement(Panel,{...props,key:props.view.project.id}));});},async answer(index,payload,ok=true){await act(async()=>{requests[index].resolve({ok,status:ok?200:503,json:async()=>payload});});},async fail(index){await act(async()=>{requests[index].reject(new TypeError('network lost'));});},async prepared(){await this.answer(0,caps);await this.answer(1,plan(props.view));},async close(){if(root){await act(async()=>{root.unmount();});root=null;}globalThis.fetch=oldFetch;globalThis.setTimeout=oldSet;globalThis.clearTimeout=oldClear;crypto.randomUUID=oldUuid;},async unmount(){await act(async()=>{root.unmount();});root=null;}};
}

test('captures before GET, blocks duplicate Start, preserves GET-retry IDs, and freezes exact POST retries',async()=>{
 const h=await setup();try{
  const start=h.ui.onStart;await act(async()=>{start();start();});assert.equal(h.requests.length,2);assert.equal(h.ids.length,2);assert(h.ui.locked);assert(h.ui.preparing);assert(h.ui.canReset);
  await h.answer(0,caps);await h.fail(1);assert(h.ui.canRetry);assert(h.ui.locked);
  await h.call('onRetry');assert.equal(h.requests.length,4);await h.answer(2,caps);await h.answer(3,plan(h.view));assert.equal(h.requests.length,5);
  const firstBody=h.requests[4].options.body;assert.equal(h.ids.length,2);assert.equal(JSON.parse(firstBody).id,h.ids[0]);assert.equal(JSON.parse(firstBody).target.id,h.ids[1]);await h.fail(4);assert(h.ui.canRetry);assert(!h.ui.preparing);assert(h.ui.locked);
  const changed=structuredClone(h.view);changed.project.revision++;changed.charts[0].revision++;
  await h.update({view:changed,selectedChartId:id(5)});await h.call('onRetry');assert.equal(h.requests.length,6);assert.equal(h.requests[5].options.body,firstBody);assert.equal(JSON.parse(firstBody).chart.id,id(4));
  await h.answer(5,{job_id:'job',draft_id:'draft'});assert.equal(h.requests[6].url,'/api/jobs/job');assert(!h.ui.preparing);assert(h.ui.locked);assert(h.ui.canCancel);assert(!h.ui.canStart);assert(!h.ui.canRetry);assert.equal(h.ui.jobState,null);
  await h.call('onReset');assert(!h.ui.locked);assert(h.ui.canStart);assert.equal(h.requests.filter(r=>r.url.endsWith('/cancel')).length,0);
 }finally{await h.close();}
});

test('current canonical source, selected chart, and write eligibility are checked after GET',async()=>{
 for(const change of ['source','chart','disabled','headers']){
  const h=await setup();try{
   await h.call('onStart');const captured=structuredClone(h.view);
   if(change==='source'){const view=structuredClone(h.view);view.project.revision++;await h.update({view});}
   if(change==='chart')await h.update({selectedChartId:id(5)});
   if(change==='disabled')await h.update({disabled:true});
   if(change==='headers')await h.update({writerHeaders:()=>null});
   await h.answer(0,caps);await h.answer(1,plan(captured));assert.equal(h.requests.length,2,change);assert(h.ui.error);assert(h.ui.locked);assert(!h.ui.preparing);assert(h.ui.canReset);
  }finally{await h.close();}
 }
});

test('reset, keyed project change, and unmount suppress late GET success or failure',async()=>{
 for(const action of ['reset','project','unmount'])for(const failure of [false,true]){
  const h=await setup();try{
   const original=structuredClone(h.view);await h.call('onStart');
   if(action==='reset')await h.call('onReset');
   if(action==='project'){const view=fixture();view.project.id=id(99);await h.update({view});}
   if(action==='unmount')await h.unmount();
   assert(h.requests[0].options.signal.aborted);assert(h.requests[1].options.signal.aborted);
   if(failure)await h.fail(1);else{await h.answer(0,caps);await h.answer(1,plan(original));}
   assert.equal(h.requests.length,2);if(action!=='unmount'){assert.equal(h.ui.error,'');assert(!h.ui.locked);assert(!h.ui.preparing);}
  }finally{await h.close();}
 }
});

test('cancellation uses the job endpoint, retains captured form, and terminal polling disables Cancel',async()=>{
 const h=await setup();try{
  await h.call('onStart');await h.prepared();await h.answer(2,{job_id:'job',draft_id:'draft'});
  await h.call('onCancel');assert.equal(h.requests[4].url,'/api/jobs/job/cancel');assert.equal(h.requests[4].options.headers['X-Writer-Lease'],'lease');await h.answer(4,true);assert(h.ui.locked);assert.equal(h.ui.jobState,null);
  await h.answer(3,{state:'cancelled'});assert.equal(h.ui.jobState,'cancelled');assert(!h.ui.canCancel);assert(h.ui.canReset);assert(h.ui.locked);assert.equal(h.timers.size,0);
 }finally{await h.close();}
});

function target(view){return{project_id:view.project.id,project_revision:view.project.revision,chart_id:id(4),chart_revision:view.charts[0].revision,target_ref:structuredClone(view.charts[0].target_profile),target:{id:id(20),version:3,name:'Stored',intended_rank:7,allowed_subdivisions:[2,4],ornament_budget:null,movement_profile:null}};}
async function withStoredTarget(h){const view=structuredClone(h.view);view.charts[0].target_profile={id:id(20),revision:3};await h.update({view});return view;}

test('stored target disables subdivision and is posted completely; ambiguous POST retries use exact frozen target without GET',async()=>{
 const h=await setup();try{
  const captured=await withStoredTarget(h);assert(h.ui.storedTarget);await h.call('onStart');assert.equal(h.requests.length,3);assert(h.requests[2].url.endsWith(`/charts/${id(4)}/target`));
  const profile=target(captured);await h.answer(0,caps);await h.answer(1,plan(captured));await h.answer(2,profile);assert.equal(h.requests.length,4);const body=h.requests[3].options.body;assert.deepEqual(JSON.parse(body).target,profile.target);profile.target.allowed_subdivisions.push(1);profile.target.name='Changed reply';await h.fail(3);
  const latest=structuredClone(h.view);latest.project.revision++;latest.charts[0].target_profile={id:id(21),revision:4};await h.update({view:latest});await h.call('onRetry');assert.equal(h.requests.length,5);assert.equal(h.requests[4].options.body,body);
 }finally{await h.close();}
});

test('stored target GET failure and missing target response never submit an ad-hoc fallback',async()=>{
 for(const mode of ['failure','missing']){const h=await setup();try{
  const captured=await withStoredTarget(h);await h.call('onStart');await h.answer(0,caps);await h.answer(1,plan(captured));if(mode==='failure')await h.fail(2);else await h.answer(2,{...target(captured),target:null});assert.equal(h.requests.length,3);assert(h.ui.error);assert(h.ui.canRetry);assert(h.ui.locked);
 }finally{await h.close();}}
});

test('stale target GET cannot submit after source replacement, reset, project switch or unmount',async()=>{
 for(const mode of ['source','reset','project','unmount']){const h=await setup();try{
  const captured=await withStoredTarget(h);await h.call('onStart');await h.answer(0,caps);await h.answer(1,plan(captured));
  if(mode==='source'){const view=structuredClone(h.view);view.charts[0].target_profile.revision++;await h.update({view});}
  if(mode==='reset')await h.call('onReset');if(mode==='project'){const view=fixture();view.project.id=id(99);await h.update({view});}if(mode==='unmount')await h.unmount();
  await h.answer(2,target(captured));assert.equal(h.requests.length,3);if(mode==='source')assert(h.ui.error);else assert(h.requests[2].options.signal.aborted);
 }finally{await h.close();}}
});
