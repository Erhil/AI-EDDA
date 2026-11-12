import {readFileSync,existsSync} from 'node:fs';
import {createRequire} from 'node:module';
import {strict as assert} from 'node:assert';
import {test} from 'node:test';
const require=createRequire(import.meta.url),React=require('../../../web/node_modules/react');
const {act,create}=require('../../../web/node_modules/react-test-renderer'),ts=require('../../../web/node_modules/typescript');
globalThis.IS_REACT_ACT_ENVIRONMENT=true;globalThis.__clipboardUiReact=React;
const previousWindow=globalThis.window;globalThis.window=new EventTarget();
const encode=s=>`data:text/javascript;base64,${Buffer.from(s).toString('base64')}`,reactUrl=encode('export const {useState,useRef,useEffect,useCallback,useMemo}=globalThis.__clipboardUiReact;'),cache=new Map();
function load(url){if(cache.has(url.href))return cache.get(url.href);let output=ts.transpileModule(readFileSync(url,'utf8'),{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext,jsx:ts.JsxEmit.React}}).outputText;output=output.replace(/import ['"].*\.css['"];?/g,'').replace(/from ['"]([^'"]+)['"]/g,(whole,path)=>{if(path==='react')return `from '${reactUrl}'`;if(!path.startsWith('.'))return whole;let child=new URL(/\.tsx?$/.test(path)?path:path+'.ts',url);if(!existsSync(child))child=new URL(path+'.tsx',url);return `from '${load(child)}'`;});const result=encode('const React=globalThis.__clipboardUiReact;\n'+output);cache.set(url.href,result);return result;}
const Editor=(await import(load(new URL('../../../web/src/TimelineEditor.tsx',import.meta.url)))).default;
const QuantizePanel=(await import(load(new URL('../../../web/src/QuantizePanel.tsx',import.meta.url)))).default;
const text=node=>typeof node==='string'?node:(node?.children??[]).map(text).join('');
const sourceGrid={segments:[{start_time_sec:0,start_beat_q:0,bpm_quarter:120,grid_division:4},{start_time_sec:2,start_beat_q:4,bpm_quarter:60,grid_division:4}]};
const source=()=>({id:'source',events:[{id:'event',time_ref:{kind:'beat',beat_q:3,micro_offset_sec:.75},notes:[{id:'n0',lane:0},{id:'n1',lane:1}],provenance:{source_ids:['ancestor']}},{id:'audio',time_ref:{kind:'audio',time_sec:1},notes:[{id:'n3',lane:3}]}]});
async function setup(){const mutations=[],copies=[];let root,props={entries:[],chart:source(),grid:sourceGrid,duration:10,disabled:false,position:5,mutate:(...args)=>{mutations.push(args);return Promise.resolve();}};props.onCopy=entries=>{copies.push(entries);props={...props,entries};root.update(React.createElement(Editor,props));};await act(async()=>{root=create(React.createElement(Editor,props));});return{mutations,copies,get props(){return props;},get tree(){return root.root;},get text(){return text(root.toJSON());},button(label){return root.root.findAllByType('button').find(b=>text(b)===label);},async click(label){await act(async()=>{this.button(label).props.onClick();});},async update(next){props={...props,...next};await act(async()=>{root.update(React.createElement(Editor,props));});},async select(){const boxes=root.root.findAllByType('input').filter(n=>n.props.type==='checkbox');for(const box of boxes.slice(1))await act(async()=>{box.props.onChange({target:{checked:false}});});await this.click('Выделить диапазон');},async target(value){const label=root.root.findAllByType('label').find(label=>text(label).startsWith('Время вставки'));await act(async()=>{label.findByType('input').props.onChange({target:{value}});});},async mode(value){const label=root.root.findAllByType('label').find(label=>text(label).startsWith('Режим вставки'));await act(async()=>{label.findByType('select').props.onChange({target:{value}});});},async key(key,extra={}){let prevented=false;await act(async()=>{root.root.findByType('canvas').props.onKeyDown({key,ctrlKey:false,metaKey:false,shiftKey:false,...extra,preventDefault:()=>{prevented=true;}});});return prevented;},async close(){await act(async()=>{root.unmount();});}};}

test('Copy keeps only selected notes, does not write, and snapshot survives source edits and chart changes',async()=>{const h=await setup();try{
 await h.select();await h.click('Копировать');assert.equal(h.mutations.length,0);assert.equal(h.copies.length,1);assert.deepEqual(h.props.entries[0].event.notes.map(note=>note.id),['n0']);assert.equal(h.props.entries[0].second,2.25);assert.equal(h.props.entries[0].beat,4.25);
 h.props.chart.events[0].provenance.source_ids.push('later-edit');assert.deepEqual(h.props.entries[0].event.provenance.source_ids,['ancestor']);const saved=h.props.entries;await h.update({chart:{id:'destination',events:[]},grid:{segments:[{start_time_sec:0,start_beat_q:0,bpm_quarter:60,grid_division:4}]}});assert.equal(h.props.entries,saved);await h.click('Вставить');assert.equal(h.mutations.length,1);assert.equal(h.mutations[0][0],'paste_notes');assert.equal(h.mutations[0][1].chart_id,'destination');assert.equal(h.mutations[0][1].target,5);
}finally{await h.close();}});

test('Paste uses explicit finite seconds or the playhead, converts mode, and emits one backend command',async()=>{const h=await setup();try{
 await h.select();await h.click('Копировать');await h.target('3');await h.click('Вставить');assert.equal(h.mutations[0][1].target,5);assert.equal(h.mutations[0][1].mode,'beats');await h.mode('seconds');await h.click('Вставить');assert.equal(h.mutations[1][1].target,3);await h.target('');await h.click('Вставить');assert.equal(h.mutations[2][1].target,5);const count=h.mutations.length;await h.target('Infinity');await h.click('Вставить');assert.equal(h.mutations.length,count);assert(h.text.includes('invalid paste'));assert.deepEqual(h.props.chart.events.map(e=>e.id),['event','audio']);
}finally{await h.close();}});

test('canvas C/V/D shortcuts use the subset and preserve Delete, arrow, Undo and Redo commands',async()=>{const h=await setup();try{
 await h.select();assert(await h.key('C',{ctrlKey:true}));assert.equal(h.mutations.length,0);assert(await h.key('v',{metaKey:true}));assert.equal(h.mutations.length,1);assert.equal(h.mutations[0][0],'paste_notes');assert.deepEqual(h.mutations[0][1].events[0].event.notes.map(n=>n.id),['n0']);await h.key('d',{ctrlKey:true});assert.equal(h.mutations.length,2);assert.equal(h.mutations[1][0],'paste_notes');
 await h.key('Delete');await h.key('ArrowRight');assert(h.text.includes('Split the double event'));await h.key('ArrowDown');await h.key('z',{ctrlKey:true});await h.key('z',{ctrlKey:true,shiftKey:true});await h.key('y',{metaKey:true});assert.deepEqual(h.mutations.slice(2).map(call=>call[0]),['delete_notes','move_notes','undo','redo','redo']);
}finally{await h.close();}});

test('disabled editor blocks button and keyboard writes while Copy remains read-only',async()=>{const h=await setup();try{
 await h.select();await h.click('Копировать');await h.update({disabled:true});assert(h.button('Вставить').props.disabled);assert(h.button('Дублировать ноты').props.disabled);await h.click('Вставить');await h.click('Дублировать ноты');for(const key of ['v','d','z','y'])await h.key(key,{ctrlKey:true});await h.key('Delete');await h.key('ArrowRight');assert.equal(h.mutations.length,0);await h.key('c',{ctrlKey:true});assert.equal(h.copies.length,2);
}finally{await h.close();}});

test('ambiguous acknowledgement does not optimistically change chart data or claim success',async()=>{const h=await setup();try{
 await h.select();await h.click('Копировать');const before=JSON.stringify(h.props.chart);let finish;await h.update({mutate:(...args)=>{h.mutations.push(args);return new Promise(resolve=>{finish=resolve;});}});await h.click('Вставить');assert.equal(JSON.stringify(h.props.chart),before);assert(!/вставлено|успешно/i.test(h.text));await act(async()=>{finish(undefined);});assert.equal(JSON.stringify(h.props.chart),before);assert(!/вставлено|успешно/i.test(h.text));
}finally{await h.close();}});

test('Timeline binds quantize to canonical view, deduplicates selected event IDs, and forwards captured third base',async()=>{
 const h=await setup(),oldFetch=globalThis.fetch;try{
  const canonical={project:{id:'project',revision:7,grid_revision:2,chart_heads:[{id:'source',revision:3}]},grid:{...structuredClone(sourceGrid),revision:2},charts:[{...structuredClone(h.props.chart),revision:3}]},requests=[];
  globalThis.fetch=async(url,options)=>{requests.push({url,options});return{ok:true,status:200,json:async()=>({view:structuredClone(canonical),diagnostics:[]})};};
  await h.update({view:canonical,chart:canonical.charts[0],grid:canonical.grid});await h.click('Выделить диапазон');const bound=h.tree.findByType(QuantizePanel).props;assert.equal(bound.view,canonical);assert.deepEqual(bound.eventIds,['audio','event']);assert.equal(bound.chartId,'source');
  await h.click('Предпросмотр');await h.click('Применить показанные изменения');assert.equal(h.mutations.length,1);assert.equal(h.mutations[0][0],'quantize_events');assert.deepEqual(h.mutations[0][1].event_ids,['audio','event']);assert.deepEqual(h.mutations[0][2],canonical);assert.notEqual(h.mutations[0][2],canonical);
  const draft={...structuredClone(canonical.charts[0]),events:[]},frozenGrid={revision:99,segments:[{start_time_sec:0,start_beat_q:0,bpm_quarter:60,grid_division:4}]};await h.update({chart:draft,grid:frozenGrid,disabled:true});const auditionBound=h.tree.findByType(QuantizePanel).props;assert.equal(auditionBound.view,canonical);assert(auditionBound.disabled);assert(h.button('Предпросмотр').props.disabled);assert.equal(h.mutations.length,1);
 }finally{await h.close();globalThis.fetch=oldFetch;}
});

test('six native disclosures start closed below the canvas and retain all advanced controls',async()=>{
 const h=await setup();try{
  const editor=h.tree.findByProps({className:'timeline-editor'}),children=editor.children.filter(child=>typeof child!=='string'),canvas=children.findIndex(child=>child.type==='canvas'),allDetails=editor.findAllByType('details'),details=allDetails.filter(detail=>text(detail.children[0])!=='Обозначения timeline');assert(canvas>=0);assert.equal(allDetails.length,7);assert.equal(details.length,6);
  const summaries=details.map(detail=>{assert.equal(detail.props.open,undefined);assert.equal(detail.children[0].type,'summary');assert.equal(detail.findAllByType('summary').length,1);assert(children.indexOf(detail)>canvas);return text(detail.children[0]);});assert.deepEqual(summaries,['Копирование и вставка','Перемещение и удаление','Дорожки и события','Выделение диапазона','Квантование','Свойства нот']);
  const basic=children[0];for(const label of ['Snap','Zoom in','Zoom out','←','→','Выбрано нот'])assert(text(basic).includes(label),label);assert(!text(basic).includes('Копировать'));
  const owner=button=>{let parent=button.parent;while(parent&&parent.type!=='details')parent=parent.parent;return parent;};for(const [label,index]of [['Копировать',0],['Вставить',0],['Дублировать ноты',0],['Отразить',1],['Удалить',1],['Задать дорожки',2],['Разделить',2],['Объединить',2],['Keep',2],['Снять Keep',2],['Выделить диапазон',3]])assert.equal(owner(h.button(label)),details[index],label);
 }finally{await h.close();}
});
