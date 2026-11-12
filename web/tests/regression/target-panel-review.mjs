import {readFileSync, existsSync} from 'node:fs';
import {createRequire} from 'node:module';
import {strict as assert} from 'node:assert';
import {test} from 'node:test';
const require=createRequire(import.meta.url), React=require('../../../web/node_modules/react');
const {act,create}=require('../../../web/node_modules/react-test-renderer'), ts=require('../../../web/node_modules/typescript');
globalThis.IS_REACT_ACT_ENVIRONMENT=true; globalThis.__targetPanelReviewReact=React;
const encode=s=>`data:text/javascript;base64,${Buffer.from(s).toString('base64')}`;
const reactUrl=encode('export const {useState,useRef,useEffect,useCallback}=globalThis.__targetPanelReviewReact;');
const cache=new Map();
function load(url){if(cache.has(url.href))return cache.get(url.href); let output=ts.transpileModule(readFileSync(url,'utf8'),{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext,jsx:ts.JsxEmit.React}}).outputText; output=output.replace(/from ['"]([^'"]+)['"]/g,(whole,path)=>{if(path==='react')return `from '${reactUrl}'`;if(!path.startsWith('.'))return whole;let child=new URL(path+'.ts',url);if(!existsSync(child))child=new URL(path+'.tsx',url);return `from '${load(child)}'`;});const result=encode('const React=globalThis.__targetPanelReviewReact;\n'+output);cache.set(url.href,result);return result;}
const Panel=(await import(load(new URL('../../../web/src/TargetPanel.tsx',import.meta.url)))).default;
const id=n=>`00000000-0000-4000-8000-${String(n).padStart(12,'0')}`;
const text=node=>typeof node==='string'?node:(node?.children??[]).map(text).join('');
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return{promise,resolve,reject};};
const fixture=()=>({project:{id:id(1),revision:1,grid_revision:2,chart_heads:[{id:id(4),revision:3},{id:id(5),revision:3}]},grid:{revision:2},charts:[{id:id(4),revision:3,target_profile:null},{id:id(5),revision:3,target_profile:null}]});
const caps={backend_id:'single_lane',backend_version:'1',modes:['single_lane'],target_fields:['allowed_subdivisions']};
const profile={id:id(9),version:4,name:'Stored',allowed_subdivisions:[2,4],intended_rank:5};
async function setup(stored=null){const oldFetch=globalThis.fetch,requests=[],previews=[],mutations=[];let root;
 globalThis.fetch=(url,options)=>{const d=deferred();requests.push({url,options,...d});return d.promise;};
 const view=fixture();if(stored)view.charts[0].target_profile={id:stored.id,revision:stored.version};
 let props={view,selectedChartId:id(4),disabled:false,preview:(...args)=>{const d=deferred();previews.push({args,...d});return d.promise;},mutate:(...args)=>{const d=deferred();mutations.push({args,...d});return d.promise;}};
 await act(async()=>{root=create(React.createElement(Panel,props));});
 return{requests,previews,mutations,get props(){return props;},get text(){return text(root.toJSON());},get inputs(){return root.root.findAllByType('input');},button(label){return root.root.findAllByType('button').find(b=>text(b)===label);},async click(label){await act(async()=>{this.button(label).props.onClick();});},async update(next){props={...props,...next};await act(async()=>{root.update(React.createElement(Panel,props));});},async answer(index,payload){await act(async()=>{requests[index].resolve({ok:true,status:200,json:async()=>payload});});},async ready(storedTarget=stored,capabilities=caps){const offset=requests.length-2, chart=props.view.charts.find(c=>c.id===props.selectedChartId);await this.answer(offset,{project_id:props.view.project.id,project_revision:props.view.project.revision,chart_id:chart.id,chart_revision:chart.revision,target_ref:chart.target_profile,target:storedTarget});await this.answer(offset+1,capabilities);},async name(value){await act(async()=>{root.root.findAllByType('input').find(n=>!n.props.type).props.onChange({target:{value}});});},async previewDone(index=previews.length-1,base=props.view){await act(async()=>{previews[index].resolve(structuredClone(base));});},async close(){await act(async()=>{root.unmount();});globalThis.fetch=oldFetch;}};
}
const previewLabel='Предпросмотр цели',applyLabel='Применить показанные изменения';

test('new target is usable by the advertised backend without selecting extra controls',async()=>{const h=await setup();try{await h.ready();await h.name('New');await h.click(previewLabel);const target=h.previews[0].args[1].target;assert.equal(target.version,1);assert.match(target.id,/^[0-9a-f-]{36}$/);assert(target.allowed_subdivisions==null||target.allowed_subdivisions.length>0,'empty subdivisions produce a target rejected by single_lane');}finally{await h.close();}});

test('stored target increments version, captures exact base, invalidates preview on edit, and clears explicitly',async()=>{const h=await setup(profile);try{await h.ready();await h.name('Updated');await h.click(previewLabel);assert.deepEqual(h.previews[0].args[1].target,{...profile,version:5,name:'Updated'});await h.previewDone();await h.click(applyLabel);assert.equal(h.mutations[0].args[2],h.props.view);assert.deepEqual(h.mutations[0].args[1],h.previews[0].args[1]);await act(async()=>{h.mutations[0].resolve(undefined);});await h.name('Edited again');assert(!h.button(applyLabel));await h.click('Предпросмотр удаления цели');assert.equal(h.previews[1].args[1].target,null);}finally{await h.close();}});

for(const change of ['chart','revision','project'])test(`old read is aborted and ignored after ${change}`,async()=>{const h=await setup();try{const original=h.requests[0],base=structuredClone(h.props.view);if(change==='chart')await h.update({selectedChartId:id(5)});else{const view=structuredClone(h.props.view);if(change==='revision')view.project.revision++;else view.project.id=id(99);await h.update({view});}assert(original.options.signal.aborted);await h.answer(0,{project_id:base.project.id,project_revision:base.project.revision,chart_id:id(4),chart_revision:3,target_ref:null,target:profile});await h.answer(1,caps);assert(!h.button(previewLabel));await h.ready(null);assert(h.button(previewLabel));}finally{await h.close();}});

test('source change invalidates pending preview',async()=>{const h=await setup(profile);try{await h.ready();await h.click(previewLabel);const base=h.props.view;const view=structuredClone(base);view.project.revision++;await h.update({view});await h.ready();await h.previewDone(0,base);assert(!h.button(applyLabel));}finally{await h.close();}});

test('preview has a synchronous duplicate guard',async()=>{const h=await setup(profile);try{await h.ready();const run=h.button(previewLabel).props.onClick;await act(async()=>{run();run();});assert.equal(h.previews.length,1);}finally{await h.close();}});

test('Apply has a synchronous duplicate guard',async()=>{const h=await setup(profile);try{await h.ready();await h.click(previewLabel);await h.previewDone();const apply=h.button(applyLabel).props.onClick;await act(async()=>{apply();apply();});assert.equal(h.mutations.length,1);}finally{await h.close();}});

test('returning to the same chart cannot restore an old preview over dirty input',async()=>{const h=await setup(profile);try{await h.ready();await h.name('Old draft');await h.click(previewLabel);const base=h.props.view;await h.update({selectedChartId:id(5)});await h.ready(null);await h.update({selectedChartId:id(4)});await h.ready(profile);await h.name('Current draft');await h.previewDone(0,base);assert(!h.button(applyLabel),'old preview must not become applicable after chart A/B/A');}finally{await h.close();}});

test('old Apply completion cannot unlock a newer Apply after chart A/B/A',async()=>{const h=await setup(profile);try{await h.ready();await h.click(previewLabel);await h.previewDone();await h.click(applyLabel);await h.update({selectedChartId:id(5)});await h.ready(null);await h.update({selectedChartId:id(4)});await h.ready(profile);await h.click(previewLabel);await h.previewDone();await h.click(applyLabel);assert(h.button(applyLabel).props.disabled);await act(async()=>{h.mutations[0].resolve(undefined);});assert(h.button(applyLabel).props.disabled,'old Apply must not unlock pending current Apply');}finally{await h.close();}});

test('lease loss blocks Apply without an automatic write',async()=>{const h=await setup(profile);try{await h.ready();await h.click(previewLabel);await h.update({disabled:true});await h.previewDone();if(h.button(applyLabel))assert(h.button(applyLabel).props.disabled);assert.equal(h.mutations.length,0);await h.update({disabled:false});if(h.button(applyLabel)){await h.click(applyLabel);assert.equal(h.mutations[0].args[2],h.props.view);}}finally{await h.close();}});

test('preview and Apply errors remain visible and preserve unsaved input',async()=>{const h=await setup(profile);try{await h.ready();await h.name('Unsaved');await h.click(previewLabel);await act(async()=>{h.previews[0].reject(new Error('preview offline'));});assert(h.text.includes('preview offline'));await h.click(previewLabel);assert.equal(h.previews[1].args[1].target.name,'Unsaved');await h.previewDone();await h.click(applyLabel);await act(async()=>{h.mutations[0].reject(new Error('STALE_REVISION'));});assert(h.text.includes('STALE_REVISION'));}finally{await h.close();}});


test('intended rank preference is saved even when it is not a generator capability',async()=>{const h=await setup(profile);try{await h.ready();await act(async()=>{h.inputs.find(n=>n.props.type==='number').props.onChange({target:{value:'8'}});});await h.click(previewLabel);assert.equal(h.previews[0].args[1].target.intended_rank,8,'editable intended_rank is profile metadata and must be saved');}finally{await h.close();}});

test('empty checkbox selection is blocked without silently adding a division',async()=>{
 const h=await setup();try{
  await h.ready();await h.name('New');const checkboxes=()=>h.inputs.filter(n=>n.props.type==='checkbox');
  await act(async()=>{checkboxes()[0].props.onChange({target:{checked:false}});});
  assert(checkboxes().every(n=>!n.props.checked));
  if(!h.button(previewLabel).props.disabled){
   await h.click(previewLabel);
   if(h.previews.length===0){
    assert(h.text.includes('Выберите хотя бы одно'),'blocked input must explain the required correction');
    assert(!h.button(applyLabel));assert.equal(h.mutations.length,0);
   }else{
    const proposed=h.previews[0].args[1].target.allowed_subdivisions;
    const visible=checkboxes().filter(n=>n.props.checked).map(n=>Number(text(n.parent)));
    assert(proposed==null||JSON.stringify(proposed)===JSON.stringify(visible),'a proposed default must be visible');
   }
  }
  const count=h.previews.length;
  await act(async()=>{checkboxes()[0].props.onChange({target:{checked:true}});});
  await h.click(previewLabel);assert.equal(h.previews.length,count+1,'validation must release its synchronous guard');
  assert.deepEqual(h.previews.at(-1).args[1].target.allowed_subdivisions,[1]);
 }finally{await h.close();}
});

test('hidden unsupported subdivision control does not add a new capability',async()=>{const h=await setup();try{await h.ready(null,{...caps,target_fields:[]});await h.name('No controls');assert.equal(h.inputs.filter(n=>n.props.type==='checkbox').length,0);await h.click(previewLabel);assert(h.previews[0].args[1].target.allowed_subdivisions==null);}finally{await h.close();}});

test('new target uses a new UUID and a stored target preserves the full profile',async()=>{const full={...profile,movement_profile:'other-backend',ornament_budget:0.2,recovery_policy:'preserve-me'};const h=await setup(full);try{await h.ready();await h.name('Only name changed');await h.click(previewLabel);assert.deepEqual(h.previews[0].args[1].target,{...full,version:5,name:'Only name changed'});}finally{await h.close();}});
