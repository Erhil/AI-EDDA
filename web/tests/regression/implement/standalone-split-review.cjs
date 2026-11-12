const assert=require('node:assert/strict');
const {mount,act,grid}=require('./timeline-marks-review.cjs');
const listeners=new Set();
window.addEventListener=(type,fn)=>{if(type==='keydown')listeners.add(fn);};
window.removeEventListener=(type,fn)=>{if(type==='keydown')listeners.delete(fn);};
const text=n=>typeof n==='string'?n:(n?.children??[]).map(text).join('');
const button=(h,label)=>h.tree.root.findAllByType('button').find(n=>text(n)===label);
async function click(h,label){const b=button(h,label);assert(b,`missing ${label}`);assert(!b.props.disabled,`disabled ${label}`);await act(async()=>b.props.onClick());}
const preview=h=>h.tree.root.findAllByProps({'aria-label':'Предпросмотр разделения'})[0];
function fixture(){const chart={id:'c',revision:3,events:[{id:'double',time_ref:{kind:'beat',beat_q:1,micro_offset_sec:.75},notes:[{id:'a',lane:0},{id:'b',lane:2}]}]},view={project:{id:'p',revision:1,grid_revision:0,chart_heads:[{id:'c',revision:3}]},grid,charts:[chart]};return{chart,grid,view,highlight:['a']};}
let passed=0,failed=0;async function test(name,fn){try{await fn();passed++;console.log('PASS '+name);}catch(e){failed++;console.error('FAIL '+name+': '+e.stack);}}
(async()=>{
await test('Split stages zero writes and describes membership, final time, and lanes',async()=>{
 const f=fixture(),before=JSON.stringify(f),h=await mount(f);try{
  await click(h,'Разделить');assert.equal(h.mutations.length,0);assert.equal(JSON.stringify(f),before);assert(preview(h));
  const rows=preview(h).findAllByType('p').map(text);assert.equal(rows.length,3);
  assert.match(rows[0],/время 2.5 с.*a \(дорожка 1\).*b \(дорожка 3\)/);
  assert.match(rows[1],/после разделения.*время 2.5 с.*b \(дорожка 3\)/);assert(!rows[1].includes('a ('));
  assert.match(rows[2],/Новое событие.*время 2.5 с.*a \(дорожка 1\)/);assert(!rows[2].includes('b ('));
  await click(h,'Применить разделение');assert.equal(h.mutations.length,1);assert.equal(h.mutations[0][0],'split_event');
  assert.deepEqual(h.mutations[0][1],{chart_id:'c',event_id:'double',note_ids:['a']});assert.deepEqual(h.mutations[0][2],f.view);assert.notEqual(h.mutations[0][2],f.view);assert.notEqual(h.mutations[0][2].charts[0],f.chart);
  assert.equal(JSON.stringify(f),before);assert(!preview(h));
 }finally{await h.close();}
});
for(const change of ['Cancel','Escape','selection','source','revision','project','grid','readonly'])await test(`${change} invalidates standalone split without writes`,async()=>{
 const f=fixture(),h=await mount(f);try{
  await click(h,'Разделить');assert(preview(h));
  if(change==='Cancel')await click(h,'Отменить');
  if(change==='Escape')await act(async()=>{for(const fn of listeners)fn({key:'Escape'});});
  if(change==='selection')await h.update({highlight:['b']});
  if(change==='source')await h.update({chart:{...f.chart,events:[{...f.chart.events[0],time_ref:{kind:'audio',time_sec:4}}]}});
  if(change==='revision')await h.update({view:{...f.view,project:{...f.view.project,revision:2}}});
  if(change==='project')await h.update({view:{...f.view,project:{...f.view.project,id:'other'}}});
  if(change==='grid')await h.update({grid:{...grid,revision:20}});
  if(change==='readonly')await h.update({disabled:true});
  assert(!preview(h));assert(!button(h,'Применить разделение'));assert.equal(h.mutations.length,0);
 }finally{await h.close();}
});
await test('playhead frames do not serialize a whole 10000-event source for Split fingerprint',async()=>{
 const f=fixture();f.chart.events.push(...Array.from({length:9999},(_,i)=>({id:'e'+i,time_ref:{kind:'audio',time_sec:i+10},notes:[{id:'n'+i,lane:1}]})));
 const h=await mount({...f,duration:10010});const original=JSON.stringify;let calls=0,bytes=0;
 JSON.stringify=function(value,...args){const result=original.call(this,value,...args);if(Array.isArray(value)&&value[0]===f.view&&value[1]===f.chart){calls++;bytes+=result.length;}return result;};
 try{for(let i=0;i<20;i++)await h.update({position:i/10});console.log(`PERF fingerprint: ${calls} complete source serializations, ${bytes} characters for 20 frames.`);assert.equal(calls,0);}
 finally{JSON.stringify=original;await h.close();}
});
console.log(`RESULT ${passed} passed, ${failed} failed`);process.exitCode=failed?1:0;
})();
