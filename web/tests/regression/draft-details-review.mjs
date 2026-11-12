import {readFileSync} from 'node:fs';
import {createRequire} from 'node:module';
import {strict as assert} from 'node:assert';
import {test} from 'node:test';
const require=createRequire(import.meta.url),React=require('../../../web/node_modules/react');
const {act,create}=require('../../../web/node_modules/react-test-renderer'),ts=require('../../../web/node_modules/typescript');
globalThis.IS_REACT_ACT_ENVIRONMENT=true;globalThis.__draftReviewReact=React;
const encode=s=>`data:text/javascript;base64,${Buffer.from(s).toString('base64')}`;
const compile=s=>ts.transpileModule(s,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext,jsx:ts.JsxEmit.React}}).outputText;
const timeline=encode(compile(readFileSync(new URL('../../../web/src/timeline.ts',import.meta.url),'utf8')));
const source=readFileSync(new URL('../../../web/src/DraftDetails.tsx',import.meta.url),'utf8').replace("import { useState } from 'react';",'const React=globalThis.__draftReviewReact; const {useState}=React;').replace("from './timeline'",`from '${timeline}'`);
const Details=(await import(encode(compile(source)))).default;
const text=node=>typeof node==='string'?node:(node?.children??[]).map(text).join('');
const deepFreeze=value=>{if(value&&typeof value==='object'){Object.values(value).forEach(deepFreeze);Object.freeze(value);}return value;};
const event=(id,lane=0,q=2)=>({id,time_ref:{kind:'beat',beat_q:q},notes:[{id:`note-${id}`,lane,provenance:{created_by:'generated',generation_run_id:'run',source_ids:['note-source']}}],provenance:{created_by:'generated',generation_run_id:'run',source_ids:['event-source']}});
function fixture(){
 const added={...event('added',3),time_ref:{kind:'audio',time_sec:9.25}};
 const removed=event('removed',1,6),before={...event('changed',0,2),time_ref:{kind:'beat',beat_q:2,micro_offset_sec:.125}},after={...event('changed',2,5),time_ref:{kind:'beat',beat_q:5,micro_offset_sec:.25}};
 return{baseChart:{id:'base',events:[removed,before]},grid:{revision:1,segments:[{start_time_sec:1,start_beat_q:0,bpm_quarter:120,grid_division:4},{start_time_sec:3,start_beat_q:4,bpm_quarter:60,grid_division:4}]},draft:{id:'draft',base_chart:{id:'base',revision:0},chart:{events:[added,after]},diff:{added_event_ids:['added'],removed_event_ids:['removed'],changed_event_ids:['changed']},validation:[{severity:'error',code:'CHART_ERROR',message:'Chart diagnostic',event_ids:['added'],note_ids:['note-added']}],generation_run:{output:{backend_id:'single_lane',backend_version:'1',seed:37,diagnostics:[{severity:'warning',code:'TIMING_DIAGNOSTIC',message:'Provider diagnostic',event_ids:['changed'],note_ids:['note-changed']}]}}},reasons:['The draft is stale']};
}
async function mount(props){let root;await act(async()=>{root=create(React.createElement(Details,props));});return root;}
async function close(root){await act(async()=>{root.unmount();});}
const row=root=>root.root.findAllByType('h4').filter(h=>/^(Добавлено|Удалено|Изменено):/.test(text(h)));

test('diff rows use supplied before and after charts and the frozen tempo map for display and seeking',async()=>{
 const props=fixture(),seeks=[];deepFreeze(props);const original=JSON.stringify(props);const root=await mount({...props,onSeek:seconds=>seeks.push(seconds)});try{
  const headings=row(root);assert.equal(headings.length,3);assert.deepEqual(headings.map(text),['Добавлено: added','Удалено: removed','Изменено: changed']);
  const rows=headings.map(h=>text(h.parent));assert.match(rows[0],/После:.*9\.250 с/);assert.doesNotMatch(rows[0],/До:/);assert.match(rows[1],/До:.*5\.000 с/);assert.doesNotMatch(rows[1],/После:/);
  assert.match(rows[2],/До:.*2\.125 с.*После:.*4\.250 с/);
  const rendered=text(root.toJSON());for(const expected of ['single_lane','seed: 37','Chart diagnostic','CHART_ERROR','The draft is stale','event-source','note-source','generation_run_id'])assert(rendered.includes(expected),expected);
  const seeksButtons=root.root.findAllByType('button').filter(button=>text(button)==='Перейти');assert.equal(seeksButtons.length,4);for(const button of seeksButtons)await act(async()=>{button.props.onClick();});assert.deepEqual(seeks,[9.25,5,2.125,4.25]);assert.equal(JSON.stringify(props),original);
 }finally{await close(root);}
});

test('human lane labels match the editor labels 1 through 4',async()=>{
 const props=fixture();props.draft.chart.events[0].notes=[0,1,2,3].map((lane,index)=>({id:`lane-${index}`,lane}));const root=await mount(props);try{assert.match(text(row(root)[0].parent),/дорожки 1, 2, 3, 4/);}finally{await close(root);}
});

test('both provider and chart diagnostics remain visible',async()=>{
 const root=await mount(fixture());try{const rendered=text(root.toJSON());assert(rendered.includes('Chart diagnostic'));assert(rendered.includes('Provider diagnostic'),'provider warning must be visible');assert(rendered.includes('TIMING_DIAGNOSTIC'));}finally{await close(root);}
});

test('pagination reaches all added, removed and changed IDs without duplicates',async()=>{
 const props=fixture();props.draft.diff={added_event_ids:Array.from({length:101},(_,i)=>`a-${i}`),removed_event_ids:Array.from({length:52},(_,i)=>`r-${i}`),changed_event_ids:Array.from({length:4},(_,i)=>`c-${i}`)};
 props.draft.chart.events=[...props.draft.diff.added_event_ids,...props.draft.diff.changed_event_ids].map(id=>event(id));props.baseChart.events=[...props.draft.diff.removed_event_ids,...props.draft.diff.changed_event_ids].map(id=>event(id));
 const root=await mount(deepFreeze(props));try{
  const found=[];let pages=0;while(true){pages++;const headings=row(root);assert.equal(headings.length,pages<4?50:7);found.push(...headings.map(text));const buttons=root.root.findAllByType('button'),next=buttons.find(button=>text(button)==='Далее');assert(next);if(next.props.disabled)break;await act(async()=>{next.props.onClick();});}
  assert.equal(pages,4);assert.equal(found.length,157);assert.equal(new Set(found).size,157);assert(found.includes('Добавлено: a-100'));assert(found.includes('Удалено: r-51'));assert(found.includes('Изменено: c-3'));
  const back=root.root.findAllByType('button').find(button=>text(button)==='Назад');await act(async()=>{back.props.onClick();});assert.match(text(root.toJSON()),/Страница 3 из 4/);
 }finally{await close(root);}
});

test('missing diff, empty diff, missing IDs and invalid timestamps produce controlled output',async()=>{
 for(const mode of ['missing','empty','missing-ids','invalid-time']){
  const props=fixture();if(mode==='missing')delete props.draft.diff;if(mode==='empty')props.draft.diff={};if(mode==='missing-ids'){props.draft.diff={added_event_ids:[''],removed_event_ids:['missing'],changed_event_ids:['changed']};props.baseChart.events=[];props.draft.chart.events=[];}if(mode==='invalid-time'){props.grid.segments=[];props.draft.chart.events[0].time_ref={kind:'beat',beat_q:0};}
  const root=await mount(props);try{
   const rendered=text(root.toJSON());if(mode==='missing')assert.match(rendered,/Список изменений отсутствует/);if(mode==='empty')assert.match(rendered,/Изменений нет/);if(mode==='missing-ids'){assert.match(rendered,/ID отсутствует/);assert(root.root.findAllByProps({role:'alert'}).length>=4);}if(mode==='invalid-time'){assert.match(rendered,/время недоступно/);assert.equal(root.root.findAllByType('button').filter(button=>text(button)==='Перейти').length,0);}
  }finally{await close(root);}
 }
});
