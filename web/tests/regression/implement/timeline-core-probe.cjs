const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const root = path.resolve(__dirname, '../../../..');
globalThis.window ??= {addEventListener() {}, removeEventListener() {}};
const ts = require(path.join(root, 'web/node_modules/typescript'));
function compile(file, dependencies) {
  const output = ts.transpileModule(fs.readFileSync(file, 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2022 }
  }).outputText;
  const module = { exports: {} };
  new Function('require', 'exports', 'module', output)(name => dependencies[name], module.exports, module);
  return module.exports;
}
const helpers = compile(path.join(root, 'web/src/timeline.ts'), {});
const grid = { segments: [
  { start_time_sec: 1, start_beat_q: 0, bpm_quarter: 120, grid_division: 4 },
  { start_time_sec: 3, start_beat_q: 4, bpm_quarter: 60, grid_division: 4 }
] };
const note = (id, lane) => ({ id, lane });
const event = (id, seconds, notes) => ({ id, time_ref: { kind: 'audio', time_sec: seconds }, notes });
function mount(events) {
  let slots = [], cursor = 0, effects = [], calls = [], seeks = [], tree, dirty = false;
  const react = {
    useRef(value) { const i = cursor++; return slots[i] ??= { current: value }; },
    useState(value) { const i = cursor++; if (!(i in slots)) slots[i] = value; return [slots[i], next => { const value = typeof next === 'function' ? next(slots[i]) : next; if (!Object.is(value, slots[i])) { slots[i] = value; dirty = true; } }]; },
    useMemo(fn, deps) { const i = cursor++; if (!slots[i] || deps.some((v, j) => v !== slots[i].deps[j])) slots[i] = { deps, value: fn() }; return slots[i].value; },
    useEffect(fn, deps) { const i = cursor++; if (!slots[i] || deps.some((v, j) => v !== slots[i][j])) effects.push(fn); slots[i] = deps; }
  };
  const jsx = (type, props) => ({ type, props });
  const Editor = compile(path.join(root, 'web/src/TimelineEditor.tsx'), {
    react, './timeline': helpers, './timeline.css': {}, 'react/jsx-runtime': { jsx, jsxs: jsx },
    './notesClipboard': compile(path.join(root, 'web/src/notesClipboard.ts'), { './timeline.ts': helpers }),
    './NoteInspector': { default: function NoteInspector() {} }, './QuantizePanel': { default: function QuantizePanel() {} }
  }).default;
  let props = { entries: [], onCopy() {}, chart: { id: 'chart', events }, grid, duration: 20, disabled: false,
    mutate: async (kind, payload) => { calls.push({ kind, payload }); }, onSeek: seconds => seeks.push(seconds) };
  function render() { let count = 0; do { dirty = false; cursor = 0; effects = []; tree = Editor(props); effects.forEach(fn => fn()); assert.ok(++count < 20, 'render loop'); } while (dirty); return tree; }
  function find(type, node = tree, predicate = () => true) {
    if (!node || typeof node !== 'object') return;
    if (node.type === type && predicate(node)) return node;
    for (const child of [node.props?.children].flat(Infinity).filter(child => child && typeof child === 'object')) { const found = find(type, child, predicate); if (found) return found; }
  }
  function mouse(name, x, lane, shiftKey = false) {
    find('canvas').props[name]({ clientX: x, clientY: lane * 60 + 30, shiftKey,
      currentTarget: { getBoundingClientRect: () => ({ left: 0, top: 0, width: 800, height: 240 }) } });
    render();
  }
  function key(key, modifiers = {}) { find('canvas').props.onKeyDown({ key, preventDefault() {}, ...modifiers }); render(); }
  function select(label, value) {
    const node = find('label', tree, n => [n.props.children].flat().includes(label));
    assert.ok(node, `Missing label: ${label}`);
    find('select', node).props.onChange({ target: { value: String(value) } }); render();
  }
  function button(label) { return find('button', tree, n => n.props.children === label); }
  function click(label) { const node = button(label); assert.ok(node, `Missing button: ${label}`); if (!node.props.disabled) node.props.onClick(); render(); }
  render(); render();
  return { calls, seeks, mouse, key, updateProps(next) { props = { ...props, ...next }; render(); }, update(events) { props = { ...props, chart: { ...props.chart, events } }; render(); },
    range(a, b) { for (const [label, value] of [['Начало, с ', a], ['Конец, с (не включён) ', b]]) { const control = find('label', tree, n => [n.props.children].flat().includes(label)); assert.ok(control, `Missing label: ${label}`); find('input', control).props.onChange({ target: { value } }); } render(); },
    filterLane(lane, enabled) { const inputs = []; function walk(n) { if (!n || typeof n !== 'object') return; if (n.type === 'input' && n.props.type === 'checkbox') inputs.push(n); [n.props?.children].flat(Infinity).forEach(walk); } walk(tree); inputs[lane].props.onChange({ target: { checked: enabled } }); render(); },
    snap(value) { select('Snap ', value); }, select, click, button, status: () => find('div', tree, n => n.props.role === 'status').props.children,
    disabled(value) { props = { ...props, disabled: value }; render(); }, tree: () => tree };
}
{
  const h = mount([event('event', 3, [note('a', 0)])]);
  h.key('z', { ctrlKey: true });
  h.key('z', { ctrlKey: true, shiftKey: true });
  assert.deepEqual(h.calls, [{ kind: 'undo', payload: {} }, { kind: 'redo', payload: {} }]);
  console.log('PASS undo and redo send empty history payloads.');
}
{
  const h = mount([event('double', 3, [note('a', 0), note('b', 1)])]);
  h.mouse('onMouseDown', 240, 0); h.mouse('onMouseUp', 240, 0); h.key('ArrowDown');
  assert.deepEqual(h.calls[0].payload, { chart_id: 'chart', note_ids: ['a'], delta_lane: 1, time_delta: 0 });
  h.key('ArrowRight');
  assert.equal(h.calls.length, 1);
  assert.match(h.status(), /Split the double/);
  console.log('PASS partial-double lane move omits time_mode; time movement is refused with a message.');
}
{
  const h = mount([event('event', 3, [note('a', 0)])]);
  h.mouse('onMouseDown', 240, 0); h.mouse('onMouseUp', 240, 0); h.key('ArrowLeft');
  assert.equal(h.calls[0].payload.time_delta, -0.25);
  assert.equal(h.calls[0].payload.time_mode, 'beats');
  h.key('ArrowRight');
  assert.equal(h.calls[1].payload.time_delta, 0.25);
  console.log('PASS signed beat nudges cross tempo boundary by the requested division.');
}
{
  const a = event('a-event', 2, [note('a', 0)]), b = event('b-event', 4, [note('b', 1)]);
  const h = mount([a, b]);
  h.mouse('onMouseDown', 160, 0); h.mouse('onMouseUp', 160, 0); h.key('Delete');
  h.update([b]);
  h.mouse('onMouseDown', 320, 1, true); h.mouse('onMouseUp', 320, 1, true); h.key('ArrowDown');
  assert.deepEqual(h.calls[1].payload.note_ids, ['b']);
  h.key('Delete');
  assert.deepEqual(h.calls[2].payload.note_ids, ['b']);
  console.log('PASS deleted IDs are removed before additive selection, move, and delete.');
}
{
  const h = mount([event('event', 3, [note('a', 0)])]);
  h.mouse('onMouseDown', 240, 0); h.mouse('onMouseMove', 280, 1);
  assert.equal(h.calls.length, 0);
  h.key('Escape'); h.mouse('onMouseUp', 280, 1);
  assert.equal(h.calls.length, 0);
  h.mouse('onMouseDown', 240, 0); h.mouse('onMouseMove', 280, 1); h.mouse('onMouseUp', 280, 1);
  assert.equal(h.calls.length, 1); assert.equal(h.calls[0].kind, 'move_notes');
  console.log('PASS drag is local until mouseup, emits one command, and Escape cancels.');
}
{
  const h = mount([event('event', 3, [note('a', 0)])]);
  h.mouse('onMouseDown', 240, 0); h.mouse('onMouseMove', 240, 1); h.mouse('onMouseUp', 240, 1);
  assert.deepEqual(h.calls[0].payload, { chart_id: 'chart', note_ids: ['a'], delta_lane: 1, time_delta: 0 });
  h.mouse('onMouseDown', 240, 0); h.mouse('onMouseMove', 240, -1); h.mouse('onMouseUp', 240, -1);
  assert.equal(h.calls.length, 1);
  console.log('PASS vertical drag omits time_mode and out-of-range lane move emits no command.');
}
{
  const h = mount([event('event', 3, [note('a', 0)])]);
  h.mouse('onMouseDown', 240, 0); h.mouse('onMouseUp', 240, 0);
  h.update([event('unrelated', 1, [note('b', 3)]), event('event', 3, [note('a', 1)])]);
  h.key('ArrowDown');
  assert.deepEqual(h.calls[0].payload.note_ids, ['a']);
  assert.equal(h.calls[0].payload.delta_lane, 1);
  h.snap('free'); h.select('Единица перемещения ', 'seconds'); h.key('ArrowLeft'); h.key('ArrowRight');
  assert.equal(h.calls[1].payload.time_mode, 'seconds');
  assert.equal(h.calls[1].payload.time_delta, -0.01);
  assert.equal(h.calls[2].payload.time_delta, 0.01);
  console.log('PASS selection follows stable IDs through replacement; free mode uses signed seconds.');
}
{
  const h = mount([]);
  h.mouse('onDoubleClick', 256, 2);
  assert.deepEqual(h.calls[0], { kind: 'create_event', payload: { chart_id: 'chart', time_ref: { kind: 'beat', beat_q: 4.25 }, lanes: [2] } });
  h.snap('free'); h.mouse('onDoubleClick', 256, 2);
  assert.deepEqual(h.calls[1].payload.time_ref, { kind: 'audio', time_sec: 3.2 });
  h.disabled(true); h.mouse('onDoubleClick', 256, 2); h.key('z', { ctrlKey: true });
  assert.equal(h.calls.length, 2);
  console.log('PASS double-click create uses explicit beat/audio references; disabled editor blocks mutations.');
}

module.exports = { mount, event, note, grid, helpers };
