// Independent retained-review fixtures. Uses real React hooks and a Canvas2D call recorder.
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const root = path.resolve(__dirname, '../../../..');
const ts = require(path.join(root, 'web/node_modules/typescript'));
const React = require(path.join(root, 'web/node_modules/react'));
const {act, create} = require(path.join(root, 'web/node_modules/react-test-renderer'));
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
globalThis.window = {devicePixelRatio: 2};
const resizeObservers=[];
globalThis.ResizeObserver = class {constructor(callback) {this.callback=callback;resizeObservers.push(this);} observe() {} disconnect() {}};
function compile(name, dependencies = {}) {
  const output = ts.transpileModule(fs.readFileSync(path.join(root, 'web/src', name), 'utf8'), {
    compilerOptions: {module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2022}
  }).outputText;
  const mod = {exports: {}};
  new Function('require', 'exports', 'module', output)(name => {
    assert(name in dependencies, `Missing dependency ${name}`);
    return dependencies[name];
  }, mod.exports, mod);
  return mod.exports;
}
const timeline = compile('timeline.ts');
const marks = compile('timelineMarks.ts', {'./timeline': timeline});
const Editor = compile('TimelineEditor.tsx', {
  react: React, 'react/jsx-runtime': require(path.join(root, 'web/node_modules/react/jsx-runtime')),
  './timeline': timeline, './timelineMarks': marks, './timeline.css': {},
  './NoteInspector': {default: () => null}, './QuantizePanel': {default: () => null},
  './notesClipboard': compile('notesClipboard.ts', {'./timeline.ts': timeline})
}).default;
const grid = {segments: [{start_time_sec: .25, start_beat_q: -2, bpm_quarter: 120, grid_division: 4},
  {start_time_sec: 2.25, start_beat_q: 2, bpm_quarter: 60, grid_division: 4}]};
let passed = 0, failed = 0;
async function test(name, fn) {try {await fn(); passed++; console.log('PASS ' + name);} catch(e) {failed++; console.error('FAIL ' + name + ': ' + e.stack);}}
async function mount(extra) {
  let operations = [], tree;
  const firstObserver=resizeObservers.length;
  const mutations = [];
  // TextMetrics.width is a deterministic fixture. This mock does not model font rasterization.
  const ctx = new Proxy({measureText: text => ({width:text.length*6})}, {get(target, key) {if (key in target) return target[key]; return (...args) => {if(key==='fillRect'&&target.fillStyle==='#161b24') operations=[];operations.push({key, args, color: target.strokeStyle, fill: target.fillStyle});};}, set(target,key,value) {target[key]=value;return true;}});
  const props = {entries: [], onCopy() {}, chart: {id:'c',events:[]}, grid, duration:20, disabled:false,
    mutate: async (...args) => mutations.push(args), ...extra};
  await act(async () => {tree = create(React.createElement(Editor, props), {createNodeMock: element => element.type === 'canvas' ? {getContext: () => ctx} : null});});
  return {get operations() {return operations;}, mutations, tree,
    async resize(width,height=240) {await act(async()=>{for(const observer of resizeObservers.slice(firstObserver)) observer.callback([{contentRect:{width,height}}]);});},
    async update(extra) {Object.assign(props,extra);operations=[];await act(async()=>tree.update(React.createElement(Editor,props)));},
    async close() {await act(async()=>tree.unmount());}};
}
const event = (id, time_ref, noteExtra = {}, extra = {}) => ({id,time_ref,notes:[{id: id+'n',lane:0,...noteExtra}],...extra});
module.exports={mount,event,grid,timeline,marks,React,act,root};
if(require.main===module) (async () => {
await test('all format families, all segments, negative quarter beats, and off-grid', () => {
  for (const [f, family] of [[0,'whole'],[.5,'half'],[.25,'quarter_first'],[.75,'quarter_third'],[1/3,'third_a'],[5/6,'third_a'],[1/6,'third_b'],[2/3,'third_b'],[.125,'?']])
    for (const base of [-2, 0, 2, 3]) assert.equal(marks.runeFamily(grid, timeline.timeAt(grid,base+f)), family);
});
await test('audio and micro offsets classify final seconds across actual tempo boundary', () => {
  const refs = [{kind:'beat',beat_q:1,micro_offset_sec:.75},{kind:'audio',time_sec:2.5}];
  for (const ref of refs) assert.equal(marks.runeFamily(grid,timeline.finalTime(grid,ref)),'quarter_first');
  assert.equal(marks.runeFamily(grid,2.25),'whole');
  assert.equal(marks.runeFamily(grid,2.251),'?');
});
await test('display tolerance covers both sides without changing timing', () => {
  const g={segments:[{start_time_sec:0,start_beat_q:0,bpm_quarter:60,grid_division:4}]};
  for(const q of [-.5e-6,.5e-6,1-.5e-6,1+.5e-6]) assert.equal(marks.runeFamily(g,q),'whole');
  for(const q of [-2e-6,2e-6,1-2e-6,1+2e-6]) assert.equal(marks.runeFamily(g,q),'?');
});
await test('bar helpers use 3/8 and fractional origin independently of snap', () => {
  const g={...grid,meter:{numerator:3,denominator:8,bar_origin_beat_q:.125}};
  for(const q of [-1.375,.125,1.625,3.125]) assert(marks.isBarStart(g,q));
  for(const q of [0,1,2,3]) assert(!marks.isBarStart(g,q));
});
await test('real canvas draws 3/8 bars, seconds, and lane labels', async () => {
  const h=await mount({grid:{...grid,meter:{numerator:3,denominator:8,bar_origin_beat_q:.125}},viewport:{start:1.3,span:3},onViewportChange(){}});
  try {
    const gold=h.operations.filter(o=>o.key==='stroke'&&o.color==='#e5b65b');assert.equal(gold.length,3);
    const xs=h.operations.filter((o,i,all)=>o.key==='moveTo'&&all[i+2]?.key==='stroke'&&all[i+2].color==='#e5b65b').map(o=>o.args[0]);
    const expected=[.125,1.625,3.125].map(q=>(timeline.timeAt(grid,q)-1.3)/3*800);
    assert.deepEqual(xs,expected);
    for(const label of ['1','2','3','4']) assert(h.operations.some(o=>o.key==='fillText'&&o.args[0]===label));
    assert(h.operations.some(o=>o.key==='fillText'&&/s$/.test(o.args[0])));
    assert.equal(h.mutations.length,0);
  } finally {await h.close();}
});
await test('real canvas uses visible final times and noncolor provenance, Keep, and selection', async () => {
  const events=[event('before',{kind:'audio',time_sec:.1}),event('micro',{kind:'beat',beat_q:1,micro_offset_sec:.75},{locks:{lane:true},provenance:{created_by:'generated'}}),
    event('manual',{kind:'audio',time_sec:3},{provenance:{created_by:'manual'}}),event('imported',{kind:'audio',time_sec:3.25},{},{provenance:{created_by:'imported'},locks:{time:true}}),event('unknown',{kind:'audio',time_sec:3.125}),event('after',{kind:'audio',time_sec:15})];
  const h=await mount({chart:{id:'c',events},viewport:{start:2.3,span:1},onViewportChange(){},highlight:['micron']});
  try {
    assert.equal(h.operations.filter(o=>o.key==='arc').length,4);
    const labels=h.operations.filter(o=>o.key==='fillText').map(o=>o.args[0]);
    for(const expected of ['quarter_first G','quarter_third M','whole I','? ?']) assert(labels.includes(expected),expected);
    assert(h.operations.some(o=>o.key==='strokeRect'&&o.args[2]===22));
    assert(h.operations.some(o=>o.key==='fillRect'&&o.args[2]===6));
    assert.equal(h.mutations.length,0);
  } finally {await h.close();}
});
await test('drawing loops terminate at extreme BPM, origin, and viewport', async () => {
  for(const [bpm,origin,start,span] of [[1e300,1e300,0,10],[1e-300,-1e300,0,10],[120,1e300,0,10],[120,0,1e12,.25],[120,0,0,1e12]]) {
    const h=await mount({grid:{segments:[{start_time_sec:0,start_beat_q:0,bpm_quarter:bpm,grid_division:4}],meter:{numerator:3,denominator:8,bar_origin_beat_q:origin}},duration:2e12,viewport:{start,span},onViewportChange(){}});
    try {assert(h.operations.length<12000,h.operations.length);assert.equal(h.mutations.length,0);} finally {await h.close();}
  }
});
await test('visible right-edge note keeps the full rune/provenance label inside canvas', async () => {
  const h=await mount({chart:{id:'c',events:[event('edge',{kind:'audio',time_sec:9.9},{provenance:{created_by:'manual'}})]}});
  try {
    const circle=h.operations.find(o=>o.key==='arc');assert.equal(circle.args[0],792);
    const label=h.operations.find(o=>o.key==='fillText'&&o.args[0]==='? M');assert.equal(label.args[1],782);
    assert(label.args[1]>=0);assert.equal(label.args[3],18);assert(label.args[1]+Math.min(18,label.args[3])<=800);
  } finally {await h.close();}
});
await test('family classification uses bounded binary searches and does not copy segments', () => {
  const raw=Array.from({length:10000},(_,i)=>({start_time_sec:i,start_beat_q:i,bpm_quarter:60,grid_division:4}));
  let copied=0,reads=0;
  const segments=new Proxy(raw,{get(target,key,receiver) {
    if(key===Symbol.iterator) return function* () {for(const value of target) {copied++;yield value;}};
    if(typeof key==='string'&&/^\d+$/.test(key)) reads++;
    return Reflect.get(target,key,receiver);
  }});
  const g={segments};
  for(let i=0;i<1000;i++) assert.equal(marks.runeFamily(g,i+.25),'quarter_first');
  assert.equal(copied,0);assert(reads<32000,reads);
  console.log(`REGRESSION 1000 classifications with 10000 segments: ${copied} copies, ${reads} indexed reads.`);
});
await test('narrow canvases constrain full label width and zero-width canvases skip label drawing', async () => {
  const h=await mount({chart:{id:'c',events:[event('narrow',{kind:'audio',time_sec:2.5},{provenance:{created_by:'generated'}})]}});
  try {
    for(const width of [40,1,.25,0,90,800]) {
      await h.resize(width);
      const background=h.operations.find(o=>o.key==='fillRect'&&o.fill==='#161b24');assert.equal(background.args[2],width);
      const labels=h.operations.filter(o=>o.key==='fillText'&&o.args[0]==='quarter_first G');
      if(width===0) {assert.equal(labels.length,0);continue;}
      assert.equal(labels.length,1);
      const label=labels[0];assert.equal(label.args.length,4);
      assert(label.args[1]>=0);assert(label.args[3]>0);assert(Number.isFinite(label.args[3]));
      assert(label.args[1]+Math.min(label.args[0].length*6,label.args[3])<=width);
      if(width<90) {assert.equal(label.args[1],0);assert.equal(label.args[3],width);}
    }
    assert.equal(h.mutations.length,0);
  } finally {await h.close();}
});
console.log(`RESULT ${passed} passed, ${failed} failed`);process.exitCode=failed?1:0;
})();


