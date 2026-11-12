import {test} from 'node:test';
import {strict as assert} from 'node:assert';
import {spawnSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {copyNotes,buildPastePayload} from '../../../web/src/notesClipboard.ts';
const workspace=fileURLToPath(new URL('../../../',import.meta.url));
const python=fileURLToPath(new URL('../../../.venv/Scripts/python.exe',import.meta.url));
const freeze=value=>{if(value&&typeof value==='object'){Object.values(value).forEach(freeze);Object.freeze(value);}return value;};
const sourceGrid={segments:[{start_time_sec:0,start_beat_q:0,bpm_quarter:120,grid_division:4},{start_time_sec:2,start_beat_q:4,bpm_quarter:60,grid_division:4}]};
const destinationGrid={segments:[{start_time_sec:2,start_beat_q:0,bpm_quarter:90,grid_division:4},{start_time_sec:4,start_beat_q:3,bpm_quarter:180,grid_division:4}]};
const source=()=>({id:'source',events:[{id:'beat-event',time_ref:{kind:'beat',beat_q:3,micro_offset_sec:.75},provenance:{created_by:'generated',generation_run_id:'run',source_ids:['ancestor']},notes:[{id:'selected-note',lane:0,locks:{lane:true}},{id:'unselected-note',lane:1}]},{id:'audio-event',time_ref:{kind:'audio',time_sec:1},notes:[{id:'audio-note',lane:3}]}]});
const backend=`import json,sys
from ai_edda.advanced_commands import Paste,apply
from ai_edda.domain import Chart,TimingGrid
from ai_edda.timing import final_time_sec
data=json.load(sys.stdin)
grid=TimingGrid.model_validate(data['grid'])
chart=Chart(id='destination')
payload=Paste.model_validate(data['payload'])
first=apply(chart,grid,'paste_notes',payload,'operation-one')
second=apply(first,grid,'paste_notes',payload,'operation-two')
json.dump({'events':first.model_dump(mode='json')['events'],'seconds':[final_time_sec(grid,e.time_ref) for e in first.events],'again':second.model_dump(mode='json')['events'][len(first.events):]},sys.stdout)
`;

test('selection keeps event grouping and snapshots nested data without changing frozen input',()=>{
 const chart=freeze(source()),before=JSON.stringify(chart),entries=copyNotes(chart,freeze(sourceGrid),['selected-note','selected-note','audio-note']);
 assert.equal(entries.length,2);assert.deepEqual(entries.map(entry=>[entry.beat,entry.second]),[[4.25,2.25],[2,1]]);assert.deepEqual(entries[0].event.notes.map(note=>note.id),['selected-note']);assert.equal(entries[0].event.id,'beat-event');assert.notEqual(entries[0].event.provenance,chart.events[0].provenance);entries[0].event.provenance.source_ids.push('copy-edit');assert.deepEqual(chart.events[0].provenance.source_ids,['ancestor']);assert.equal(JSON.stringify(chart),before);
 const double=copyNotes(chart,sourceGrid,['selected-note','unselected-note']);assert.equal(double.length,1);assert.equal(double[0].event.notes.length,2);
});

for(const mode of ['beats','seconds'])test(`actual backend accepts cross-grid ${mode} payload and allocates fresh event/note IDs`,()=>{
 const entries=freeze(copyNotes(freeze(source()),sourceGrid,['selected-note','audio-note'])),before=JSON.stringify(entries),payload=buildPastePayload({id:'destination'},destinationGrid,entries,5,mode);
 assert.equal(payload.target,mode==='beats'?6:5);assert.equal(JSON.stringify(entries),before);assert.deepEqual(payload.events.map(entry=>entry.event.id),['beat-event','audio-event']);
 const run=spawnSync(python,['-c',backend],{cwd:workspace,input:JSON.stringify({grid:destinationGrid,payload}),encoding:'utf8'});assert.equal(run.status,0,run.stderr);const result=JSON.parse(run.stdout);assert.deepEqual(result.seconds,mode==='beats'?[5.75,5]:[6.25,5]);assert.equal(result.events[0].notes.length,1);
 const allIds=events=>events.flatMap(event=>[event.id,...event.notes.map(note=>note.id)]),firstIds=allIds(result.events),againIds=allIds(result.again);assert.equal(new Set([...firstIds,...againIds]).size,8);for(const id of firstIds)assert(!['beat-event','audio-event','selected-note','audio-note'].includes(id));assert(result.events[0].provenance.source_ids.includes('beat-event'));assert(result.events[0].notes[0].provenance.source_ids.includes('selected-note'));assert(result.events[0].notes[0].locks.existence);assert.equal(JSON.stringify(entries),before);
});

test('invalid copy and paste inputs fail without partial source mutation',()=>{
 const chart=freeze(source()),entries=freeze(copyNotes(chart,sourceGrid,['selected-note'])),before=JSON.stringify(chart);
 for(const notes of [[],['missing'],['selected-note','missing']])assert.throws(()=>copyNotes(chart,sourceGrid,notes));
 for(const target of [NaN,Infinity,-Infinity,true,'5'])assert.throws(()=>buildPastePayload({id:'destination'},destinationGrid,entries,target,'seconds'));
 assert.throws(()=>buildPastePayload({id:'destination'},destinationGrid,entries,5,'unsupported'));assert.throws(()=>buildPastePayload({id:''},destinationGrid,entries,5,'beats'));assert.throws(()=>buildPastePayload({id:'destination'},destinationGrid,[],5,'beats'));assert.throws(()=>buildPastePayload({id:'destination'},destinationGrid,[{...entries[0],beat:Infinity}],5,'beats'));assert.equal(JSON.stringify(chart),before);
});
