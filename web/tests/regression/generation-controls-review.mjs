import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { strict as assert } from 'node:assert';
import { test } from 'node:test';
const require=createRequire(import.meta.url);
const React=require('../../../web/node_modules/react');
const {act,create}=require('../../../web/node_modules/react-test-renderer');
const ts=require('../../../web/node_modules/typescript');
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
globalThis.__controlsReviewReact=React;
const source="const React=globalThis.__controlsReviewReact;\n"+readFileSync(new URL('../../../web/src/GenerationControls.tsx',import.meta.url),'utf8');
const output=ts.transpileModule(source,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext,jsx:ts.JsxEmit.React}}).outputText;
const {GenerationControls}=await import(`data:text/javascript;base64,${Buffer.from(output).toString('base64')}`);
const text=node=>typeof node==='string'?node:(node?.children??[]).map(text).join('');
const allText=root=>text(root.toJSON());
const form=Object.freeze({lane:'0',subdivision:'1',startSec:'',endSec:'1e',seed:'-'});
const defaults=()=>({storedTarget:false,form,onChange:()=>{},locked:false,prerequisite:'',error:'',preparing:false,jobId:'',jobState:null,jobError:'',draftId:'',canStart:true,canRetry:false,canCancel:false,canReset:false,onStart:()=>{},onRetry:()=>{},onCancel:()=>{},onReset:()=>{}});
async function mount(props){let root;await act(async()=>{root=create(React.createElement(GenerationControls,props));});return root;}
const update=async(root,props)=>{await act(async()=>{root.update(React.createElement(GenerationControls,props));});};
const close=async root=>{await act(async()=>{root.unmount();});};

test('all editable settings have labels, preserve incomplete numeric text, and return detached string changes',async()=>{
 const changes=[],props={...defaults(),onChange:next=>changes.push(next)},root=await mount(props);
 try{
  const labels=root.root.findAllByType('label');assert.equal(labels.length,5);
  for(const label of labels){assert(text(label).trim().length>0);assert.equal(label.findAll(node=>node.type==='input'||node.type==='select').length,1);}
  const controls=root.root.findAll(node=>node.type==='input'||node.type==='select');assert(controls.every(control=>control.props.disabled===false));
  assert.deepEqual(controls.map(control=>control.props.value),['0','1','','1e','-']);
  const keys=['lane','subdivision','startSec','endSec','seed'];
  for(const [index,value]of ['3','4','-','1e-',''].entries()){
   await act(async()=>{controls[index].props.onChange({target:{value}});});
   const changed=changes.at(-1);assert.notEqual(changed,form);assert.equal(changed[keys[index]],value);assert.equal(typeof changed[keys[index]],'string');
   for(const key of keys.filter(key=>key!==keys[index]))assert.equal(changed[key],form[key]);
  }
  assert.deepEqual(form,{lane:'0',subdivision:'1',startSec:'',endSec:'1e',seed:'-'});
  await update(root,{...props,locked:true});assert(root.root.findAll(node=>node.type==='input'||node.type==='select').every(control=>control.props.disabled===true));
 }finally{await close(root);}
});

test('action eligibility controls each button independently and dispatches the corresponding action',async()=>{
 const events=[],props=defaults(),actions=[['canStart','onStart','Начать'],['canRetry','onRetry','Повторить'],['canCancel','onCancel','Отменить'],['canReset','onReset','Новый запрос']];
 for(const [,handler]of actions)props[handler]=()=>events.push(handler);
 const root=await mount(props);try{
  for(const [eligible,handler,label]of actions){
   const state={...props,locked:true,canStart:false,canRetry:false,canCancel:false,canReset:false,[eligible]:true};await update(root,state);
   const buttons=root.root.findAllByType('button');assert.equal(buttons.filter(button=>!button.props.disabled).length,1);
   const enabled=buttons.find(button=>!button.props.disabled);assert.equal(text(enabled),label);assert.equal(enabled.props.type,'button');
   await act(async()=>{enabled.props.onClick();});assert.equal(events.at(-1),handler);
  }
 }finally{await close(root);}
});

test('preparation, accepted pending job, terminal states, errors and draft identity render without inventing success',async()=>{
 const root=await mount({...defaults(),prerequisite:'Approve the timing grid'});try{
  assert.match(allText(root),/Approve the timing grid/);assert.equal(root.root.findAllByProps({role:'status'}).length,0);
  await update(root,{...defaults(),locked:true,preparing:true,canStart:false,canReset:true});assert.match(text(root.root.findByProps({role:'status'})),/Подготовка/);assert.doesNotMatch(allText(root),/Задача:/);
  await update(root,{...defaults(),locked:true,canStart:false,canCancel:true,canReset:true,jobId:'accepted-job',jobState:null,draftId:'reserved-draft'});
  assert.match(allText(root),/accepted-job/);assert.match(allText(root),/ожидание первого ответа/);assert.match(allText(root),/reserved-draft/);assert.doesNotMatch(allText(root),/Подготовка|completed/);
  for(const jobState of ['running','completed','failed','cancelled','interrupted']){
   await update(root,{...defaults(),locked:true,canStart:false,canRetry:true,canReset:true,jobId:'job-id',jobState,draftId:'draft-id',error:'POST response lost',jobError:'Worker failed'});
   const rendered=allText(root);assert(rendered.includes(jobState));assert(rendered.includes('job-id'));assert(rendered.includes('draft-id'));
   const alerts=root.root.findAllByProps({role:'alert'}).map(text);assert.equal(alerts.length,2);assert(alerts.some(message=>message.includes('POST response lost')));assert(alerts.some(message=>message.includes('Worker failed')));
   assert(root.root.findAll(node=>node.type==='input'||node.type==='select').every(control=>control.props.disabled));
  }
 }finally{await close(root);}
});

test('stored target disables only subdivision before capture and explains its source',async()=>{
 const root=await mount({...defaults(),storedTarget:true});try{
  const selects=root.root.findAllByType('select');assert.equal(selects[0].props.disabled,false);assert.equal(selects[1].props.disabled,true);assert(root.root.findAllByType('input').every(input=>input.props.disabled===false));assert.match(allText(root),/сохранённый профиль цели карты/);
 }finally{await close(root);}
});
