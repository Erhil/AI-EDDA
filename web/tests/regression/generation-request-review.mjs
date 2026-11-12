import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { strict as assert } from 'node:assert';
import { test } from 'node:test';
const require=createRequire(import.meta.url),ts=require('../../../web/node_modules/typescript');
const moduleUrl=source=>`data:text/javascript;base64,${Buffer.from(ts.transpileModule(source,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext}}).outputText).toString('base64')}`;
const timeline=moduleUrl(readFileSync(new URL('../../../web/src/timeline.ts',import.meta.url),'utf8'));
const source=readFileSync(new URL('../../../web/src/generationRequest.ts',import.meta.url),'utf8').replace("from './timeline'",`from '${timeline}'`);
const {captureGeneration,buildGenerationRequest}=await import(moduleUrl(source));
const id=n=>`00000000-0000-4000-8000-${String(n).padStart(12,'0')}`;
function fixture(){
 const view={project:{id:id(1),revision:4,grid_revision:2,analysis:{id:id(2),revision:3},plan_inputs:{id:id(3),revision:1},audio:{path:`audio/${id(4)}/song.ogg`,sha256:'a'.repeat(64),duration_sec:12,sample_rate:48000,channels:2,frames:576000,subtype:'VORBIS'}},grid:{revision:2,review:'approved',segments:[{start_time_sec:1,start_beat_q:0,bpm_quarter:120,grid_division:4,review:'approved'},{start_time_sec:3,start_beat_q:4,bpm_quarter:60,grid_division:4,review:'approved'}]},charts:[{id:id(5),revision:6,grid_revision:2}]};
 const form={lane:'2',subdivision:'4',startSec:'2',endSec:'6',seed:'123'};
 const caps={backend_id:'single_lane',backend_version:'1',modes:['single_lane'],target_fields:['allowed_subdivisions']};
 const response={project_id:id(1),project_revision:4,grid_revision:2,analysis:{id:id(2),revision:3},plan_inputs:{id:id(3),revision:1},plan:{id:id(6),revision:0,audio_asset_id:id(4),audio_sha256:'a'.repeat(64),analysis:{id:id(2),revision:3},grid_revision:2,sections:[{id:id(7),start_sec:0,end_sec:12}]}};
 return {view,form,caps,response};
}
const capture=f=>captureGeneration(f.view,id(5),f.form);
const build=f=>buildGenerationRequest(capture(f),f.caps,f.response);

test('capture detaches sources and string form; repeated builds preserve IDs and convert both tempo endpoints',()=>{
 const f=fixture(),c=capture(f);f.form.seed='999';f.form.endSec='9';f.view.grid.segments[1].bpm_quarter=240;f.view.project.analysis.revision=9;f.view.charts[0].revision=10;
 assert.notEqual(c.view,f.view);assert.notEqual(c.form,f.form);assert.equal(c.form.endSec,'6');assert.equal(c.view.charts[0].revision,6);
 const a=buildGenerationRequest(c,f.caps,f.response),b=buildGenerationRequest(c,f.caps,f.response);
 assert.deepEqual(a,b);assert.equal(a.id,c.requestId);assert.equal(a.target.id,c.targetId);assert.notEqual(c.requestId,c.targetId);assert.deepEqual(a.range_q,{start_q:2,end_q:7});assert.equal(a.seed,123);assert.equal(a.lane,2);assert.deepEqual(a.target.allowed_subdivisions,[4]);
 assert.equal(a.generator_id,'single_lane');assert.equal(a.generator_version,'1');assert.deepEqual(a.chart,{id:id(5),revision:6});
});

test('invalid prerequisites, source references and capabilities reject preparation',()=>{
 const mutations=[
  f=>f.view.project.id='bad', f=>f.view.project.revision=-1, f=>f.view.project.revision=true, f=>f.view.project.revision=Number.MAX_SAFE_INTEGER+1,
  f=>f.view.project.analysis=null, f=>f.view.project.analysis.revision=-1,f=>f.view.project.analysis.id='bad',f=>f.view.project.plan_inputs.id='bad',
  f=>f.view.charts=[],f=>f.view.charts[0].revision=true,f=>f.view.charts[0].revision=Number.MAX_SAFE_INTEGER+1,
  f=>f.view.project.audio=null,f=>f.view.project.audio.path='../song.ogg',f=>f.view.project.audio.sha256='bad',f=>f.view.project.audio.duration_sec=Infinity,f=>f.view.project.audio.duration_sec=true,f=>f.view.project.audio.duration_sec=0,
  f=>f.view.grid.review='needs_review',f=>f.view.grid.segments[1].review='needs_review',f=>f.view.grid.segments=[],f=>f.view.grid.segments[1].bpm_quarter=0,f=>f.view.grid.segments[1].start_time_sec=Infinity,f=>f.view.grid.segments[1].start_beat_q=true,f=>f.view.project.grid_revision=3,
  f=>f.caps.backend_id='',f=>f.caps.backend_version='',f=>f.caps.modes=[],f=>f.caps.target_fields=[],
 ];
 for(const mutate of mutations){const f=fixture();mutate(f);assert.throws(()=>build(f),mutate.toString());}
});

test('all outer and nested plan identities and revisions reject mismatches',()=>{
 const mutations=[f=>f.response.project_id=id(99),f=>f.response.project_revision++,f=>f.response.grid_revision++,f=>f.response.analysis.id=id(99),f=>f.response.analysis.revision++,f=>f.response.analysis=null,f=>f.response.plan_inputs.id=id(99),f=>f.response.plan_inputs.revision++,f=>f.response.plan_inputs=null,f=>f.response.plan.id='bad',f=>f.response.plan.revision=-1,f=>f.response.plan.revision=true,f=>f.response.plan.audio_asset_id=id(99),f=>f.response.plan.audio_sha256='b'.repeat(64),f=>f.response.plan.analysis.id=id(99),f=>f.response.plan.analysis.revision++,f=>f.response.plan.grid_revision++];
 for(const mutate of mutations){const f=fixture();mutate(f);assert.throws(()=>build(f),mutate.toString());}
 const f=fixture();f.view.project.plan_inputs=null;f.response.plan_inputs=null;assert.doesNotThrow(()=>build(f));
});

test('empty, boolean-like, nonfinite, out-of-bound and unsafe integer settings reject preparation',()=>{
 for(const field of ['lane','subdivision','startSec','endSec','seed'])for(const value of ['', ' ', 'true','false','NaN','Infinity','-Infinity',true,false,null,undefined]){
  const f=fixture();f.form[field]=value;assert.throws(()=>build(f),`${field}=${String(value)}`);
 }
 for(const [field,values]of Object.entries({lane:['-1','4','1.5'],subdivision:['0','3','8'],startSec:['0','-1','7'],endSec:['1','2','13'],seed:['1.5','9007199254740992','9007199254740993']}))for(const value of values){const f=fixture();f.form[field]=value;assert.throws(()=>build(f),`${field}=${value}`);}
});

test('selected chart must use the approved grid revision',()=>{
 const f=fixture();f.view.charts[0].grid_revision=1;
 assert.throws(()=>build(f),/./,'stale chart grid must reject before POST');
});

test('finite seconds and BPM must produce finite ordered quarter-beat endpoints',()=>{
 for(const bpm of [1e308,Number.MIN_VALUE]){
  const f=fixture();f.view.grid.segments=[{start_time_sec:0,start_beat_q:0,bpm_quarter:bpm,grid_division:4,review:'approved'}];
  assert.throws(()=>build(f),/./,`conversion at BPM ${bpm} must reject before POST`);
 }
});

function storedTarget(f){f.view.charts[0].target_profile={id:id(20),revision:3};return{project_id:f.view.project.id,project_revision:f.view.project.revision,chart_id:f.view.charts[0].id,chart_revision:f.view.charts[0].revision,target_ref:{id:id(20),revision:3},target:{id:id(20),version:3,name:'Stored target',intended_rank:8,allowed_subdivisions:[2,4],syncopation_budget:null,ornament_budget:null,movement_profile:null}};}

test('stored target uses the complete detached profile and ad-hoc target remains unchanged when no reference exists',()=>{
 const f=fixture(),stored=storedTarget(f),c=capture(f);const built=buildGenerationRequest(c,f.caps,f.response,stored);assert.deepEqual(built.target,stored.target);assert.notEqual(built.target,stored.target);assert.notEqual(built.target.allowed_subdivisions,stored.target.allowed_subdivisions);stored.target.name='Mutated response';stored.target.allowed_subdivisions.push(1);assert.equal(built.target.name,'Stored target');assert.deepEqual(built.target.allowed_subdivisions,[2,4]);
 const plain=fixture(),adHoc=buildGenerationRequest(capture(plain),plain.caps,plain.response,stored);assert.equal(adHoc.target.name,'Single lane');assert.deepEqual(adHoc.target.allowed_subdivisions,[4]);
});

test('stored target never falls back when response, references, identities or revisions are missing or stale',()=>{
 const mutations=[(f,t)=>t.project_id=id(99),(f,t)=>t.project_revision++,(f,t)=>t.chart_id=id(99),(f,t)=>t.chart_revision++,(f,t)=>t.target_ref=null,(f,t)=>t.target_ref.id=id(99),(f,t)=>t.target_ref.revision++,(f,t)=>t.target=null,(f,t)=>t.target.id=id(99),(f,t)=>t.target.version++,(f,t)=>f.view.charts[0].target_profile.id='bad',(f,t)=>f.view.charts[0].target_profile.revision=true];
 for(const mutate of mutations){const f=fixture(),t=storedTarget(f);mutate(f,t);assert.throws(()=>buildGenerationRequest(capture(f),f.caps,f.response,t),mutate.toString());}
 const f=fixture();storedTarget(f);for(const missing of [undefined,null])assert.throws(()=>buildGenerationRequest(capture(f),f.caps,f.response,missing));
});
