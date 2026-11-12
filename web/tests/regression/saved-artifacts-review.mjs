import {readFileSync,existsSync} from 'node:fs';
import {createRequire} from 'node:module';
import {strict as assert} from 'node:assert';
import {test} from 'node:test';
const require=createRequire(import.meta.url),React=require('../../../web/node_modules/react');
const {act,create}=require('../../../web/node_modules/react-test-renderer'),ts=require('../../../web/node_modules/typescript');
globalThis.IS_REACT_ACT_ENVIRONMENT=true;globalThis.__savedReviewReact=React;
const encode=s=>`data:text/javascript;base64,${Buffer.from(s).toString('base64')}`,reactUrl=encode('export const {useState,useRef,useEffect}=globalThis.__savedReviewReact;'),cache=new Map();
function load(url){if(cache.has(url.href))return cache.get(url.href);let output=ts.transpileModule(readFileSync(url,'utf8'),{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext,jsx:ts.JsxEmit.React}}).outputText;output=output.replace(/from ['"]([^'"]+)['"]/g,(whole,path)=>{if(path==='react')return `from '${reactUrl}'`;if(!path.startsWith('.'))return whole;let child=new URL(path+'.ts',url);if(!existsSync(child))child=new URL(path+'.tsx',url);return `from '${load(child)}'`;});const result=encode('const React=globalThis.__savedReviewReact;\n'+output);cache.set(url.href,result);return result;}
const Panel=(await import(load(new URL('../../../web/src/SavedArtifactsPanel.tsx',import.meta.url)))).default;
const {getCatalog}=await import(load(new URL('../../../web/src/catalogClient.ts',import.meta.url)));
const text=node=>typeof node==='string'?node:(node?.children??[]).map(text).join('');
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return{promise,resolve,reject};};
const analysis=id=>({id,audio_asset_id:'audio',audio_sha256:'hash',revision:0});
async function setup(){const oldFetch=globalThis.fetch,requests=[],mutations=[],drafts=[];let root,props={view:{project:{id:'project /?',revision:1,audio:{path:'audio/audio/song.ogg',sha256:'hash'}},grid:{},charts:[]},disabled:false,mutate:(...args)=>{const d=deferred();mutations.push({args,...d});return d.promise;},onDraft:id=>drafts.push(id)};globalThis.fetch=(url,options)=>{const d=deferred();requests.push({url,options,...d});return d.promise;};await act(async()=>{root=create(React.createElement(Panel,props));});return{requests,mutations,drafts,get props(){return props;},get text(){return text(root.toJSON());},get tree(){return root.root;},button(label){return root.root.findAllByType('button').find(b=>text(b)===label);},async click(label){await act(async()=>{this.button(label).props.onClick();});},async update(next){props={...props,...next};await act(async()=>{root.update(React.createElement(Panel,props));});},async answer(index,items,next_cursor=null,kind='analysis'){await act(async()=>{requests[index].resolve({ok:true,status:200,json:async()=>({kind,items,next_cursor})});});},async close(){await act(async()=>{root.unmount();});globalThis.fetch=oldFetch;}};}

test('read-only lists page damaged rows safely; draft Open routes without any write or automatic head change',async()=>{const h=await setup();try{
 await h.update({disabled:true});assert.equal(h.requests[0].options.credentials,'same-origin');assert(h.requests[0].url.startsWith('/api/projects/project%20%2F%3F/artifacts?'));await h.answer(0,[analysis('analysis-a'),{id:'broken',error:'Artifact unavailable'}],'cursor-1');assert(h.text.includes('Результат недоступен'));assert(h.button('Выбрать анализ').props.disabled);assert.equal(h.mutations.length,0);await h.click('Показать ещё');assert(h.requests[1].url.includes('after=cursor-1'));await h.answer(1,[analysis('analysis-b')]);assert(h.text.includes('analysis-a'));assert(h.text.includes('analysis-b'));await h.click('Черновики');await h.answer(2,[{id:'draft-old',chart_id:'chart',base_chart_revision:3,generator_id:'single_lane',seed:7,event_count:12,rejected:true}],null,'draft');await h.click('Открыть черновик');assert.deepEqual(h.drafts,['draft-old']);assert.equal(h.mutations.length,0);
}finally{await h.close();}});

test('select analysis uses the existing command and canonical input alone determines selected status',async()=>{const h=await setup();try{
 await h.answer(0,[analysis('analysis-a')]);await h.click('Выбрать анализ');assert.deepEqual(h.mutations[0].args,['select_analysis',{analysis_id:'analysis-a'}]);await act(async()=>{h.mutations[0].resolve(undefined);});assert(!h.text.includes('Анализ выбран.'));const view=structuredClone(h.props.view);view.project.analysis={id:'analysis-a',revision:0};await h.update({view});assert(h.text.includes('Анализ выбран.'));
}finally{await h.close();}});

test('old list responses cannot overwrite a later tab after A/B/A',async()=>{const h=await setup();try{
 await h.click('Черновики');await h.click('Анализы');assert(h.requests[0].options.signal.aborted);await h.answer(0,[analysis('obsolete')]);assert(!h.text.includes('obsolete'));await h.answer(2,[analysis('current')]);assert(h.text.includes('current'));
}finally{await h.close();}});

test('canonical revision change replaces pending catalog generation',async()=>{const h=await setup();try{
 const view=structuredClone(h.props.view);view.project.revision++;await h.update({view});assert.equal(h.requests.length,2,'revision change must request a fresh catalog');await h.answer(0,[analysis('obsolete-revision')]);assert(!h.text.includes('obsolete-revision'));await h.answer(1,[analysis('fresh-revision')]);assert(h.text.includes('fresh-revision'));
}finally{await h.close();}});

test('analysis decision pending state does not remain busy after tab change',async()=>{const h=await setup();try{
 await h.answer(0,[analysis('analysis-a')]);await h.click('Выбрать анализ');await h.click('Черновики');await h.answer(1,[],null,'draft');await h.click('Анализы');await h.answer(2,[analysis('analysis-a')]);await act(async()=>{h.mutations[0].resolve(undefined);});assert(!h.button('Выбрать анализ').props.disabled,'completed old-tab decision must not keep analysis controls busy');
}finally{await h.close();}});

test('same-tick More requests cannot duplicate a catalog page',async()=>{const h=await setup();try{
 await h.answer(0,[analysis('first')],'cursor-1');const more=h.button('Показать ещё').props.onClick;await act(async()=>{more();more();});assert.equal(h.requests.length,2,'one cursor must have only one pending page request');await h.answer(1,[analysis('second')]);assert.equal((h.text.match(/second/g)??[]).length,1);
}finally{await h.close();}});

test('catalog client propagates aborts while consuming an HTTP error body',async()=>{const previous=globalThis.fetch;try{
 const c=new AbortController(),aborted=new DOMException('Aborted','AbortError');globalThis.fetch=async()=>({ok:false,status:409,statusText:'Conflict',json:()=>new Promise((resolve,reject)=>c.signal.addEventListener('abort',()=>reject(aborted),{once:true}))});const pending=getCatalog('project','analysis',null,c.signal);await new Promise(resolve=>setImmediate(resolve));c.abort();await assert.rejects(pending,error=>error===aborted);
}finally{globalThis.fetch=previous;}});

test('unmount aborts the active More request rather than only the initial page request',async()=>{
 const h=await setup();let closed=false;try{await h.answer(0,[analysis('first')],'cursor-1');await h.click('Показать ещё');const pending=h.requests[1];await h.close();closed=true;assert(pending.options.signal.aborted,'unmount must abort the active pagination request');await h.answer(1,[analysis('late-page')]);assert.equal(h.requests.length,2);}finally{if(!closed)await h.close();}
});

test('old analysis selection failure cannot enter another project panel state',async()=>{
 const h=await setup();try{
  await h.answer(0,[analysis('old-analysis')]);await h.click('Выбрать анализ');const view=structuredClone(h.props.view);view.project.id='another-project';await h.update({view});await h.answer(1,[analysis('current-analysis')]);await act(async()=>{h.mutations[0].reject(new Error('old-project-selection-error'));});assert(!h.text.includes('old-project-selection-error'),'old command error must remain bound to its source project');assert(h.text.includes('current-analysis'));
 }finally{await h.close();}
});

test('old unmounted selection cannot pollute a fresh project panel instance',async()=>{
 const old=await setup();await old.answer(0,[analysis('old-analysis')]);await old.click('Выбрать анализ');await old.close();const fresh=await setup();try{
  await fresh.answer(0,[analysis('fresh-analysis')]);await act(async()=>{old.mutations[0].reject(new Error('unmounted-selection-error'));});assert(!fresh.text.includes('unmounted-selection-error'));assert(fresh.text.includes('fresh-analysis'));
 }finally{await fresh.close();}
});
