const assert=require('node:assert/strict');
const {spawnSync}=require('node:child_process');
const path=require('node:path');
const {mount,act,root,grid}=require('./timeline-marks-review.cjs');
const simple={segments:[{start_time_sec:0,start_beat_q:0,bpm_quarter:60,grid_division:4}]};
const text=n=>typeof n==='string'?n:(n?.children??[]).map(text).join('');
const status=h=>text(h.tree.root.findByProps({role:'status'}));
const double=(ref={kind:'audio',time_sec:1})=>({id:'d',time_ref:ref,notes:[{id:'a',lane:0},{id:'b',lane:1}]});
async function mouse(h,name,x,lane){await act(async()=>h.tree.root.findByType('canvas').props[name]({clientX:x,clientY:lane*60+30,shiftKey:false,currentTarget:{getBoundingClientRect:()=>({left:0,top:0,width:800,height:240})}}));}
async function select(h,label,value){const item=h.tree.root.findAllByType('label').find(n=>text(n).startsWith(label));await act(async()=>item.findByType('select').props.onChange({target:{value}}));}
const backend=`import json,sys
from ai_edda.domain import Chart,TimingGrid
from ai_edda.advanced_commands import Split,apply
from ai_edda.editing import move_notes
from ai_edda.timing import final_time_sec
d=json.load(sys.stdin);g=TimingGrid.model_validate(d['grid']);c=Chart.model_validate(d['chart']);p=d['payload']
actual=apply(c,g,'split_event',Split.model_validate(p),'review')
first=apply(c,g,'split_event',Split(chart_id=c.id,event_id=p['event_id'],note_ids=p['note_ids']),'review')
expected=move_notes(first,g,p['note_ids'],p['delta_lane'],'review',d['mode'],d['delta'])
def result(c):
 return {n.id:{'ref':e.time_ref.model_dump(),'lane':n.lane,'seconds':final_time_sec(g,e.time_ref),'event':e.id} for e in c.events for n in e.notes}
json.dump({'actual':result(actual),'expected':result(expected),'events':len(actual.events)},sys.stdout)
`;
function compare(chart,grid,payload,mode,delta){const p=spawnSync(path.join(root,'.venv/Scripts/python.exe'),['-c',backend],{cwd:root,input:JSON.stringify({chart,grid,payload,mode,delta}),encoding:'utf8'});assert.equal(p.status,0,p.stderr);return JSON.parse(p.stdout);}
let passed=0,failed=0;async function test(name,fn){try{await fn();passed++;console.log('PASS '+name);}catch(e){failed++;console.error('FAIL '+name+': '+e.stack);}}
(async()=>{
for(const [ref,mode,snap]of [[{kind:'beat',beat_q:1,micro_offset_sec:.75},'beats','4'],[{kind:'audio',time_sec:2.5},'beats','4'],[{kind:'audio',time_sec:2.5},'beats','free'],[{kind:'beat',beat_q:1,micro_offset_sec:.75},'seconds','4']])await test(`atomic split matches split-then-move oracle: ${ref.kind}, ${mode}, ${snap}`,async()=>{
 const chart={id:'c',events:[double(ref)]},before=JSON.stringify(chart),h=await mount({chart,grid,highlight:['a']});try{
  await select(h,'Единица перемещения',mode);await select(h,'Snap',snap);
  await mouse(h,'onMouseDown',200,0);await mouse(h,'onMouseMove',240,2);assert.match(status(h),/будет разделено/);assert.equal(h.mutations.length,0);assert.equal(JSON.stringify(chart),before);
  const circles=h.operations.filter(o=>o.key==='arc');assert(circles.some(o=>o.args[0]===200&&o.args[1]===90));assert(circles.some(o=>o.args[1]===150));
  await mouse(h,'onMouseUp',240,2);assert.equal(h.mutations.length,1);assert.equal(h.mutations[0][0],'split_event');
  const result=compare(chart,grid,h.mutations[0][1],mode,.5);assert.equal(result.events,2);assert.equal(result.actual.b.event,'d');assert.equal(result.actual.b.lane,1);assert.deepEqual(result.actual.b.ref,ref);assert.equal(result.actual.b.seconds,2.5);
  assert.deepEqual(result.actual.a.ref,result.expected.a.ref);assert.equal(result.actual.a.lane,result.expected.a.lane);
 }finally{await h.close();}
});
await test('multi-event partial selection remains blocked',async()=>{
 const h=await mount({chart:{id:'c',events:[double(),{id:'other',time_ref:{kind:'audio',time_sec:3},notes:[{id:'o',lane:2}]}]},grid:simple,highlight:['a','o']});try{
  await mouse(h,'onMouseDown',80,0);await mouse(h,'onMouseMove',160,0);assert.match(status(h),/разделите/);assert(!/будет разделено/.test(status(h)));await mouse(h,'onMouseUp',160,0);assert.equal(h.mutations.length,0);
 }finally{await h.close();}
});
for(const change of ['Escape','source','readonly'])await test(`${change} cancels partial split preview`,async()=>{
 const h=await mount({chart:{id:'c',events:[double()]},grid:simple,highlight:['a']});try{
  await mouse(h,'onMouseDown',80,0);await mouse(h,'onMouseMove',160,0);assert.match(status(h),/будет разделено/);
  if(change==='Escape')await act(async()=>h.tree.root.findByType('canvas').props.onKeyDown({key:'Escape',preventDefault(){}}));
  if(change==='source')await h.update({chart:{id:'c',events:[double({kind:'audio',time_sec:2})]}});
  if(change==='readonly')await h.update({disabled:true});
  await mouse(h,'onMouseUp',160,0);assert.equal(h.mutations.length,0);
 }finally{await h.close();}
});
await test('zero snapped time delta does not split a partial double',async()=>{
 const h=await mount({chart:{id:'c',events:[double()]},grid:simple,highlight:['a']});try{
  await mouse(h,'onMouseDown',80,0);await mouse(h,'onMouseMove',81,0);assert(!/будет разделено/.test(status(h)));await mouse(h,'onMouseUp',81,0);assert.equal(h.mutations.length,0);
 }finally{await h.close();}
});
await test('sub-snap horizontal jitter still commits a previewed partial lane move',async()=>{
 const h=await mount({chart:{id:'c',events:[double()]},grid:simple,highlight:['a']});try{
  await mouse(h,'onMouseDown',80,0);await mouse(h,'onMouseMove',81,2);assert(!/раздел/.test(status(h)));
  assert(h.operations.some(o=>o.key==='arc'&&o.args[0]===80&&o.args[1]===150));
  await mouse(h,'onMouseUp',81,2);assert.equal(h.mutations.length,1);assert.equal(h.mutations[0][0],'move_notes');
  assert.deepEqual(h.mutations[0][1],{chart_id:'c',note_ids:['a'],delta_lane:2,time_delta:0});
 }finally{await h.close();}
});
await test('split commit uses the project view captured when the drag began',async()=>{
 const chart={id:'c',revision:3,events:[double()]},base={project:{id:'p',revision:1,grid_revision:0,chart_heads:[{id:'c',revision:3}]},grid:simple,charts:[chart]};
 const h=await mount({chart,grid:simple,view:base,highlight:['a']});try{
  await mouse(h,'onMouseDown',80,0);await mouse(h,'onMouseMove',160,0);
  await h.update({view:{...base,project:{...base.project,revision:2}}});await mouse(h,'onMouseUp',160,0);assert.equal(h.mutations.length,1);assert.deepEqual(h.mutations[0][2],base);
 }finally{await h.close();}
});
console.log(`RESULT ${passed} passed, ${failed} failed`);process.exitCode=failed?1:0;
})();
