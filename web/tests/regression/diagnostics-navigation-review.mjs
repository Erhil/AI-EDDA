import {readFileSync,existsSync} from 'node:fs';
import {createRequire} from 'node:module';
import {strict as assert} from 'node:assert';
import {test} from 'node:test';
const require=createRequire(import.meta.url),React=require('../../../web/node_modules/react');
const {act,create}=require('../../../web/node_modules/react-test-renderer'),ts=require('../../../web/node_modules/typescript');
globalThis.IS_REACT_ACT_ENVIRONMENT=true;globalThis.__diagReact=React;
globalThis.window={devicePixelRatio:1,addEventListener(){},removeEventListener(){}};
globalThis.ResizeObserver=class{observe(){} disconnect(){}};
const encode=s=>`data:text/javascript;base64,${Buffer.from(s).toString('base64')}`;
const reactUrl=encode('export const {useState,useRef,useEffect,useMemo,useCallback}=globalThis.__diagReact;');
const apiUrl=encode('export class ApiError extends Error{};export const api={list:async()=>[]};');
const projectUrl=encode(`export function useProject(){const [selectedChart,selectChart]=globalThis.__diagReact.useState('A');return {...globalThis.__diagProject,selectedChart,selectChart};}`);
const transportUrl=encode(`const React=globalThis.__diagReact;export default function Transport({ref}){React.useImperativeHandle(ref,()=>({pause(){},seek(s){globalThis.__diagSeeks.push(s);}}));return null;}`);
const blankUrl=encode('export default function Blank(){return null;} export const WaveformView=()=>null;');
const cache=new Map();
function load(url){if(cache.has(url.href))return cache.get(url.href);let output=ts.transpileModule(readFileSync(url,'utf8'),{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext,jsx:ts.JsxEmit.React}}).outputText;
 output=output.replace(/import ['"][^'"]+\.css['"];?/g,'').replace(/from ['"]([^'"]+)['"]/g,(whole,path)=>{
 if(path==='react')return `from '${reactUrl}'`;if(path==='./api')return `from '${apiUrl}'`;if(path==='./useProject')return `from '${projectUrl}'`;if(path==='./AudioTransport')return `from '${transportUrl}'`;
 if(!path.startsWith('.'))return whole;
 const keep=['./TimelineEditor','./QuantizePanel','./DiagnosticsList','./timeline','./timelineMarks','./notesClipboard','./validationClient'];
 if(!keep.includes(path.replace(/\.tsx?$/,'')))return `from '${blankUrl}'`;
 let child=new URL(/\.tsx?$/.test(path)?path:path+'.ts',url);if(!existsSync(child))child=new URL(path+'.tsx',url);return `from '${load(child)}'`;});
 if(url.pathname.endsWith('/App.tsx'))output+='\nexport {Workspace};';
 const result=encode('const React=globalThis.__diagReact;\n'+output);cache.set(url.href,result);return result;
}
const Diagnostics=(await import(load(new URL('../../../web/src/DiagnosticsList.tsx',import.meta.url)))).default;
const Timeline=(await import(load(new URL('../../../web/src/TimelineEditor.tsx',import.meta.url)))).default;
const Panel=(await import(load(new URL('../../../web/src/QuantizePanel.tsx',import.meta.url)))).default;
const {Workspace}=await import(load(new URL('../../../web/src/App.tsx',import.meta.url)));
const text=n=>typeof n==='string'?n:(n?.children??[]).map(text).join('');
const targets=root=>root.root.findAllByType('button').filter(b=>/^(event|note): /.test(text(b)));
const navButtons=root=>root.root.findAllByType('button').filter(b=>text(b)==='Далее');
function view(){return{project:{id:'P',revision:1,grid_revision:1,chart_heads:[],audio:{sha256:'audio',duration_sec:100}},grid:{revision:1,segments:[{start_time_sec:1,start_beat_q:0,bpm_quarter:120,grid_division:4}]},charts:[{id:'A',revision:1,events:[{id:'eA',time_ref:{kind:'audio',time_sec:10},notes:[{id:'nA',lane:0}]}]},{id:'B',revision:1,events:[{id:'eB',time_ref:{kind:'beat',beat_q:40,micro_offset_sec:.125},notes:[{id:'nB',lane:1},{id:'nB2',lane:2}]}]}]};}
function diagnostic(chart_id='B',event_ids=['eB'],note_ids=[]){return{chart_id,event_ids,note_ids,severity:'warning',message:'test'};}
function nodeMock(el){if(el.type!=='canvas')return null;const ctx=new Proxy({},{get:(_,key)=>key==='measureText'?text=>({width:text.length*6}):()=>{if(key==='strokeRect')globalThis.__diagHighlights++;},set:()=>true});return{getContext:()=>ctx,getBoundingClientRect:()=>({left:0,top:0,width:800,height:240})};}
async function mount(Component,props){let root;await act(async()=>{root=create(React.createElement(Component,props),{createNodeMock:nodeMock});});return root;}
async function click(b){await act(async()=>{b.props.onClick();});}

test('all 10003 affected IDs are reachable in bounded pages, across event/note boundary',async()=>{
 const seen=[],v=view(),response={view:v,diagnostics:[diagnostic('B',Array.from({length:5001},(_,i)=>`e${i}`),Array.from({length:5002},(_,i)=>`n${i}`))]};
 const root=await mount(Diagnostics,{response,onNavigate:t=>seen.push(t)});try{
 for(let page=0;page<201;page++){const buttons=targets(root);assert(buttons.length<=50);for(const b of buttons)await click(b);const next=navButtons(root)[0];if(page<200){assert(!next.props.disabled);await click(next);}else assert(next.props.disabled);}
 assert.equal(seen.length,10003);assert.equal(new Set(seen.map(t=>t.kind+':'+t.id)).size,10003);assert.equal(seen[5000].id,'e5000');assert.equal(seen[5001].kind,'note');assert.equal(seen.at(-1).id,'n5001');assert(seen.every(t=>t.view===v&&t.chartId==='B'));
 }finally{await act(async()=>root.unmount());}
});
test('all 103 diagnostic rows are reachable with at most 20 rows mounted',async()=>{
 const response={view:view(),diagnostics:Array.from({length:103},(_,i)=>diagnostic('B',[`e${i}`]))};const seen=[];
 const root=await mount(Diagnostics,{response,onNavigate:t=>seen.push(t.id)});try{for(let page=0;page<6;page++){assert(targets(root).length<=20);for(const b of targets(root))await click(b);if(page<5)await click(navButtons(root)[0]);}assert.equal(new Set(seen).size,103);}finally{await act(async()=>root.unmount());}
});
async function workspace(){globalThis.__diagSeeks=[];globalThis.__diagHighlights=0;const requests=[],mutations=[];globalThis.__diagProject={view:view(),canWrite:false,status:'readonly',canRetry:false,mutate:(...args)=>{mutations.push(args);return Promise.resolve();},writerHeaders:{}};
 globalThis.fetch=(url,options)=>new Promise((resolve,reject)=>requests.push({url,options,resolve,reject}));
 const root=await mount(Workspace,{onSessionLost(){}});return{root,requests,mutations,timeline:()=>root.root.findByType(Timeline),async update(v){globalThis.__diagProject={...globalThis.__diagProject,view:v};await act(async()=>root.update(React.createElement(Workspace,{onSessionLost(){}})));},async close(){await act(async()=>root.unmount());}};
}
test('cross-chart diagnostic navigation seeks source time, highlights current IDs, and aborts old GET without mutation',async()=>{
 const h=await workspace();try{const source=structuredClone(globalThis.__diagProject.view);source.charts[1].events[0].time_ref={kind:'audio',time_sec:25.25};
 await act(async()=>h.requests[0].resolve({ok:true,json:async()=>({view:source,diagnostics:[diagnostic()]})}));
 await click(targets(h.root)[0]);assert.equal(h.timeline().props.chart.id,'B');assert.equal(h.timeline().props.position,25.25);assert.equal(h.timeline().props.viewport.start,20.25);assert.deepEqual(h.timeline().props.highlight,['nB','nB2']);assert.deepEqual(globalThis.__diagSeeks,[25.25]);assert(globalThis.__diagHighlights>=2);assert(h.requests[0].options.signal.aborted);assert.equal(h.mutations.length,0);
 }finally{await h.close();}
});
test('missing event, missing note, deleted current IDs do not navigate; project/revision clear highlight',async()=>{
 const h=await workspace();try{const source=structuredClone(globalThis.__diagProject.view),navigate=h.timeline().props.onNavigate;
 for(const [kind,id] of [['event','missing'],['note','missing']])await act(async()=>navigate({chartId:'B',kind,id,view:source}));
 const deleted=structuredClone(source);deleted.charts[1].events=[];await h.update(deleted);await act(async()=>h.timeline().props.onNavigate({chartId:'B',kind:'event',id:'eB',view:source}));assert.equal(h.timeline().props.chart.id,'A');assert.equal(globalThis.__diagSeeks.length,0);
 await h.update(source);await act(async()=>h.timeline().props.onNavigate({chartId:'B',kind:'note',id:'nB',view:source}));assert.deepEqual(h.timeline().props.highlight,['nB']);
 const changed=structuredClone(source);changed.project.revision++;await h.update(changed);assert.equal(h.timeline().props.highlight,undefined);
 await act(async()=>h.timeline().props.onNavigate({chartId:'B',kind:'note',id:'nB',view:changed}));const other=structuredClone(changed);other.project.id='Q';await h.update(other);assert.equal(h.timeline().props.highlight,undefined);assert.equal(h.mutations.length,0);
 }finally{await h.close();}
});
test('diagnostic click must select the highlighted notes in TimelineEditor',async()=>{
 const h=await workspace();try{await act(async()=>h.timeline().props.onNavigate({chartId:'B',kind:'note',id:'nB',view:globalThis.__diagProject.view}));assert(text(h.root.toJSON()).includes('Выбрано нот: 1, событий: 1'));}finally{await h.close();}
});
test('navigation must reject a response from another project with reused object IDs',async()=>{
 const h=await workspace();try{const foreign=view();foreign.project.id='OTHER';await act(async()=>h.timeline().props.onNavigate({chartId:'B',kind:'event',id:'eB',view:foreign}));assert.equal(h.timeline().props.chart.id,'A');assert.equal(globalThis.__diagSeeks.length,0);}finally{await h.close();}
});
test('highlight selection survives mount ordering, respects later user selection, and repeats only on a new navigation',async()=>{
 const h=await workspace();try{
 const source=globalThis.__diagProject.view;
 await act(async()=>h.timeline().props.onNavigate({chartId:'B',kind:'event',id:'eB',view:source}));
 assert(text(h.root.toJSON()).includes('Выбрано нот: 2, событий: 1'));
 await act(async()=>h.root.root.findByType('canvas').props.onKeyDown({key:'Escape'}));
 assert(text(h.root.toJSON()).includes('Выбрано нот: 0, событий: 0'));
 await h.update(structuredClone(source));
 assert(text(h.root.toJSON()).includes('Выбрано нот: 0, событий: 0'));
 await act(async()=>h.timeline().props.onNavigate({chartId:'B',kind:'note',id:'nB',view:source}));
 assert(text(h.root.toJSON()).includes('Выбрано нот: 1, событий: 1'));
 const deleted=structuredClone(source);deleted.charts[1].events[0].notes=deleted.charts[1].events[0].notes.filter(n=>n.id!=='nB');await h.update(deleted);
 assert(text(h.root.toJSON()).includes('Выбрано нот: 0, событий: 0'));
 assert.equal(h.mutations.length,0);
 }finally{await h.close();}
});
test('before/after rows page all 103 events using captured source and preview times',async()=>{
 const v=view();v.charts[0].events=Array.from({length:103},(_,i)=>({id:`e${String(i).padStart(3,'0')}`,time_ref:{kind:'audio',time_sec:i+.125},notes:[{id:`n${i}`,lane:0}]}));let pending;const mutations=[];
 globalThis.fetch=(url,options)=>url.endsWith('/validation')?Promise.resolve({ok:true,json:async()=>({view:v,diagnostics:[]})}):new Promise(resolve=>{pending={resolve,options};});
 const root=await mount(Panel,{view:v,chartId:'A',eventIds:v.charts[0].events.map(e=>e.id),disabled:false,mutate:(...args)=>{mutations.push(args);return Promise.resolve();}});try{
 await click(root.root.findAllByType('button')[0]);const preview=structuredClone(v);preview.charts[0].events.forEach(e=>{e.time_ref.time_sec-=.125;});v.charts[0].events[0].time_ref.time_sec=99;
 await act(async()=>pending.resolve({ok:true,json:async()=>({view:preview,diagnostics:[]})}));const seen=[];
 for(let page=0;page<3;page++){const rows=root.root.findAllByType('p').map(text).filter(s=>/^e\d+:/.test(s));assert(rows.length<=50);seen.push(...rows);if(page<2)await click(navButtons(root)[0]);}
 assert.equal(seen.length,103);assert.deepEqual(seen[0].match(/\d+\.\d+/g),['0.125','0.000']);assert.deepEqual(seen.at(-1).match(/\d+\.\d+/g),['102.125','102.000']);assert.equal(mutations.length,0);
 }finally{await act(async()=>root.unmount());}
});
test('cross-chart navigation aborts a pending preview and never reveals late Apply',async()=>{
 const h=await workspace();try{globalThis.__diagProject.canWrite=true;await h.update(globalThis.__diagProject.view);
 const source=structuredClone(globalThis.__diagProject.view);await act(async()=>h.requests[0].resolve({ok:true,json:async()=>({view:source,diagnostics:[diagnostic()]})}));
 await click(h.root.root.findAllByType('button').find(b=>text(b)==='Выделить диапазон'));
 assert.equal(h.root.root.findByType(Panel).props.eventIds[0],'eA');
 await click(h.root.root.findByType(Panel).findAllByType('button').find(b=>b.props.onClick?.name==='preview'));
 const pending=h.requests.at(-1);assert(pending.url.endsWith('preview-validation'));
 await click(targets(h.root)[0]);assert(pending.options.signal.aborted);assert.equal(h.timeline().props.chart.id,'B');
 await act(async()=>pending.resolve({ok:true,json:async()=>({view:source,diagnostics:[diagnostic()]})}));
 assert.equal(h.mutations.length,0);assert.equal(h.root.root.findByType(Panel).findAllByType('button').length,1);
 }finally{await h.close();}
});
