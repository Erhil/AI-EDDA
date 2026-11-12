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
async function resolve(f, start) {
  const r = f.requests.find(r => r.start === start && !r.done);
  assert.ok(r, `missing request ${start}`); r.done = true; r.resolve(); await flush();
}
async function ready(f) {
  for (let i = 0; i < 10; i++) {
    const pending = f.requests.filter(r => !r.done && !r.signal.aborted);
    if (!pending.length) break;
    for (const r of pending) { r.done = true; r.resolve(); } await flush();
  }
}
function bounded(f) {
  assert.ok(f.transport.cache.size <= 3, `cache ${f.transport.cache.size}`);
  assert.ok(f.transport.sources.length <= 8, `active source count ${f.transport.sources.length}`);
  assert.ok(f.transport.pending.size <= 3);
  // The scheduler fills at less than 30 seconds, then completes the natural fragment.
  // Thus one final fragment can extend coverage past 30, but never past 60 seconds.
  assert.ok((f.transport.scheduledEnd - f.context.currentTime) * f.transport.rate <= 60 + 1e-8);
}

await check('long loop schedules the four planned ranges at rate 0.5', async () => {
  const f = fixture(150, 1000, true); f.transport.setLoop(29.9, 90.1); f.transport.seek(89.9); f.transport.setRate(0.5); await f.transport.play();
  assert.deepEqual(f.requests.map(r => r.start), [60000, 90000, 0]);
  await resolve(f, 60000); await resolve(f, 90000); await resolve(f, 0);
  f.context.advance(0.4); await resolve(f, 30000); paired(f.context); bounded(f);
  const nodes = musicNodes(f.context).slice(0, 4);
  for (let i = 0; i < 4; i++) near(nodes[i].startArgs[0], 100 + i * 0.2);
  const firstFrames = [89900, 90000, 29900, 30000], counts = [100, 100, 100, 30000];
  for (let i = 0; i < 4; i++) {
    const node = nodes[i], [when, offset, duration] = node.startArgs;
    assert.equal(node.playbackRate.value, 0.5); near(duration, counts[i] / 1000);
    const start = Math.round(offset * 1000); const data = node.buffer.getChannelData(0);
    assert.equal(data[start], firstFrames[i]); assert.equal(data[start + counts[i] - 1], firstFrames[i] + counts[i] - 1);
    assert.ok(when >= 100); assert.equal(node.loop, undefined);
  }
});

await check('delayed loop-start data freezes at A and resumes with a fresh anchor', async () => {
  const f = fixture(150, 1000); f.transport.setLoop(29.9, 90.1); f.transport.seek(89.9); f.transport.setRate(0.5); await f.transport.play();
  await resolve(f, 60000); await resolve(f, 90000);
  f.context.advance(0.4); near(f.last().position, 29.9); assert.equal(f.last().status, 'buffering');
  f.context.advance(10); near(f.last().position, 29.9); const count = musicNodes(f.context).length;
  await resolve(f, 0); const resumed = musicNodes(f.context)[count];
  near(resumed.startArgs[0], 110.4); near(resumed.startArgs[1], 29.9); near(f.last().position, 29.9); paired(f.context); bounded(f);
});

await check('delayed successor after loop head freezes at 30 instead of A', async () => {
  const f = fixture(150, 1000); f.transport.setLoop(29.9, 90.1); f.transport.seek(89.9); f.transport.setRate(0.5); await f.transport.play();
  await resolve(f, 60000); await resolve(f, 90000); await resolve(f, 0);
  f.context.advance(0.7); near(f.last().position, 30); assert.equal(f.last().status, 'buffering');
  f.context.advance(7); near(f.last().position, 30); const count = musicNodes(f.context).length;
  await resolve(f, 30000); near(musicNodes(f.context)[count].startArgs[0], 107.7); near(f.last().position, 30); paired(f.context); bounded(f);
});

await check('many traversals keep bounded cache and source horizon without missing or duplicated frames', async () => {
  const f = fixture(180, 1000, true); f.transport.setLoop(29.9, 90.1); f.transport.seek(89.9); await f.transport.play(); await ready(f);
  for (let i = 0; i < 4000; i++) { f.context.advance(0.05); await ready(f); bounded(f); }
  const nodes = musicNodes(f.context); let expected = 89900;
  for (let i = 0; i < nodes.length; i++) {
    const n = nodes[i], [when, offset, duration] = n.startArgs;
    const start = Math.round(offset * 1000), length = Math.round(duration * 1000);
    assert.equal(n.buffer.getChannelData(0)[start], expected, `sequence node ${i}`);
    assert.equal(n.buffer.getChannelData(1)[start], -expected);
    expected += length; if (expected === 90100) expected = 29900;
    if (i) near(when, nodes[i - 1].startArgs[0] + nodes[i - 1].startArgs[2]);
  }
  assert.ok(nodes.length > 12); assert.ok(f.requests.every(r => r.start <= 90000)); paired(f.context);
});

await check('long-loop click onsets and internal tails match an independent kernel', async () => {
  const f = fixture(120, 48000); const a = 29.99, b = 60.01;
  const events = [{ timeSec: a - 0.005, kind: 'beat' }, { timeSec: a, kind: 'hit', lane: 1 }, { timeSec: 29.999, kind: 'hit', lane: 0 }, { timeSec: 60.009, kind: 'hit', lane: 2 }, { timeSec: b, kind: 'beat' }];
  f.transport.setClicks(events, { metronome: 0.5, hits: 0.7 }); f.transport.setLoop(a, b); f.transport.seek(59.99); await f.transport.play(); await ready(f);
  f.context.advance(0.02); await ready(f); paired(f.context); bounded(f);
  for (const node of overlayNodes(f.context)) {
    const music = musicNodes(f.context)[overlayNodes(f.context).indexOf(node)];
    // Infer raw fragment origin from its source offset and the expected ordered ranges.
    const index = overlayNodes(f.context).indexOf(node), origins = [30, 60, 0, 30];
    if (index >= origins.length) break;
    const origin = origins[index]; const output = node.buffer.getChannelData(0);
    const begin = Math.round(node.startArgs[1] * 48000), end = begin + Math.round(node.startArgs[2] * 48000);
    for (let sample = begin; sample < end; sample++) {
      let expected = 0;
      for (const event of events.slice(1, 4)) {
        const phase = origin * 48000 + sample - Math.round(event.timeSec * 48000);
        if (phase < 0 || phase >= 960) continue;
        const t = phase / 48000; expected = Math.fround(expected + 0.7 * (1 - t / 0.02) * Math.sin(2 * Math.PI * (600 + 150 * event.lane) * t));
      }
      near(output[sample], expected);
    }
    assert.deepEqual(node.startArgs, music.startArgs);
  }
});

await check('long loop pause, rate, seek, clear and stale generation preserve ownership', async () => {
  const f = fixture(150, 1000); f.transport.setLoop(29.9, 90.1); f.transport.seek(89.9); await f.transport.play();
  const stale = f.requests.slice(); f.transport.seek(30.025); await flush();
  assert.ok(stale.every(r => r.signal.aborted)); stale[0].resolve(); stale[1].reject(new Error('stale')); await flush();
  assert.equal(f.context.buffers.length, 0); await ready(f);
  f.context.advance(0.25, false); f.transport.pause(); near(f.last().position, 30.275);
  await f.transport.play(); const old = f.transport.sources.slice(); f.transport.setRate(0.5); await flush(); await ready(f);
  assert.ok(old.every(n => n.stopped && n.disconnected)); near(f.last().position, 30.275);
  f.transport.seek(90.1); await flush(); await ready(f); near(f.last().position, 29.9);
  f.transport.setLoop(null); await flush(); await ready(f); near(f.last().position, 29.9);
  assert.equal(f.transport.loop, null); f.transport.dispose(); assert.equal(f.transport.sources.length, 0); assert.equal(f.transport.pending.size, 0);
});

await check('A zero and B audio-end long loop wraps without requesting past EOF', async () => {
  const f = fixture(60.001, 1000, true); f.transport.setLoop(0, 60.001); f.transport.seek(60.001);
  await f.transport.play(); await ready(f); near(f.last().position, 0);
  for (let i = 0; i < 1400; i++) { f.context.advance(0.05); await ready(f); bounded(f); }
  near(f.last().position, 70 - 60.001);
  assert.ok(f.requests.every(r => r.start <= 60000));
  assert.ok(f.requests.filter(r => r.start === 60000).every(r => r.count === 1));
  assert.ok(musicNodes(f.context).some(n => n.startArgs[2] === 0.001)); paired(f.context);
});

await check('long-loop resume cancellation and disposal ignore old asynchronous work', async () => {
  for (const action of ['pause', 'dispose', 'change']) {
    const f = fixture(180, 1000); let resume;
    f.transport.setLoop(29.9, 90.1); f.context.resume = () => new Promise(resolve => { resume = resolve; });
    const playing = f.transport.play(); const oldResume = resume;
    if (action === 'change') f.transport.setLoop(60, 120.1); else f.transport[action]();
    oldResume(); await playing; assert.equal(f.requests.length, 0);
    if (action === 'change') { resume(); await flush(); assert.deepEqual(f.requests.map(r => r.start), [60000, 90000]); }
    f.transport.dispose(); const count = f.context.buffers.length; const emitted = f.states.length;
    for (const r of f.requests) { assert.ok(r.signal.aborted); r.resolve(); }
    await flush(); assert.equal(f.context.buffers.length, count); assert.equal(f.states.length, emitted);
  }
});

console.log(`${passed} long-loop scenarios passed; ${failed} failed`);
assert.equal(failed, 0);
