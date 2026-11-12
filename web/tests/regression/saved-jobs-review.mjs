import {readFileSync, existsSync} from 'node:fs';
import {createRequire} from 'node:module';
import {strict as assert} from 'node:assert';
import {test} from 'node:test';

const require = createRequire(import.meta.url);
const React = require('../../../web/node_modules/react');
const {act, create} = require('../../../web/node_modules/react-test-renderer');
const ts = require('../../../web/node_modules/typescript');
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
globalThis.__savedJobsReviewReact = React;
const encode = source => `data:text/javascript;base64,${Buffer.from(source).toString('base64')}`;
const reactUrl = encode('export const {useState,useRef,useEffect}=globalThis.__savedJobsReviewReact;');
const cache = new Map();
function load(url) {
  if (cache.has(url.href)) return cache.get(url.href);
  let output = ts.transpileModule(readFileSync(url, 'utf8'), {compilerOptions: {
    target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext, jsx: ts.JsxEmit.React,
  }}).outputText;
  output = output.replace(/from ['"]([^'"]+)['"]/g, (whole, path) => {
    if (path === 'react') return `from '${reactUrl}'`;
    if (!path.startsWith('.')) return whole;
    let child = new URL(path + '.ts', url);
    if (!existsSync(child)) child = new URL(path + '.tsx', url);
    return `from '${load(child)}'`;
  });
  const result = encode('const React=globalThis.__savedJobsReviewReact;\n' + output);
  cache.set(url.href, result);
  return result;
}
const Panel = (await import(load(new URL('../../../web/src/SavedJobsPanel.tsx', import.meta.url)))).default;
const clients = await import(load(new URL('../../../web/src/jobsClient.ts', import.meta.url)));
const {ApiError} = await import(load(new URL('../../../web/src/api.ts', import.meta.url)));
const text = node => typeof node === 'string' ? node : (node?.children ?? []).map(text).join('');
const deferred = () => {
  let resolve, reject;
  const promise = new Promise((a,b) => { resolve=a; reject=b; });
  return {promise,resolve,reject};
};
const row = (id, extra = {}) => ({id,kind:'project_import',state:'completed',label:id,project_id:null,...extra});
const details = (id, extra = {}) => ({...row(id),context:{},...extra});
const metadata = (project, id) => ({project_id:project,export_id:id,project_revision:7,kind:'game',
  download_url:`/api/projects/${project}/exports/${id}/download`,server_folder:'songs',site_check:{passed:true,errors:[]}});

async function setup(initial = {}) {
  const oldFetch = globalThis.fetch, oldSet = globalThis.setTimeout, oldClear = globalThis.clearTimeout;
  const requests = [], timers = new Map(), opened = [], lost = [];
  let root, props = {currentProjectId:null,writerHeaders:() => null,onOpenProject:id => opened.push(id),onSessionLost:() => lost.push('initial'),...initial};
  globalThis.fetch = (url, options) => { const d = deferred(); requests.push({url,options,...d}); return d.promise; };
  globalThis.setTimeout = (fn, ms, ...args) => {
    if (ms !== 1000) return oldSet(fn,ms,...args);
    const id = {}; timers.set(id,fn); return id;
  };
  globalThis.clearTimeout = id => { if (!timers.delete(id)) oldClear(id); };
  await act(async () => { root = create(React.createElement(Panel,props)); });
  return {
    requests,timers,opened,lost,
    get text() { return text(root.toJSON()); },
    get tree() { return root.root; },
    button(label) { return root.root.findAllByType('button').find(b => text(b) === label); },
    async click(label) { const button = this.button(label); assert(button,`Missing button ${label}`); await act(async () => { button.props.onClick(); }); },
    async select(index,value) { await act(async () => { root.root.findAllByType('select')[index].props.onChange({target:{value}}); }); },
    async update(next) { props={...props,...next}; await act(async () => { root.update(React.createElement(Panel,props)); }); },
    async answer(index, body, status=200) {
      assert(requests[index],`Missing request ${index}`);
      await act(async () => { requests[index].resolve({ok:status<400,status,statusText:`HTTP ${status}`,json:async()=>body}); });
    },
    async page(index,items,next_cursor=null) { await this.answer(index,{items,next_cursor}); },
    async tick() { const [id,fn] = timers.entries().next().value; timers.delete(id); await act(async () => { fn(); }); },
    async close() { await act(async () => { root.unmount(); }); globalThis.fetch=oldFetch;globalThis.setTimeout=oldSet;globalThis.clearTimeout=oldClear; },
  };
}

test('global read-only listing recovers persisted rows and opens projects only on an explicit click',async () => {
  const h = await setup(); try {
    assert.equal(h.requests[0].url,'/api/jobs?limit=20');
    assert.equal(h.requests[0].options.headers,undefined);
    await h.page(0,[row('old-import',{kind:'future_kind',state:'failed',label:'Future worker'})]);
    await h.click('Future worker — Ошибка');
    await h.answer(1,details('old-import',{kind:'future_kind',state:'failed',context:[],error:'Known failure',publication_result:{project_id:'restored'}}));
    assert(h.text.includes('Known failure'));assert.equal(h.opened.length,0);
    assert(!h.text.includes('[object Object]'));
    await h.update({onOpenProject:id=>h.opened.push('new:'+id)});
    assert.equal(h.requests.length,2,'callback change must not restart requests');
    await h.click('Открыть проект');assert.deepEqual(h.opened,['new:restored']);
    assert(h.requests.every(r=>r.options.method==='GET'));
  } finally { await h.close(); }
});

test('paging, filters, refresh and repeated project changes suppress stale list responses',async () => {
  const h = await setup({currentProjectId:'A'});try {
    await h.page(0,[row('first')],'cursor');await h.click('Показать ещё');
    assert(h.requests[1].url.includes('after=cursor'));
    await h.select(0,'current');assert(h.requests[1].options.signal.aborted);
    assert(h.requests[2].url.includes('project_id=A'));
    await h.update({currentProjectId:'B'});await h.update({currentProjectId:'A'});
    assert(h.requests[2].options.signal.aborted);assert(h.requests[3].options.signal.aborted);
    await h.page(1,[row('obsolete-page')]);await h.page(2,[row('obsolete-project')]);
    assert(!h.text.includes('obsolete'));
    await h.page(4,[row('current')]);assert(h.text.includes('current'));
    await h.select(1,'terminal');assert(h.requests[5].url.includes('state=terminal'));
    await h.click('Обновить');assert(h.requests[5].options.signal.aborted);
    await h.page(6,[]);assert(!h.text.includes('current'));
  }finally { await h.close(); }
});

test('one detail poll survives callback changes and stops at terminal state or unmount',async () => {
  const h = await setup();try {
    await h.page(0,[row('active',{state:'running'})]);await h.click('active — Выполняется');
    await h.answer(1,details('active',{state:'running',progress:.5}));assert.equal(h.timers.size,1);
    await h.update({onSessionLost:()=>h.lost.push('new')});assert.equal(h.requests.length,2);
    await h.click('active — Выполняется');assert.equal(h.requests.length,2,'same selection must not restart detail polling');
    await h.tick();assert.equal(h.requests.length,3);
    await h.answer(2,details('active',{state:'completed'}));assert.equal(h.timers.size,0);
  } finally { await h.close(); }
  assert(h.requests.filter(r=>r.url.startsWith('/api/jobs/')).every(r=>r.options.signal.aborted));assert.equal(h.timers.size,0);
});

test('new selection aborts old detail body and stale response cannot overwrite current details',async () => {
  const h=await setup();try {
    await h.page(0,[row('a'),row('b')]);await h.click('a — Завершена');
    const body=deferred();await act(async()=>{h.requests[1].resolve({ok:true,json:()=>body.promise});});
    await h.click('b — Завершена');assert(h.requests[1].options.signal.aborted);
    await h.answer(2,details('b',{error:'CURRENT'}));
    await act(async()=>{body.resolve(details('a',{error:'OBSOLETE'}));});
    assert(h.text.includes('CURRENT'));assert(!h.text.includes('OBSOLETE'));
  } finally {await h.close();}
});

test('interrupted publication with a coordinator error must continue polling until reconciliation resolves it',async () => {
  const h=await setup();try {
    await h.page(0,[row('pending',{state:'interrupted'})]);await h.click('pending — Прервана');
    await h.answer(1,details('pending',{state:'interrupted',result:{worker_output:'PRIVATE'},coordinator_error:'Recovery pending'}));
    assert.equal(h.timers.size,1,'coordinator recovery can finish without a user action');
    assert(!h.text.includes('PRIVATE'));
    await h.tick();await h.answer(2,details('pending',{state:'completed',publication_result:{project_id:'p'}}));
    assert.equal(h.timers.size,0);assert.equal(h.opened.length,0);
  }finally {await h.close();}
});

test('interrupted job without worker result or coordinator error is terminal and must not poll forever',async () => {
  const h=await setup();try {
    await h.page(0,[row('interrupted',{state:'interrupted'})]);await h.click('interrupted — Прервана');
    await h.answer(1,details('interrupted',{state:'interrupted'}));
    assert.equal(h.timers.size,0,'an interrupted job without unresolved publication is terminal');
  }finally {await h.close();}
});

test('stale export metadata body cannot enter another selected job',async () => {
  const h=await setup();try {
    await h.page(0,[row('export',{kind:'project_export'}),row('other')]);await h.click('export — Завершена');
    await h.answer(1,details('export',{kind:'project_export',context:{project_id:'p'},publication_result:{export_id:'e'}}));
    const body=deferred();await act(async()=>{h.requests[2].resolve({ok:true,json:()=>body.promise});});
    await h.click('other — Завершена');assert(h.requests[2].options.signal.aborted);
    await h.answer(3,details('other'));
    await act(async()=>{body.resolve(metadata('p','e'));});
    assert.equal(h.tree.findAllByType('a').length,0,'old export download must not appear under another job');
  } finally {await h.close();}
});

test('completed export uses verified metadata and a plain download anchor',async () => {
  const h=await setup();try {
    await h.page(0,[row('export',{kind:'project_export'})]);await h.click('export — Завершена');
    await h.answer(1,details('export',{kind:'project_export',context:{project_id:'p'},publication_result:{export_id:'e'},result:{private:'DO_NOT_RENDER'}}));
    assert.equal(h.requests[2].url,'/api/projects/p/exports/e');await h.answer(2,metadata('p','e'));
    assert(h.text.includes('Ревизия: 7'));assert(h.text.includes('успешно'));assert(h.text.includes('songs'));
    const anchor=h.tree.findByType('a');assert.equal(anchor.props.href,'/api/projects/p/exports/e/download');assert.equal(anchor.props.download,true);
    assert(!h.text.includes('DO_NOT_RENDER'));assert.equal(h.requests.length,3);
  }finally {await h.close();}
});

test('metadata rejects an unexpected download URL',async () => {
  const h=await setup();try {
    await h.page(0,[row('export',{kind:'project_export'})]);await h.click('export — Завершена');
    await h.answer(1,details('export',{kind:'project_export',context:{project_id:'p'},publication_result:{export_id:'e'}}));
    await h.answer(2,{...metadata('p','e'),download_url:'https://other.example/unsafe'});
    assert.equal(h.tree.findAllByType('a').length,0);assert(h.text.includes('Некорректные данные экспорта'));
  }finally {await h.close();}
});

test('expired writer access at click time must prevent a project job cancel request',async () => {
  let headers={'X-Tab-Id':'tab','X-Writer-Lease':'fresh'};
  const h=await setup({currentProjectId:'p',writerHeaders:()=>headers});try {
    await h.page(0,[row('generation',{kind:'generation',state:'running'})]);await h.click('generation — Выполняется');
    await h.answer(1,details('generation',{kind:'generation',state:'running',context:{project_id:'p'}}));
    assert(h.button('Отменить задачу'));headers=null;await h.click('Отменить задачу');
    assert.equal(h.requests.filter(r=>r.options.method==='POST').length,0,'fresh null writer headers must block cancellation');
  }finally {await h.close();}
});

test('cancel imports without a lease and preserve a false publication cancellation result',async () => {
  const h=await setup();try {
    await h.page(0,[row('import',{state:'publication_pending'})]);await h.click('import — publication_pending');
    await h.answer(1,details('import',{state:'publication_pending'}));await h.click('Отменить задачу');
    assert.equal(h.requests[2].url,'/api/jobs/import/cancel');assert.equal(h.requests[2].options.method,'POST');assert.equal(h.requests[2].options.headers,undefined);
    await h.answer(2,false);assert(h.text.includes('Публикацию уже нельзя отменить.'));
    await h.click('Обновить');assert(h.requests[1].options.signal.aborted);assert.equal(h.timers.size,0);
    assert.equal(h.requests.filter(r=>r.options.method==='POST').length,1,'refresh must not send implicit cancel');
  }finally {await h.close();}
});

test('current project mismatch blocks project cancellation and a fresh callback supplies current headers',async () => {
  const h=await setup({currentProjectId:'other',writerHeaders:()=>({'X-Tab-Id':'old','X-Writer-Lease':'old'})});try {
    await h.page(0,[row('generation',{kind:'generation',state:'running'})]);await h.click('generation — Выполняется');
    await h.answer(1,details('generation',{kind:'generation',state:'running',context:{project_id:'p'}}));assert(!h.button('Отменить задачу'));
    await h.update({currentProjectId:'p'});await h.page(2,[row('generation',{kind:'generation',state:'running'})]);await h.click('generation — Выполняется');
    await h.answer(3,details('generation',{kind:'generation',state:'running',context:{project_id:'p'}}));
    const fresh={'X-Tab-Id':'new','X-Writer-Lease':'new'};await h.update({writerHeaders:()=>fresh});
    await h.click('Отменить задачу');assert.deepEqual(h.requests[4].options.headers,fresh);
  }finally {await h.close();}
});

test('401 uses current callback while stale aborted responses are ignored',async () => {
  const h=await setup();try {
    await h.update({onSessionLost:()=>h.lost.push('current')});await h.answer(0,{detail:'Expired'},401);assert.deepEqual(h.lost,['current']);
    await h.click('Обновить');await h.click('Обновить');assert(h.requests[1].options.signal.aborted);
    await h.answer(1,{detail:'Stale'},401);assert.deepEqual(h.lost,['current']);
    await h.page(2,[]);
  }finally {await h.close();}
});

test('client encodes IDs, preserves errors, and forwards abort into fetch and response bodies',async () => {
  const old=globalThis.fetch;try {
    for(const [run,path,method,headers] of [
      [(signal)=>clients.listJobs(new URLSearchParams({state:'active',limit:'20'}),signal),'/api/jobs?state=active&limit=20','GET',undefined],
      [(signal)=>clients.getJob('a /?%',signal),'/api/jobs/a%20%2F%3F%25','GET',undefined],
      [(signal)=>clients.cancelJob('a /?%',signal),'/api/jobs/a%20%2F%3F%25/cancel','POST',undefined],
      [(signal)=>clients.cancelJob('a /?%',signal,{'X-Tab-Id':'t','X-Writer-Lease':'l'}),'/api/jobs/a%20%2F%3F%25/cancel','POST',{'X-Tab-Id':'t','X-Writer-Lease':'l'}],
    ]) {
      const ctl=new AbortController();globalThis.fetch=async(url,options)=>{assert.equal(url,path);assert.equal(options.method,method);assert.equal(options.credentials,'same-origin');assert.equal(options.signal,ctl.signal);assert.deepEqual(options.headers,headers);return {ok:true,json:async()=>false};};assert.equal(await run(ctl.signal),false);
      globalThis.fetch=async()=>({ok:false,status:401,json:async()=>({detail:'Expired'})});await assert.rejects(run(ctl.signal),e=>e instanceof ApiError&&e.status===401&&e.detail==='Expired');
      for(const phase of ['fetch','success-body','error-body']) {
        const c=new AbortController(),aborted=new DOMException('Aborted','AbortError');
        const wait=()=>new Promise((resolve,reject)=>c.signal.addEventListener('abort',()=>reject(aborted),{once:true}));
        globalThis.fetch=async()=>phase==='fetch'?wait():{ok:phase==='success-body',status:503,json:wait};
        const pending=run(c.signal);await new Promise(resolve=>setImmediate(resolve));c.abort();await assert.rejects(pending,e=>e===aborted);
      }
    }
  }finally {globalThis.fetch=old;}
});
