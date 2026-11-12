import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { strict as assert } from 'node:assert';
import { test } from 'node:test';
const require = createRequire(import.meta.url);
const React = require('../../../web/node_modules/react');
const { act, create } = require('../../../web/node_modules/react-test-renderer');
const ts = require('../../../web/node_modules/typescript');
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
globalThis.__generationReview = { React };
let source = readFileSync(new URL('../../../web/src/useGenerationJob.ts', import.meta.url), 'utf8');
source = source.replace(/import .* from 'react';/, 'const {useCallback,useEffect,useRef,useState}=globalThis.__generationReview.React;')
 .replace(/import .* from '.\/generationClient';/, 'const startGeneration=(...args)=>globalThis.__generationReview.startGeneration(...args);')
 .replace(/import .* from '.\/uploads';/, 'const getJob=(...args)=>globalThis.__generationReview.getJob(...args); const cancelJob=(...args)=>globalThis.__generationReview.cancelJob(...args);');
const compiled = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } }).outputText;
const { useGenerationJob } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`);
const deferred = () => { let resolve, reject; const promise = new Promise((yes,no)=>{resolve=yes;reject=no;}); return { promise, resolve, reject }; };
const flush = async () => { await act(async()=>{}); };
const input = (id='first') => ({id,project_id:'project',seed:5,target:{name:'Target',allowed_subdivisions:[1,2]},analysis:{id:'analysis',revision:1},range_q:{start_q:0,end_q:8}});
async function setup() {
 const env = globalThis.__generationReview;
 let current; let projectId='project'; let lease=0;
 const starts=[], polls=[], cancels=[], timers=new Map();
 const oldSet=globalThis.setTimeout, oldClear=globalThis.clearTimeout;
 globalThis.setTimeout=(fn,delay,...rest)=>{ if(delay!==1000)return oldSet(fn,delay,...rest); const id={};timers.set(id,fn);return id; };
 globalThis.clearTimeout=id=>{if(!timers.delete(id))oldClear(id);};
 env.startGeneration=(project,body,headers,signal)=>{const d=deferred();starts.push({project,body,headers,signal,...d});return d.promise;};
 env.getJob=id=>{const d=deferred();polls.push({id,...d});return d.promise;};
 env.cancelJob=(id,headers)=>{const d=deferred();cancels.push({id,headers,...d});return d.promise;};
 const writerHeaders=()=>({'X-Tab-Id':'tab','X-Writer-Lease':`lease-${++lease}`});
 function Host(){current=useGenerationJob(projectId,writerHeaders);return null;}
 let root; await act(async()=>{root=create(React.createElement(Host));});
 return {
  get hook(){return current;}, starts,polls,cancels,timers,
  async start(value){let result;await act(async()=>{result=current.start(value);});return {result};},
  async finishStart(index=starts.length-1,id='job'){await act(async()=>{starts[index].resolve({job_id:id,draft_id:`draft-${id}`});});},
  async tick(){assert.equal(timers.size,1);const [id,fn]=timers.entries().next().value;timers.delete(id);await act(async()=>{fn();});},
  async reset(){await act(async()=>{current.reset();});},
  async project(){projectId='other';await act(async()=>{root.update(React.createElement(Host));});},
  async unmount(){if(root){await act(async()=>{root.unmount();});root=null;}},
  async close(){await this.unmount();globalThis.setTimeout=oldSet;globalThis.clearTimeout=oldClear;},
 };
}

test('request snapshot is detached and deeply frozen; retries reuse it; reset captures new meaning', async()=>{
 const h=await setup();try{
  const caller=input();await h.start(caller);
  const body=h.starts[0].body;assert.notEqual(body,caller);assert.notEqual(body.target,caller.target);
  assert(Object.isFrozen(body));assert(Object.isFrozen(body.target));assert(Object.isFrozen(body.target.allowed_subdivisions));
  caller.seed=90;caller.target.allowed_subdivisions.push(16);assert.equal(body.seed,5);assert.deepEqual(body.target.allowed_subdivisions,[1,2]);
  await act(async()=>{h.starts[0].reject(new Error('lost reply'));});
  await h.start(input('changed'));assert.equal(h.starts[1].body,body);assert.equal(h.starts[1].headers['X-Writer-Lease'],'lease-2');assert(h.starts[0].signal.aborted);
  await h.finishStart();assert.equal(h.hook.request,body);
  let cancellation;await act(async()=>{cancellation=h.hook.cancel();});assert.equal(h.cancels[0].headers['X-Writer-Lease'],'lease-3');
  await act(async()=>{h.cancels[0].resolve(true);});await cancellation;
  await h.reset();assert.equal(h.hook.request,null);assert.equal(h.hook.jobId,'');assert.equal(h.hook.draftId,'');assert.equal(h.hook.job,null);assert.equal(h.timers.size,0);
  await h.start(input('new'));assert.equal(h.starts[2].body.id,'new');assert.notEqual(h.starts[2].body,body);
 }finally{await h.close();}
});

test('polls each second, retries poll failures, stops at each terminal state, and restarts same job ID',async()=>{
 for(const state of ['completed','failed','cancelled','interrupted']){
  const h=await setup();try{
   await h.start(input());assert(h.hook.busy);await h.finishStart();assert(!h.hook.busy);assert.equal(h.polls.length,1);
   await act(async()=>{h.polls[0].resolve({state:'running'});});assert.equal(h.hook.job.state,'running');assert.equal(h.timers.size,1);
   await h.tick();assert.equal(h.polls.length,2);
   await act(async()=>{h.polls[1].reject(new Error('temporary'));});assert.match(h.hook.error,/temporary/);assert.equal(h.timers.size,1);
   await h.tick();await act(async()=>{h.polls[2].resolve({state});});assert.equal(h.hook.job.state,state);assert.equal(h.timers.size,0);
   await h.start(input('changed'));assert.equal(h.hook.jobId,'');assert.equal(h.hook.draftId,'');assert.equal(h.hook.job,null);
   await h.finishStart(1,'job');assert.equal(h.polls.length,4);assert.equal(h.polls[3].id,'job');assert.equal(h.hook.error,'');
  }finally{await h.close();}
 }
});

test('reset, project change and unmount suppress late POST, poll and cancellation results or errors',async()=>{
 for(const action of ['reset','project','unmount'])for(const operation of ['start','poll','cancel'])for(const failure of [false,true]){
  const h=await setup();try{
   await h.start(input());let late=h.starts[0];
   if(operation!=='start'){await h.finishStart();late=h.polls[0];}
   if(operation==='cancel'){await act(async()=>{h.hook.cancel();});late=h.cancels[0];}
   await h[action]();const snapshot={jobId:h.hook.jobId,draftId:h.hook.draftId,job:h.hook.job,error:h.hook.error,busy:h.hook.busy};
   await act(async()=>{if(failure)late.reject(new Error('late failure'));else late.resolve(operation==='start'?{job_id:'stale',draft_id:'stale'}:operation==='poll'?{state:'completed'}:true);});
   assert.deepEqual({jobId:h.hook.jobId,draftId:h.hook.draftId,job:h.hook.job,error:h.hook.error,busy:h.hook.busy},snapshot,`${action}/${operation}/${failure}`);
   assert.equal(h.timers.size,0);
   assert(h.starts[0].signal.aborted);
  }finally{await h.close();}
 }
});

test('new start suppresses old pending POST and poll completions',async()=>{
 for(const failure of [false,true])for(const operation of ['start','poll']){
  const h=await setup();try{
   await h.start(input());let late=h.starts[0];
   if(operation==='poll'){await h.finishStart();late=h.polls[0];}
   await h.start(input('different'));
   await act(async()=>{if(failure)late.reject(new Error('old error'));else late.resolve(operation==='start'?{job_id:'old',draft_id:'old'}:{state:'completed'});});
   assert.equal(h.hook.jobId,'');assert.equal(h.hook.job,null);assert.equal(h.hook.error,'');assert(h.hook.busy);assert.equal(h.timers.size,0);
   await h.finishStart(1,'new');assert.equal(h.hook.jobId,'new');
  }finally{await h.close();}
 }
});
