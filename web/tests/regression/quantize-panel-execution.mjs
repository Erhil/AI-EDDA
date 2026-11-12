import { readFileSync, existsSync } from 'node:fs';
import { createRequire } from 'node:module';
import { test } from 'node:test';
import { strict as assert } from 'node:assert';
const require = createRequire(import.meta.url), React = require('../../../web/node_modules/react');
const { act, create } = require('../../../web/node_modules/react-test-renderer'), ts = require('../../../web/node_modules/typescript');
globalThis.IS_REACT_ACT_ENVIRONMENT = true; globalThis.__quantizeReact = React;
const encode = s => `data:text/javascript;base64,${Buffer.from(s).toString('base64')}`;
const reactUrl = encode('export const {useState,useRef,useEffect}=globalThis.__quantizeReact;'), cache = new Map();
function load(url) { if (cache.has(url.href)) return cache.get(url.href); let output = ts.transpileModule(readFileSync(url, 'utf8'), { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext, jsx: ts.JsxEmit.React } }).outputText; output = output.replace(/from ['"]([^'"]+)['"]/g, (whole, path) => { if (path === 'react') return `from '${reactUrl}'`; if (!path.startsWith('.')) return whole; let child = new URL(path + '.ts', url); if (!existsSync(child)) child = new URL(path + '.tsx', url); return `from '${load(child)}'`; }); const result = encode('const React=globalThis.__quantizeReact;\n' + output); cache.set(url.href, result); return result; }
const Panel = (await import(load(new URL('../../../web/src/QuantizePanel.tsx', import.meta.url)))).default;
test('preview applies captured payload once and retains it after a no-op', async () => {
  const view = { project: { id: 'p', revision: 1 }, grid: { revision: 1, segments: [{ start_time_sec: 0, start_beat_q: 0, bpm_quarter: 120, grid_division: 4 }] }, charts: [{ id: 'c', revision: 1, events: [{ id: 'e', time_ref: { kind: 'beat', beat_q: 2 }, notes: [{ id: 'n', lane: 0 }] }] }] };
  const oldFetch = globalThis.fetch;
  globalThis.fetch = async () => ({ ok: true, json: async () => ({ view, diagnostics: [] }) });
  const text = node => typeof node === 'string' ? node : (node?.children ?? []).map(text).join('');
  // Pagination adds Previous/Next buttons before Apply. Select controls by their visible action.
  const button = label => root.root.findAllByType('button').find(node => text(node) === label);
  const calls = []; let root;
  try {
    await act(async () => { root = create(React.createElement(Panel, { view, chartId: 'c', eventIds: ['e', 'e'], disabled: false, mutate: async (...args) => { calls.push(args); } })); });
    await act(async () => { button('Предпросмотр').props.onClick(); });
    const apply = button('Применить показанные изменения');
    assert(apply && !apply.props.disabled);
    await act(async () => { apply.props.onClick(); apply.props.onClick(); });
    assert.equal(calls.length, 1);
    assert.equal(calls[0][0], 'quantize_events');
    assert.deepEqual(calls[0][1], { chart_id: 'c', event_ids: ['e'], grid_division: 4 });
    assert.deepEqual(calls[0][2], view);
    assert.notEqual(calls[0][2], view);
    assert(button('Применить показанные изменения'));
    assert.equal(button('Применить показанные изменения').props.disabled, false);
  } finally {
    if (root) await act(async () => root.unmount());
    globalThis.fetch = oldFetch;
  }
});
