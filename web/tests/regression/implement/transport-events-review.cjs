const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const root = path.resolve(__dirname, '../../../..');
const ts = require(path.join(root, 'web/node_modules/typescript'));
function load(name, deps) {
  const output = ts.transpileModule(fs.readFileSync(path.join(root, 'web/src', name), 'utf8'), { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS } }).outputText;
  const module = { exports: {} };
  vm.runInNewContext(output, { module, exports: module.exports, require: id => deps[id] }); return module.exports;
}
const { transportEvents } = load('transportEvents.ts', { './timeline': load('timeline.ts', {}) });
const base = { segments: [{ start_time_sec: 0, start_beat_q: 0, bpm_quarter: 120, grid_division: 4 }], meter: { numerator: 4, denominator: 4 } };
const plain = value => JSON.parse(JSON.stringify(value));
let passed = 0, failed = 0;
function check(name, fn) { try { fn(); passed++; console.log(`PASS ${name}`); } catch (e) { failed++; console.log(`FAIL ${name}: ${e.message}`); } }
check('6/8 meter uses eighth-note intervals and shifted bar origin', () => {
  const grid = { ...base, meter: { numerator: 6, denominator: 8, bar_origin_beat_q: 0.5 } };
  const events = transportEvents(grid, {}, 2);
  assert.deepEqual(plain(events.map(e => [e.timeSec, e.kind])), [[0, 'beat'], [0.25, 'downbeat'], [0.5, 'beat'], [0.75, 'beat'], [1, 'beat'], [1.25, 'beat'], [1.5, 'beat'], [1.75, 'downbeat']]);
});
check('tempo change maps metronome times and hits use finalTime including micro-offset', () => {
  const grid = { ...base, segments: [...base.segments, { start_time_sec: 2, start_beat_q: 4, bpm_quarter: 60, grid_division: 4 }] };
  const chart = { events: [
    { time_ref: { kind: 'beat', beat_q: 4, micro_offset_sec: 0.125 }, notes: [{ lane: 0 }, { lane: 2 }] },
    { time_ref: { kind: 'audio', time_sec: 3.25 }, notes: [{ lane: 1 }] },
    { time_ref: { kind: 'audio', time_sec: -0.1 }, notes: [{ lane: 0 }] },
    { time_ref: { kind: 'audio', time_sec: 4 }, notes: [{ lane: 3 }] },
  ] };
  const events = transportEvents(grid, chart, 4);
  assert.deepEqual(plain(events.filter(e => e.kind === 'hit')), [{ timeSec: 2.125, kind: 'hit', lane: 0 }, { timeSec: 2.125, kind: 'hit', lane: 2 }, { timeSec: 3.25, kind: 'hit', lane: 1 }]);
  assert.deepEqual(plain(events.filter(e => e.kind !== 'hit').map(e => e.timeSec)), [0, 0.5, 1, 1.5, 2, 3]);
  assert.equal(events.find(e => e.timeSec === 2).kind, 'downbeat');
});
check('ordinary dense or invalid metronome grids fail before event expansion', () => {
  assert.throws(() => transportEvents(base, {}, 100000), /слишком плотная/);
  for (const denominator of [0, NaN, Infinity]) assert.throws(() => transportEvents({ ...base, meter: { numerator: 4, denominator } }, {}, 2));
});
check('valid finite large bar origin must reject unsafe loop indices without hanging', () => {
  const grid = { ...base, meter: { numerator: 4, denominator: 4, bar_origin_beat_q: 1e20 } };
  const first = Math.ceil((0 - 1e20) - 1e-9), last = Math.floor((4 - 1e20) + 1e-9);
  assert.equal(first, last); assert.equal(first + 1, first);
  try { vm.runInNewContext('transportEvents(grid, {}, 2)', { transportEvents, grid }, { timeout: 30 }); }
  catch (error) {
    if (error.code === 'ERR_SCRIPT_EXECUTION_TIMEOUT') throw new Error('Rendering hangs: first=last=-1e20; i++ does not advance; VM interrupted after 30 ms');
    assert.match(error.message, /Некорректная|плотная/); return;
  }
  assert.fail('Unsafe indices were not rejected');
});
console.log(`RESULT ${passed} passed, ${failed} failed`); process.exitCode = failed ? 1 : 0;
