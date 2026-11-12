import {readFileSync,existsSync} from 'node:fs';
import {createRequire} from 'node:module';
import {strict as assert} from 'node:assert';
import {test} from 'node:test';
const require=createRequire(import.meta.url),React=require('../../../web/node_modules/react');
const {act,create}=require('../../../web/node_modules/react-test-renderer'),ts=require('../../../web/node_modules/typescript');
globalThis.IS_REACT_ACT_ENVIRONMENT=true;globalThis.__draftPanelReviewReact=React;
const encode=s=>`data:text/javascript;base64,${Buffer.from(s).toString('base64')}`;
const reactUrl=encode('export const {useState,useRef,useEffect,useCallback}=globalThis.__draftPanelReviewReact;');
const cache=new Map();
function load(url){if(cache.has(url.href))return cache.get(url.href);let output=ts.transpileModule(readFileSync(url,'utf8'),{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext,jsx:ts.JsxEmit.React}}).outputText;output=output.replace(/from ['"]([^'"]+)['"]/g,(whole,path)=>{if(path==='react')return `from '${reactUrl}'`;if(!path.startsWith('.'))return whole;let child=new URL(path+'.ts',url);if(!existsSync(child))child=new URL(path+'.tsx',url);return `from '${load(child)}'`;});const result=encode('const React=globalThis.__draftPanelReviewReact;\n'+output);cache.set(url.href,result);return result;}
const Panel=(await import(load(new URL('../../../web/src/DraftPanel.tsx',import.meta.url)))).default;
const text=node=>typeof node==='string'?node:(node?.children??[]).map(text).join('');
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return{promise,resolve,reject};};
const grid={revision:2,segments:[{start_time_sec:1,start_beat_q:0,bpm_quarter:120,grid_division:4}]};
const base={id:'chart',revision:2,grid_revision:2,events:[]};
const chart={id:'chart',revision:3,grid_revision:2,events:[{id:'new',time_ref:{kind:'beat',beat_q:2},notes:[{id:'note',lane:0}],provenance:{created_by:'generated'}}]};
const view=()=>({project:{id:'project',revision:5,audio:{path:'audio/audio-asset/song.ogg',sha256:'a'.repeat(64)}},grid:{revision:8,segments:[{start_time_sec:0,start_beat_q:0,bpm_quarter:60,grid_division:4}]},charts:[structuredClone(base)]});
const inspection=(extra={})=>({draft:{id:'draft-a',base_chart:{id:'chart',revision:2},chart:structuredClone(chart),diff:{added_event_ids:['new']},validation:[],generation_run:{request:{audio_asset_id:'audio-asset',audio_sha256:'a'.repeat(64)},output:{backend_id:'single_lane',backend_version:'1',seed:5,diagnostics:[]}}},base_chart:structuredClone(base),grid:structuredClone(grid),applicable:true,rejected:false,reasons:[],...extra});
async function setup(){const oldFetch=globalThis.fetch,requests=[],mutations=[],auditions=[];let lease='one',root;globalThis.fetch=(url,options)=>{const d=deferred();requests.push({url,options,...d});return d.promise;};const writerHeaders=()=>lease?{'X-Tab-Id':'tab','X-Writer-Lease':lease}:null;let props={view:view(),draftId:'draft-a',disabled:false,writerHeaders,mutate:(...args)=>{const d=deferred();mutations.push({args,...d});return d.promise;},onAudition:value=>auditions.push(value)};await act(async()=>{root=create(React.createElement(Panel,props));});return{requests,mutations,auditions,get props(){return props;},get tree(){return root.root;},get text(){return text(root.toJSON());},lease(value){lease=value;},button(label){return root.root.findAllByType('button').find(button=>text(button)===label);},async click(label){await act(async()=>{this.button(label).props.onClick();});},async update(next){props={...props,...next};await act(async()=>{root.update(React.createElement(Panel,props));});},async answer(index,payload=inspection()){await act(async()=>{requests[index].resolve({ok:true,status:200,json:async()=>payload});});},async fail(index){await act(async()=>{requests[index].reject(new TypeError('lost reply'));});},async close(){if(root){await act(async()=>{root.unmount();});root=null;}globalThis.fetch=oldFetch;},async unmount(){await act(async()=>{root.unmount();});root=null;}};}

test('inspection binds to project/draft/revision immediately and stale GETs abort',async()=>{
 for(const changed of ['revision','draft','project','unmount']){
  const h=await setup();try{
   if(changed==='unmount'){await h.unmount();assert(h.requests[0].options.signal.aborted);await h.answer(0);assert.equal(h.requests.length,1);continue;}
   const next=structuredClone(h.props.view);if(changed==='revision')next.project.revision++;if(changed==='project')next.project.id='project-b';await h.update({view:next,...changed==='draft'?{draftId:'draft-b'}:{}});
   assert(h.requests[0].options.signal.aborted);await h.answer(0);assert(!h.button('Применить'));await h.answer(1);assert(h.button('Применить'));
   const newer=structuredClone(h.props.view);newer.project.revision++;await h.update({view:newer});assert(!h.button('Применить'));
  }finally{await h.close();}
 }
});

test('Apply has a synchronous duplicate guard, captures base view, and requires canonical semantic confirmation',async()=>{
 const h=await setup();try{
  await h.answer(0);const apply=h.button('Применить').props.onClick;await act(async()=>{apply();apply();});assert.equal(h.mutations.length,1);assert.equal(h.mutations[0].args[0],'apply_draft');assert.deepEqual(h.mutations[0].args[1],{draft_id:'draft-a'});assert.equal(h.mutations[0].args[2],h.props.view);
  await act(async()=>{h.mutations[0].resolve(undefined);});assert(!h.text.includes('Черновик применён.'));
  const wrong=structuredClone(h.props.view);wrong.project.revision++;await h.update({view:wrong});assert(!h.text.includes('Черновик применён.'));
  const right=structuredClone(wrong);right.project.revision++;right.charts=[{events:structuredClone(chart.events),grid_revision:99,revision:99,id:'chart'}];await h.update({view:right});assert(h.text.includes('Черновик применён.'));
 }finally{await h.close();}
});

test('Reject retries preserve UUID and use fresh headers; duplicate clicks send one request',async()=>{
 const h=await setup();try{
  await h.answer(0);const reject=h.button('Отклонить').props.onClick;await act(async()=>{reject();reject();});assert.equal(h.requests.length,2);assert.equal(h.requests[1].options.headers['X-Writer-Lease'],'one');const first=h.requests[1].options.body;await h.fail(1);h.lease('two');await h.click('Отклонить');assert.equal(h.requests.length,3);assert.equal(h.requests[2].options.body,first);assert.equal(h.requests[2].options.headers['X-Writer-Lease'],'two');
  await h.answer(2,{draft_id:'draft-a',decision_id:JSON.parse(first).decision_id,draft_sha256:'hash'});assert.equal(h.requests.length,4);await h.answer(3,inspection({rejected:true,applicable:false,reasons:['Draft is rejected']}));assert(h.text.includes('Черновик отклонён.'));assert(h.button('Применить').props.disabled);assert(h.button('Отклонить').props.disabled);
 }finally{await h.close();}
});

test('stale same-audio draft stays readable and audition uses frozen grid/chart; both audio identities gate it',async()=>{
 const h=await setup();try{
  const frozen=inspection({applicable:false,reasons:['STALE_REVISION']});await h.answer(0,frozen);assert(h.button('Применить').props.disabled);assert(!h.button('Прослушать').props.disabled);assert(h.text.includes('STALE_REVISION'));await h.click('Прослушать');assert.deepEqual(h.auditions[0],{projectId:'project',draftId:'draft-a',chart:frozen.draft.chart,grid:frozen.grid,audioAssetId:'audio-asset',audioSha256:'a'.repeat(64)});
  for(const mode of ['hash','asset']){const next=structuredClone(h.props.view);if(mode==='hash')next.project.audio.sha256='b'.repeat(64);else{next.project.audio.sha256='a'.repeat(64);next.project.audio.path='audio/other-asset/song.ogg';}await h.update({view:next});assert(h.button('Прослушать').props.disabled,mode);}
 }finally{await h.close();}
});

test('changing draft while a decision is pending must not leave the new draft permanently busy',async()=>{
 for(const operation of ['Применить','Отклонить']){
  const h=await setup();try{
   await h.answer(0);await h.click(operation);await h.update({draftId:'draft-b'});const newGet=h.requests.length-1;await h.answer(newGet,inspection());
   if(operation==='Применить')await act(async()=>{h.mutations[0].resolve(undefined);});else await h.answer(1,{draft_id:'draft-a'});
   assert(!h.button('Применить').props.disabled,`${operation}: new draft remains busy`);assert(!h.button('Отклонить').props.disabled);
  }finally{await h.close();}
 }
});

test('Reject request aborts when the panel unmounts',async()=>{
 const h=await setup();try{await h.answer(0);await h.click('Отклонить');await h.unmount();assert(h.requests[1].options.signal?.aborted,'Reject fetch must receive an abort signal that cleanup aborts');}finally{await h.close();}
});

test('pending Apply belongs to its draft and an old completion cannot unlock a newer Apply',async()=>{
 const h=await setup();try{
  await h.answer(0);await h.click('Применить');await h.update({draftId:'draft-b'});await h.answer(1);
  assert(!h.button('Применить').props.disabled,'draft B must be usable while draft A settles');
  await h.click('Применить');assert.equal(h.mutations.length,2);assert(h.button('Применить').props.disabled);
  await act(async()=>{h.mutations[0].resolve(undefined);});assert(h.button('Применить').props.disabled,'old completion must not release draft B');
  await h.click('Применить');assert.equal(h.mutations.length,2,'draft B duplicate guard must remain active');
  await act(async()=>{h.mutations[1].resolve(undefined);});assert(!h.button('Применить').props.disabled);
 }finally{await h.close();}
});

for(const action of ['Применить','Отклонить'])test(`old completion cannot unlock the same draft after returning (${action})`,async()=>{
 const h=await setup();try{
  await h.answer(0);await h.click(action);const first=action==='Применить'?h.mutations[0]:h.requests.at(-1);
  await h.update({draftId:'draft-b'});await h.answer(h.requests.length-1);await h.click(action);
  await h.update({draftId:'draft-a'});await h.answer(h.requests.length-1);assert(!h.button(action).props.disabled);await h.click(action);
  const count=()=>action==='Применить'?h.mutations.length:h.requests.filter(request=>request.url.endsWith('/reject')).length;
  assert.equal(count(),3);assert(h.button(action).props.disabled);
  await act(async()=>{if(action==='Применить')first.resolve(undefined);else first.reject(new TypeError('old lost reply'));});
  assert(h.button(action).props.disabled,'old A completion must not release newer A operation');
  await h.click(action);assert.equal(count(),3,'new A operation must keep its synchronous duplicate guard');
 }finally{await h.close();}
});

for(const action of ['Применить','Отклонить'])test(`completed decision does not remain busy after viewing another draft (${action})`,async()=>{
 const h=await setup();try{
  await h.answer(0);await h.click(action);const first=action==='Применить'?h.mutations[0]:h.requests.at(-1);
  await h.update({draftId:'draft-b'});await h.answer(h.requests.length-1);
  await act(async()=>{if(action==='Применить')first.resolve(undefined);else first.reject(new TypeError('old lost reply'));});
  await h.update({draftId:'draft-a'});await h.answer(h.requests.length-1);
  assert(!h.button('Применить').props.disabled,'settled A must not remain busy on return');assert(!h.button('Отклонить').props.disabled);
 }finally{await h.close();}
});

test('Reject retry keeps UUID after canonical revision changes',async()=>{
 const h=await setup();try{
  await h.answer(0);await h.click('Отклонить');const firstBody=h.requests[1].options.body;await h.fail(1);
  const next=structuredClone(h.props.view);next.project.revision++;await h.update({view:next});await h.answer(h.requests.length-1);h.lease('new-lease');
  await h.click('Отклонить');const retry=h.requests.at(-1);assert.equal(retry.options.body,firstBody);assert.equal(retry.options.headers['X-Writer-Lease'],'new-lease');
 }finally{await h.close();}
});

for(const change of ['project','draft','unmount'])for(const reply of ['success','failure'])test(`Reject cleanup aborts on ${change} and ignores late ${reply}`,async()=>{
 const h=await setup();try{
  await h.answer(0);await h.click('Отклонить');const pending=h.requests[1];
  if(change==='project'){const next=structuredClone(h.props.view);next.project.id='other-project';await h.update({view:next});}
  if(change==='draft')await h.update({draftId:'draft-b'});
  if(change==='unmount')await h.unmount();else await h.answer(h.requests.length-1);
  assert(pending.options.signal?.aborted,`${change} must abort Reject`);const count=h.requests.length;
  await act(async()=>{if(reply==='success')pending.resolve({ok:true,status:200,json:async()=>({draft_id:'draft-a'})});else pending.reject(new TypeError('cleanup-late-failure'));});
  assert.equal(h.requests.length,count,'late Reject reply must not reload inspection');
  if(change!=='unmount'){assert(!h.text.includes('cleanup-late-failure'));assert(!h.button('Применить').props.disabled);}
 }finally{await h.close();}
});

for(const reply of ['success','failure'])test(`Reject cleanup ignores old ${reply} after returning to the same draft`,async()=>{
 const h=await setup();try{
  await h.answer(0);await h.click('Отклонить');const original=h.requests[1];
  await h.update({draftId:'draft-b'});await h.answer(h.requests.length-1);await h.click('Отклонить');
  await h.update({draftId:'draft-a'});await h.answer(h.requests.length-1);await h.click('Отклонить');const count=h.requests.length;
  await act(async()=>{if(reply==='success')original.resolve({ok:true,status:200,json:async()=>({draft_id:'draft-a'})});else original.reject(new TypeError('obsolete-A-error'));});
  assert.equal(h.requests.length,count,'old A reply must not reload current A inspection');assert(!h.text.includes('obsolete-A-error'),'old A failure must not enter current state');assert(h.button('Отклонить').props.disabled,'new A operation must stay pending');
 }finally{await h.close();}
});

test('Reject revision change keeps the request live and success refreshes current inspection',async()=>{
 const h=await setup();try{
  await h.answer(0);await h.click('Отклонить');const reject=h.requests[1],body=reject.options.body;
  const next=structuredClone(h.props.view);next.project.revision++;await h.update({view:next});await h.answer(2);
  assert(!reject.options.signal?.aborted,'canonical revision alone must not abort Reject');assert(h.button('Отклонить').props.disabled);
  await h.answer(1,{draft_id:'draft-a',decision_id:JSON.parse(body).decision_id,draft_sha256:'hash'});
  assert.equal(h.requests.length,4,'successful same-draft decision must refresh latest inspection');assert.equal(h.requests[3].options.method,'GET');await h.answer(3,inspection({rejected:true,applicable:false,reasons:['Draft is rejected']}));assert(h.text.includes('Черновик отклонён.'));assert(h.button('Применить').props.disabled);
 }finally{await h.close();}
});

test('Reject revision failure stays visible and retry preserves UUID with fresh headers',async()=>{
 const h=await setup();try{
  await h.answer(0);await h.click('Отклонить');const body=h.requests[1].options.body;
  const next=structuredClone(h.props.view);next.project.revision++;await h.update({view:next});await h.answer(2);await h.fail(1);
  assert(h.text.includes('lost reply'),'same-draft failure must remain visible after revision change');h.lease('revision-retry-lease');await h.click('Отклонить');assert.equal(h.requests.at(-1).options.body,body);assert.equal(h.requests.at(-1).options.headers['X-Writer-Lease'],'revision-retry-lease');
 }finally{await h.close();}
});

test('diff seek controls disappear when either current audio identity differs',async()=>{
 const h=await setup();try{
  const seeks=[];await h.update({onSeek:seconds=>seeks.push(seconds)});await h.answer(0);
  const seekButtons=()=>h.tree.findAllByType('button').filter(button=>text(button)==='Перейти');assert.equal(seekButtons().length,1);await act(async()=>{seekButtons()[0].props.onClick();});assert.deepEqual(seeks,[2]);
  for(const mode of ['hash','asset']){const next=structuredClone(h.props.view);next.project.audio.sha256=mode==='hash'?'b'.repeat(64):'a'.repeat(64);next.project.audio.path=mode==='asset'?'audio/other-asset/song.ogg':'audio/audio-asset/song.ogg';await h.update({view:next});assert.equal(seekButtons().length,0,mode);}
 }finally{await h.close();}
});
