import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
const harnessUrl = new URL('./music-transport-review.mjs', import.meta.url);
const harness = await readFile(harnessUrl, 'utf8');
const prelude = harness.split('let passed = 0;')[0]
  .replace("'../../../../web/node_modules/typescript/lib/typescript.js'", JSON.stringify(new URL('../../../../web/node_modules/typescript/lib/typescript.js', harnessUrl).href))
  .replaceAll('import.meta.url', JSON.stringify(harnessUrl.href));
const helpers = await import(`data:text/javascript;base64,${Buffer.from(`${prelude}\nexport { fixture, flush, near, paired, timers, pump, musicNodes, overlayNodes };`).toString('base64')}`);
const { flush, near, paired, timers, pump, musicNodes, overlayNodes } = helpers;
let fixtures = [], passed = 0, failed = 0;
const fixture = (...args) => { const f = helpers.fixture(...args); fixtures.push(f); return f; };
async function check(name, body) {
  try { await body(); passed++; console.log(`PASS ${name}`); }
  catch (error) { failed++; console.log(`FAIL ${name}\n${error.stack}`); }
  finally { fixtures.forEach(f => f.transport.dispose()); fixtures = []; timers.clear(); }
}
const resolveAll = async f => { f.requests.slice().forEach(r => r.resolve()); await flush(); };

await check('cross-fragment native loop copies exact stereo frames and has bounded allocations', async () => {
  const f = fixture(90, 1000, true);
  f.transport.setLoop(29.9, 30.1); f.transport.seek(29.95); f.transport.setRate(0.5); await f.transport.play();
  assert.deepEqual(f.requests.map(r => r.start), [0, 30000]);
  f.requests[0].resolve(); await flush(); assert.equal(f.context.nodes.length, 0);
  f.context.advance(10); near(f.last().position, 29.95); assert.equal(f.last().status, 'buffering');
  f.requests[1].resolve(); await flush(); paired(f.context);
  assert.equal(f.context.nodes.length, 2); assert.equal(f.context.buffers.length, 4); assert.equal(f.transport.cache.size, 2);
  const source = musicNodes(f.context)[0]; const music = source.buffer;
  assert.equal(music.length, 200); assert.equal(music.duration / source.playbackRate.value, 0.4);
  for (const node of f.context.nodes) { assert.equal(node.loop, true); assert.equal(node.startArgs.length, 2); near(node.startArgs[0], 110); near(node.startArgs[1], 0.05); }
  for (let i = 0; i < 200; i++) { assert.equal(music.getChannelData(0)[i], 29900 + i); assert.equal(music.getChannelData(1)[i], -(29900 + i)); }
  for (let i = 0; i < 200; i++) { f.context.currentTime = 110 + i * 0.4 + 0.2; pump(); near(f.last().position, 30.05); }
  f.context.currentTime = 10110.1; pump(); near(f.last().position, 30);
  assert.equal(f.context.nodes.length, 2); assert.equal(f.context.buffers.length, 4);
  assert.ok(f.context.nodes.every(n => !n.ended)); assert.equal(f.transport.cache.size, 2);
});

await check('one-frame rounded loop and exact full-fragment loop are accepted', async () => {
  const f = fixture(30, 48000, true);
  f.transport.setLoop(100.1 / 48000, 100.9 / 48000); await f.transport.play(); await resolveAll(f);
  assert.equal(f.context.nodes[0].buffer.length, 1); assert.equal(f.context.nodes[0].buffer.getChannelData(0)[0], 100);
  f.context.advance(300); assert.equal(f.context.nodes.length, 2); assert.equal(f.context.buffers.length, 3);
  assert.ok(f.last().position >= 100 / 48000 && f.last().position < 101 / 48000);
  f.transport.pause(); f.transport.setLoop(0, 30); await f.transport.play();
  assert.equal(f.requests.length, 1); assert.equal(musicNodes(f.context).at(-1).buffer.length, 30 * 48000);
});

await check('short-loop click selection excludes pre-A tails and B onsets and clips end tails', async () => {
  const f = fixture(61, 48000); const a = 29.99, b = 30.01;
  const events = [
    { timeSec: a - 0.005, kind: 'downbeat' }, { timeSec: a, kind: 'hit', lane: 1 },
    { timeSec: 29.999, kind: 'hit', lane: 0 }, { timeSec: 30.009, kind: 'hit', lane: 2 },
    { timeSec: b, kind: 'beat' },
  ];
  f.transport.setClicks(events, { metronome: 0.3, hits: 0.7 }); f.transport.setLoop(a, b);
  await f.transport.play(); await resolveAll(f);
  const actual = overlayNodes(f.context)[0].buffer.getChannelData(0);
  assert.equal(actual.length, 960); assert.equal(actual[0], 0);
  for (let i = 0; i < actual.length; i++) {
    let expected = 0;
    for (const event of events.slice(1, 4)) {
      const phase = Math.round(a * 48000) + i - Math.round(event.timeSec * 48000);
      if (phase < 0 || phase >= 960) continue;
      const t = phase / 48000;
      expected = Math.fround(expected + 0.7 * (1 - t / 0.02) * Math.sin(2 * Math.PI * (600 + 150 * event.lane) * t));
    }
    near(actual[i], expected);
  }
  f.transport.seek(30); await flush();
  const afterSeek = overlayNodes(f.context).at(-1);
  near(afterSeek.startArgs[1], 0.01); assert.deepEqual(afterSeek.buffer.getChannelData(0), actual);
});

await check('seek, pause, rate change and clear preserve fractional wrapped position', async () => {
  const f = fixture(90, 1000); f.transport.setLoop(29.9, 30.1); f.transport.seek(30.025);
  await f.transport.play(); await resolveAll(f); near(f.context.nodes[0].startArgs[1], 0.125);
  f.context.advance(0.05, false); const previous = f.context.nodes.slice(); f.transport.setRate(0.5); await flush();
  near(f.last().position, 30.075); assert.ok(previous.every(n => n.stopped && n.disconnected));
  near(musicNodes(f.context).at(-1).startArgs[1], 0.175); assert.equal(musicNodes(f.context).at(-1).playbackRate.value, 0.5);
  f.transport.seek(30.1); await flush(); near(f.last().position, 29.9);
  f.transport.seek(50); await flush(); near(f.last().position, 29.9);
  f.transport.seek(30.025); await flush(); f.transport.pause(); near(f.last().position, 30.025);
  await f.transport.play(); f.context.advance(0.1, false); f.transport.setLoop(null); await flush();
  near(f.last().position, 30.075); assert.equal(f.last().status, 'playing'); assert.equal(f.transport.loop, null);
  assert.equal(musicNodes(f.context).at(-1).loop, undefined); near(musicNodes(f.context).at(-1).startArgs[1], 0.075);
});

await check('invalid loops preserve active playback and long loop selection is accepted', async () => {
  const f = fixture(90, 1000); f.transport.setLoop(1, 2); await f.transport.play(); await resolveAll(f);
  const state = f.last(), nodes = f.context.nodes.length, generation = f.transport.generation;
  for (const [a, b] of [[NaN, 2], [-1, 2], [1, Infinity], [2, 2], [3, 2], [1, 91], [1.0001, 1.0002]]) {
    assert.throws(() => f.transport.setLoop(a, b), RangeError);
    assert.equal(f.last(), state); assert.equal(f.context.nodes.length, nodes); assert.equal(f.transport.generation, generation);
  }
  f.transport.setLoop(0, 31); await flush(); assert.deepEqual(f.transport.loop, [0, 31000]);
});

await check('loop changes invalidate stale fragment completions and context resumes', async () => {
  const f = fixture(90, 1000); f.transport.setLoop(29.9, 30.1); await f.transport.play(); const stale = f.requests.slice();
  f.transport.setLoop(60, 61); await flush(); assert.ok(stale.every(r => r.signal.aborted));
  stale[0].resolve(); stale[1].reject(new Error('stale')); await flush(); assert.equal(f.context.buffers.length, 0);
  f.requests.at(-1).resolve(); await flush(); assert.equal(f.last().status, 'playing'); assert.equal(f.context.nodes.length, 2);
  f.transport.dispose(); assert.ok(f.context.nodes.every(n => n.stopped && n.disconnected));
  for (const action of ['pause', 'dispose', 'change']) {
    const g = fixture(90, 1000); let resolveResume; g.transport.setLoop(29.9, 30.1);
    g.context.resume = () => new Promise(resolve => { resolveResume = resolve; });
    const playing = g.transport.play(); const oldResume = resolveResume;
    if (action === 'change') g.transport.setLoop(60, 61); else g.transport[action]();
    oldResume(); await playing; assert.equal(g.requests.length, 0);
    if (action === 'change') { resolveResume(); await flush(); assert.deepEqual(g.requests.map(r => r.start), [60000]); }
  }
});

console.log(`${passed} short-loop scenarios passed; ${failed} failed`);
assert.equal(failed, 0);
