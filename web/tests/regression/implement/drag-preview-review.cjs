const assert=require('node:assert/strict');
const {spawnSync}=require('node:child_process');
const path=require('node:path');
const {performance}=require('node:perf_hooks');
const {mount,event,grid,timeline,act,root}=require('./timeline-marks-review.cjs');
const simple={segments:[{start_time_sec:0,start_beat_q:0,bpm_quarter:60,grid_division:4}]};
const text=n=>typeof n==='string'?n:(n?.children??[]).map(text).join('');
const status=h=>text(h.tree.root.findByProps({role:'status'}));
async function mouse(h,name,x,lane){await act(async()=>h.tree.root.findByType('canvas').props[name]({clientX:x,clientY:lane*60+30,shiftKey:false,currentTarget:{getBoundingClientRect:()=>({left:0,top:0,width:800,height:240})}}));}
async function key(h,key){await act(async()=>h.tree.root.findByType('canvas').props.onKeyDown({key,preventDefault(){}}));}
async function select(h,label,value){const item=h.tree.root.findAllByType('label').find(n=>text(n).startsWith(label));await act(async()=>item.findByType('select').props.onChange({target:{value}}));}
const circles=h=>h.operations.filter(o=>o.key==='arc');
const backend=`import json,sys
from ai_edda.domain import Chart,TimingGrid
from ai_edda.editing import move_notes
from ai_edda.timing import final_time_sec
from ai_edda.validation import validate_chart
d=json.load(sys.stdin);g=TimingGrid.model_validate(d['grid']);c=Chart.model_validate(d['chart']);p=d['payload']
c=move_notes(c,g,p['note_ids'],p['delta_lane'],'review',p.get('time_mode'),p['time_delta'])
json.dump({'notes':{n.id:[final_time_sec(g,e.time_ref),n.lane] for e in c.events for n in e.notes},'codes':[x.code for x in validate_chart(c,g,d['duration'])]},sys.stdout)
`;
function apply(chart,grid,payload,duration=20){const p=spawnSync(path.join(root,'.venv/Scripts/python.exe'),['-c',backend],{cwd:root,input:JSON.stringify({chart,grid,payload,duration}),encoding:'utf8'});assert.equal(p.status,0,p.stderr);return JSON.parse(p.stdout);}
let passed=0,failed=0;async function test(name,fn){try{await fn();passed++;console.log('PASS '+name);}catch(e){failed++;console.error('FAIL '+name+': '+e.stack);}}
(async()=>{
for(const [mode,snap,dx] of [['beats','4',40],['beats','free',31],['seconds','4',40]])await test(`candidate coordinates match actual backend: ${mode}, snap=${snap}`,async()=>{
 const events=[event('beat',{kind:'beat',beat_q:1,micro_offset_sec:.75}),event('audio',{kind:'audio',time_sec:3.1},{lane:1})],chart={id:'c',events};
 const h=await mount({chart,grid,highlight:['beatn','audion']});
 try{
  await select(h,'Единица перемещения',mode);await select(h,'Snap',snap);
  await mouse(h,'onMouseDown',200,0);await mouse(h,'onMouseMove',200+dx,1);
  assert.equal(h.mutations.length,0);const drawn=circles(h).map(o=>[o.args[0]/80,o.args[1]/60-.5]);
  await mouse(h,'onMouseUp',200+dx,1);assert.equal(h.mutations.length,1);
  const result=apply(chart,grid,h.mutations[0][1]);
  for(const [i,id]of ['beatn','audion'].entries()){assert(Math.abs(drawn[i][0]-result.notes[id][0])<1e-10);assert.equal(drawn[i][1],result.notes[id][1]);}
 }finally{await h.close();}
});
await test('duplicate and >2 warnings appear before release and invalid draft still emits one command',async()=>{
 const chart={id:'c',events:[event('a',{kind:'audio',time_sec:1}),event('b',{kind:'audio',time_sec:2}),event('c',{kind:'audio',time_sec:2},{lane:1})]};
 const h=await mount({chart,grid:simple,highlight:['an']});try{
  await mouse(h,'onMouseDown',80,0);await mouse(h,'onMouseMove',160,0);
  assert.match(status(h),/Совпадают/);assert.match(status(h),/Более двух/);assert.equal(h.mutations.length,0);
  assert(circles(h).some(o=>o.fill==='#ff6b6b'));
  await mouse(h,'onMouseUp',160,0);assert.equal(h.mutations.length,1);assert.equal(chart.events.length,3);
  const result=apply(chart,simple,h.mutations[0][1]);assert(result.codes.includes('DUPLICATE_LANE_TIME'));assert(result.codes.includes('TOO_MANY_SIMULTANEOUS_NOTES'));
 }finally{await h.close();}
});
await test('selected candidates collide after beat movement across tempo boundary',async()=>{
 const chart={id:'c',events:[event('a',{kind:'beat',beat_q:1,micro_offset_sec:.25}),event('b',{kind:'audio',time_sec:1.875})]};
 const h=await mount({chart,grid,highlight:['an','bn']});try{
  await mouse(h,'onMouseDown',160,0);await mouse(h,'onMouseMove',200,0);
  assert.match(status(h),/Совпадают/);assert.equal(h.mutations.length,0);
 }finally{await h.close();}
});
await test('partial double previews split; lane and time bounds remain enforced',async()=>{
 const double={id:'d',time_ref:{kind:'audio',time_sec:1},notes:[{id:'a',lane:0},{id:'b',lane:1}]};
 const h=await mount({chart:{id:'c',events:[double]},grid:simple,highlight:['a']});try{
  await mouse(h,'onMouseDown',80,0);await mouse(h,'onMouseMove',160,0);assert.match(status(h),/будет разделено/);assert.equal(h.mutations.length,0);
  await mouse(h,'onMouseUp',160,0);assert.equal(h.mutations.length,1);assert.equal(h.mutations[0][0],'split_event');h.mutations.length=0;
  await mouse(h,'onMouseDown',80,0);await mouse(h,'onMouseMove',80,-1);assert.match(status(h),/Дорожка/);
  await mouse(h,'onMouseUp',80,-1);assert.equal(h.mutations.length,0);
  await h.update({chart:{id:'c',events:[event('a',{kind:'audio',time_sec:1})]},highlight:['an']});
  await mouse(h,'onMouseDown',80,0);await mouse(h,'onMouseMove',-80,0);assert.match(status(h),/Время/);
  await mouse(h,'onMouseUp',-80,0);assert.equal(h.mutations.length,1);
  assert(apply({id:'c',events:[event('a',{kind:'audio',time_sec:1})]},simple,h.mutations[0][1]).codes.includes('OUTSIDE_AUDIO_DURATION'));
 }finally{await h.close();}
});
for(const cancel of ['Escape','source','readonly'])await test(`${cancel} cancels pending candidate without a command`,async()=>{
 const h=await mount({chart:{id:'c',events:[event('a',{kind:'audio',time_sec:1})]},grid:simple,highlight:['an']});try{
  await mouse(h,'onMouseDown',80,0);await mouse(h,'onMouseMove',160,0);
  if(cancel==='Escape')await key(h,'Escape');
  if(cancel==='source')await h.update({chart:{id:'c',events:[event('a',{kind:'audio',time_sec:3})]}});
  if(cancel==='readonly')await h.update({disabled:true});
  await mouse(h,'onMouseUp',160,0);assert.equal(h.mutations.length,0);
 }finally{await h.close();}
});
await test('candidate displays the rune family at its preview position',async()=>{
 const h=await mount({chart:{id:'c',events:[event('a',{kind:'audio',time_sec:1},{provenance:{created_by:'manual'}})]},grid:simple,highlight:['an']});try{
  await mouse(h,'onMouseDown',80,0);await mouse(h,'onMouseMove',100,0);assert.equal(circles(h)[0].args[0],100);
  assert(!h.operations.some(o=>o.key==='fillText'&&o.args[0]==='whole M'));
  assert(h.operations.some(o=>o.key==='fillText'&&o.args[0]==='quarter_first M'));
 }finally{await h.close();}
});
await test('a chain outside a single tolerance window does not trigger >2 warning',async()=>{
 const chart={id:'c',events:[event('a',{kind:'audio',time_sec:1},{lane:1}),event('b',{kind:'audio',time_sec:2-.75e-6}),event('c',{kind:'audio',time_sec:2+.75e-6},{lane:2})]};
 const h=await mount({chart,grid:simple,highlight:['an']});try{
  await mouse(h,'onMouseDown',80,1);await mouse(h,'onMouseMove',160,1);assert(!/Более двух/.test(status(h)));
  await mouse(h,'onMouseUp',160,1);const result=apply(chart,simple,h.mutations[0][1]);assert(!result.codes.includes('TOO_MANY_SIMULTANEOUS_NOTES'));
 }finally{await h.close();}
});
for(const [position,otherTimes]of [['middle',[2-.25e-6,2+.25e-6]],['latest',[2-.75e-6,2-.25e-6]],['epsilon upper',[2-.5e-6,2+1e-6]],['epsilon lower',[2-1e-6,2-.5e-6]]])await test(`>2 candidate at ${position} matches backend direct tolerance window`,async()=>{
 const chart={id:'c',events:[event('a',{kind:'audio',time_sec:1},{lane:2}),...otherTimes.map((time,i)=>event('o'+i,{kind:'audio',time_sec:time},{lane:i}))]};
 const h=await mount({chart,grid:simple,highlight:['an']});try{
  await mouse(h,'onMouseDown',80,2);await mouse(h,'onMouseMove',160,2);const warned=/Более двух/.test(status(h));assert.equal(h.mutations.length,0);
  await mouse(h,'onMouseUp',160,2);assert.equal(h.mutations.length,1);const result=apply(chart,simple,h.mutations[0][1]);
  assert.equal(warned,result.codes.includes('TOO_MANY_SIMULTANEOUS_NOTES'));
 }finally{await h.close();}
});
for(const offset of [0,.75e-6,1e-6,-1e-6,1.25e-6])await test(`duplicate pair at ${offset} seconds matches backend direct tolerance`,async()=>{
 const chart={id:'c',events:[event('a',{kind:'audio',time_sec:1}),event('b',{kind:'audio',time_sec:2+offset})]};
 const h=await mount({chart,grid:simple,highlight:['an']});try{
  await mouse(h,'onMouseDown',80,0);await mouse(h,'onMouseMove',160,0);const warned=/Совпадают/.test(status(h));
  assert(!/Более двух/.test(status(h)));await mouse(h,'onMouseUp',160,0);
  const result=apply(chart,simple,h.mutations[0][1]);assert.equal(warned,result.codes.includes('DUPLICATE_LANE_TIME'));
 }finally{await h.close();}
});
for(const [direction,targetX,expectedX]of [['enter',160,[160,640]],['leave',1120,[]]])await test(`range-selected off-viewport candidates ${direction} using candidate visibility`,async()=>{
 const chart={id:'c',events:[event('visible',{kind:'audio',time_sec:8}),event('outside',{kind:'audio',time_sec:11})]};
 const h=await mount({chart,grid:simple,viewport:{start:5,span:5},onViewportChange(){}});try{
  const selectRange=h.tree.root.findAllByType('button').find(n=>text(n)==='Выделить диапазон');await act(async()=>selectRange.props.onClick());
  assert.equal(circles(h).length,1);
  await mouse(h,'onMouseDown',480,0);await mouse(h,'onMouseMove',targetX,0);assert.equal(h.mutations.length,0);
  assert.deepEqual(circles(h).map(o=>o.args[0]),expectedX);
  if(direction==='leave')assert(!h.operations.some(o=>o.key==='fillText'&&/whole [MGI?]$/.test(o.args[0])));
 }finally{await h.close();}
});
await test('10000 selected notes use bounded conflict queries during drag',async()=>{
 const events=Array.from({length:10000},(_,i)=>event('e'+i,{kind:'audio',time_sec:i+.25}));
 const original=timeline.visibleNotes;let queries=0,returned=0;
 timeline.visibleNotes=(...args)=>{queries++;const result=original(...args);returned+=result.length;return result;};
 let h;const started=performance.now();try{
  h=await mount({chart:{id:'c',events},grid:simple,duration:10001,viewport:{start:0,span:10000},onViewportChange(){},highlight:events.map(e=>e.notes[0].id)});
  await mouse(h,'onMouseDown',.02,0);queries=0;returned=0;await mouse(h,'onMouseMove',1.02,0);
  assert(queries<=20000,queries);assert(returned<=10000,returned);assert.equal(h.mutations.length,0);
  console.log(`PERF 10000 notes: ${queries} conflict queries, ${returned} results, ${(performance.now()-started).toFixed(0)} ms total including mount.`);
 }finally{timeline.visibleNotes=original;if(h)await h.close();}
});
console.log(`RESULT ${passed} passed, ${failed} failed`);process.exitCode=failed?1:0;
})();
