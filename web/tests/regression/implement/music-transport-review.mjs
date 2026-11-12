import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import ts from '../../../../web/node_modules/typescript/lib/typescript.js';

const source = await readFile(new URL('../../../../web/src/transport.ts', import.meta.url), 'utf8');
const clickSource = await readFile(new URL('../../../../web/src/clickTrack.ts', import.meta.url), 'utf8');
const compile = text => ts.transpileModule(text, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 } }).outputText;
const clickUrl = `data:text/javascript;base64,${Buffer.from(compile(clickSource)).toString('base64')}`;
const compiled = compile(source).replace("'./clickTrack'", JSON.stringify(clickUrl));
const { Transport } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`);
const timers = new Map();
let timerId = 0;
globalThis.setInterval = callback => { timers.set(++timerId, callback); return timerId; };
globalThis.clearInterval = id => timers.delete(id);
const flush = async () => { for (let i = 0; i < 8; i++) await Promise.resolve(); };
const pump = () => { for (const callback of [...timers.values()]) callback(); };
const near = (actual, expected) => assert.ok(Math.abs(actual - expected) < 1e-8, `${actual} != ${expected}`);

class FakeContext {
  currentTime = 100;
  destination = {};
  nodes = [];
  buffers = [];
  closed = false;
  async resume() {}
  async close() { this.closed = true; }
  createBuffer(channels, frames, rate) {
    const data = Array.from({ length: channels }, () => new Float32Array(frames));
    assert.ok(Number.isInteger(frames) && frames > 0);
    const buffer = { duration: frames / rate, length: frames, sampleRate: rate, numberOfChannels: channels, getChannelData: i => data[i] };
    this.buffers.push(buffer);
    return buffer;
  }
  createBufferSource() {
    const node = {
      playbackRate: { value: 1 }, disconnected: false, stopped: false,
      connect: destination => assert.equal(destination, this.destination),
      disconnect() { this.disconnected = true; },
      stop() { this.stopped = true; },
      start(...args) { this.startArgs = args; },
    };
    this.nodes.push(node);
    return node;
  }
  advance(seconds, runTimer = true) {
    this.currentTime += seconds;
    for (const node of this.nodes) {
      if (!node.loop && !node.ended && node.startArgs?.length === 3 && this.currentTime >= node.startArgs[0] + node.startArgs[2] / node.playbackRate.value) {
        node.ended = true; node.onended?.();
      }
    }
    if (runTimer) pump();
  }
}
function fixture(seconds = 180, sampleRate = 10, distinctFrames = false) {
  const context = new FakeContext();
  const requests = [];
  const states = [];
  const audio = { frames: seconds * sampleRate, sample_rate: sampleRate, channels: 2, sha256: 'test' };
  const loader = (start, count, signal) => new Promise((resolve, reject) => {
    const request = { start, count, signal, reject, resolve: () => {
      const samples = new Float32Array(count * 2);
      if (distinctFrames) for (let i = 0; i < count; i++) { samples[i * 2] = start + i; samples[i * 2 + 1] = -(start + i); }
      resolve({ sourceStartSample: start, sampleRate, channels: 2, frames: count, hash: 'test', samples });
    } };
    requests.push(request);
  });
  const transport = new Transport(audio, loader, state => states.push(state), context);
  return { context, requests, transport, states, last: () => states.at(-1) };
}
const musicNodes = context => context.nodes.filter(node => node.buffer.numberOfChannels === 2);
const overlayNodes = context => context.nodes.filter(node => node.buffer.numberOfChannels === 1);
function paired(context) {
  const music = musicNodes(context); const overlay = overlayNodes(context);
  assert.equal(music.length, overlay.length);
  for (let i = 0; i < music.length; i++) {
    assert.deepEqual(overlay[i].startArgs, music[i].startArgs);
    assert.equal(overlay[i].playbackRate.value, music[i].playbackRate.value);
    assert.equal(overlay[i].buffer.length, music[i].buffer.length);
    assert.equal(overlay[i].buffer.sampleRate, music[i].buffer.sampleRate);
  }
}
let passed = 0;
async function check(name, body) {
  try { await body(); passed++; console.log(`PASS ${name}`); }
  finally { timers.clear(); }
}

await check('origin 45 seconds, rate 0.5, contiguous source-seconds duration', async () => {
  const f = fixture();
  f.transport.seek(45); f.transport.setRate(0.5); await f.transport.play();
  assert.deepEqual(f.requests.map(r => [r.start, r.count]), [[300, 300], [600, 300]]);
  f.requests[0].resolve(); await flush();
  assert.deepEqual(f.context.nodes[0].startArgs, [100, 15, 15]);
  assert.equal(f.context.nodes[0].playbackRate.value, 0.5);
  f.requests[1].resolve(); await flush();
  assert.deepEqual(musicNodes(f.context)[1].startArgs, [130, 0, 30]); paired(f.context);
  f.context.advance(10); near(f.last().position, 50);
  f.context.advance(20); near(f.last().position, 60);
  assert.equal(f.last().status, 'playing');
  assert.equal(f.requests[2].start, 900);
  f.transport.dispose(); assert.equal(f.context.closed, false);
});

await check('deferred next load freezes at boundary and resumes without jump', async () => {
  const f = fixture(); await f.transport.play();
  f.requests[0].resolve(); await flush();
  f.context.advance(35); near(f.last().position, 30); assert.equal(f.last().status, 'buffering');
  f.context.advance(20); near(f.last().position, 30);
  f.requests[1].resolve(); await flush();
  assert.deepEqual(musicNodes(f.context)[1].startArgs, [155, 0, 30]); paired(f.context);
  near(f.last().position, 30); f.context.advance(2); near(f.last().position, 32);
  f.transport.dispose();
});

await check('pause and rate changes use context time, stop and disconnect all nodes', async () => {
  const f = fixture(); await f.transport.play();
  f.requests.slice().forEach(r => r.resolve()); await flush();
  f.context.advance(7, false); f.transport.pause(); near(f.last().position, 7);
  assert.ok(f.context.nodes.every(n => n.stopped && n.disconnected)); assert.equal(timers.size, 0);
  await f.transport.play(); f.context.advance(3, false);
  const previous = f.context.nodes.slice(); f.transport.setRate(0.5); await flush();
  near(f.last().position, 10); assert.ok(previous.every(n => n.stopped && n.disconnected));
  assert.deepEqual(musicNodes(f.context).at(-2).startArgs, [110, 10, 20]); paired(f.context);
  f.context.advance(4); near(f.last().position, 12);
  f.transport.dispose();
});

await check('seek in flight aborts requests and ignores stale resolution and rejection', async () => {
  const f = fixture(); await f.transport.play(); const stale = f.requests.slice();
  f.transport.seek(95); await flush(); assert.ok(stale.every(r => r.signal.aborted));
  stale[0].resolve(); stale[1].reject(new Error('stale')); await flush();
  assert.equal(f.context.buffers.length, 0); assert.equal(f.last().status, 'buffering');
  assert.deepEqual(f.requests.slice(2).map(r => r.start), [900, 1200]);
  f.requests[2].resolve(); await flush(); assert.deepEqual(f.context.nodes[0].startArgs, [100, 5, 25]);
  const nodes = f.context.nodes.slice(); f.transport.seek(1); await flush();
  assert.ok(nodes.every(n => n.stopped && n.disconnected)); f.transport.dispose();
});

await check('dispose aborts pending loads and leaves injected context open', async () => {
  const f = fixture(); await f.transport.play(); f.requests[0].resolve(); await flush();
  const pending = f.requests[1]; const emitted = f.states.length; f.transport.dispose();
  assert.ok(pending.signal.aborted); assert.ok(f.context.nodes.every(n => n.stopped && n.disconnected));
  pending.resolve(); await flush(); assert.equal(f.states.length, emitted);
  assert.equal(f.transport.cache.size, 0); assert.equal(timers.size, 0); assert.equal(f.context.closed, false);
  await f.transport.play(); assert.equal(timers.size, 0);
});

await check('pause and dispose invalidate a pending context resume', async () => {
  for (const action of ['pause', 'dispose']) {
    const f = fixture(); let resume;
    f.context.resume = () => new Promise(resolve => { resume = resolve; });
    const playing = f.transport.play(); f.transport[action](); resume(); await playing;
    assert.equal(f.requests.length, 0); assert.equal(timers.size, 0); f.transport.dispose();
  }
});

await check('loader error stops existing music and aborts remaining work', async () => {
  const f = fixture(); await f.transport.play(); f.requests[0].resolve(); await flush();
  f.context.advance(5, false); f.requests[1].reject(new Error('network failure')); await flush();
  assert.equal(f.last().status, 'error'); near(f.last().position, 5);
  assert.equal(f.last().error.message, 'network failure');
  assert.ok(f.context.nodes.every(n => n.stopped && n.disconnected)); assert.equal(timers.size, 0);
  f.transport.dispose();
});

await check('long playback bounds cache to current and next and handles final short chunk', async () => {
  const f = fixture(125, 44100); await f.transport.play();
  for (let i = 0; i < 125; i++) {
    for (const r of f.requests) if (!r.resolved) { r.resolved = true; r.resolve(); }
    await flush(); assert.ok(f.transport.cache.size <= 2);
    assert.ok(f.transport.pending.size <= 2);
    f.context.advance(1);
  }
  near(f.last().position, 125); assert.equal(f.last().status, 'paused');
  assert.equal(f.requests.at(-1).count, 5 * 44100);
  const scheduled = musicNodes(f.context).map(n => n.startArgs); paired(f.context);
  assert.deepEqual(scheduled.map(s => s[0]), [100, 130, 160, 190, 220]);
  assert.deepEqual(scheduled.map(s => s[2]), [30, 30, 30, 30, 5]);
  assert.equal(timers.size, 0); f.transport.dispose();
});

await check('overlay rate 0.5 shares music offset and keeps click tail at seek 45', async () => {
  const f = fixture(95, 48000);
  f.transport.setClicks([{ timeSec: 44.99, kind: 'hit', lane: 1 }], { metronome: 0, hits: 0.8 });
  f.transport.seek(45); f.transport.setRate(0.5); await f.transport.play();
  f.requests.slice().forEach(r => r.resolve()); await flush(); paired(f.context);
  const overlay = overlayNodes(f.context)[0];
  assert.deepEqual(overlay.startArgs, [100, 15, 15]); assert.equal(overlay.playbackRate.value, 0.5);
  const t = 481 / 48000;
  near(overlay.buffer.getChannelData(0)[15 * 48000 + 1], 0.8 * (1 - t / 0.02) * Math.sin(2 * Math.PI * 750 * t));
  f.transport.dispose(); assert.ok(f.context.nodes.every(n => n.stopped && n.disconnected));
});

await check('click burst crosses 30-second fragment boundary with matching sample phase', async () => {
  const f = fixture(32, 48000);
  f.transport.setClicks([{ timeSec: 29.99, kind: 'hit', lane: 1 }], { metronome: 0, hits: 0.8 });
  await f.transport.play(); f.requests.slice().forEach(r => r.resolve()); await flush(); paired(f.context);
  const overlay = overlayNodes(f.context);
  assert.deepEqual(overlay.map(n => n.startArgs), [[100, 0, 30], [130, 0, 2]]);
  const first = overlay[0].buffer.getChannelData(0); const second = overlay[1].buffer.getChannelData(0);
  for (const [output, index, phase] of [[first, first.length - 1, 479], [second, 0, 480], [second, 1, 481]]) {
    const t = phase / 48000;
    near(output[index], 0.8 * (1 - t / 0.02) * Math.sin(2 * Math.PI * 750 * t));
  }
  assert.ok(second.slice(480).every(x => x === 0)); f.transport.dispose();
});

await check('muted levels produce zero overlay and metronome and hit levels remain separate', async () => {
  const f = fixture(31, 48000);
  const events = [{ timeSec: 1, kind: 'beat' }, { timeSec: 2, kind: 'hit', lane: 1 }];
  f.transport.setClicks(events, { metronome: 0, hits: 0 }); await f.transport.play();
  f.requests.slice().forEach(r => r.resolve()); await flush();
  assert.ok(overlayNodes(f.context).every(n => n.buffer.getChannelData(0).every(x => x === 0)));
  f.transport.setClicks(events, { metronome: 0, hits: 0.5 }); await f.transport.play(); paired(f.context);
  const enabled = overlayNodes(f.context).at(-2).buffer.getChannelData(0);
  assert.equal(enabled[48001], 0); assert.notEqual(enabled[96001], 0); f.transport.dispose();
});

await check('setClicks pauses at context position and clones caller events and levels', async () => {
  const f = fixture(90, 48000); await f.transport.play();
  f.requests[0].resolve(); await flush(); f.context.advance(5, false);
  const previous = f.context.nodes.slice();
  const events = [{ timeSec: 6.001, kind: 'hit', lane: 1 }]; const levels = { metronome: 0, hits: 0.6 };
  f.transport.setClicks(events, levels);
  near(f.last().position, 5); assert.equal(f.last().status, 'paused'); assert.equal(timers.size, 0);
  assert.ok(previous.every(n => n.stopped && n.disconnected)); assert.ok(f.requests[1].signal.aborted);
  events[0].timeSec = 70; events[0].lane = 3; events.push({ timeSec: 7, kind: 'beat' }); levels.hits = 0;
  f.requests[1].resolve(); await flush();
  assert.equal(f.context.nodes.length, previous.length);
  await f.transport.play(); paired(f.context);
  const overlay = overlayNodes(f.context).at(-1);
  assert.deepEqual(overlay.startArgs, [105, 5, 25]);
  near(overlay.buffer.getChannelData(0)[Math.round(6.001 * 48000) + 1], 0.6 * (1 - 1 / 960) * Math.sin(2 * Math.PI * 750 / 48000));
  assert.equal(overlay.buffer.getChannelData(0)[7 * 48000 + 1], 0);
  f.transport.dispose(); assert.ok(f.context.nodes.every(n => n.stopped && n.disconnected));
});

await check('fractional seek reaches exact EOF without an extra source pair', async () => {
  const f = fixture(8671 / 44100, 44100); f.transport.seek(0.1); await f.transport.play();
  f.requests[0].resolve(); await flush(); const count = f.context.nodes.length;
  f.context.advance(1);
  assert.equal(f.last().status, 'paused'); assert.equal(f.context.nodes.length, count);
  assert.equal(f.last().position, 8671 / 44100); f.transport.dispose();
});

await check('pause after exhausted fractional coverage preserves exact EOF', async () => {
  const f = fixture(8671 / 44100, 44100); f.transport.seek(0.1); await f.transport.play();
  f.requests[0].resolve(); await flush(); const count = f.context.nodes.length;
  f.context.advance(1, false); f.transport.pause();
  assert.equal(f.last().position, 8671 / 44100); assert.equal(f.last().status, 'paused');
  assert.equal(f.context.nodes.length, count); assert.equal(timers.size, 0);
  assert.ok(f.context.nodes.every(node => (node.stopped || node.ended) && node.disconnected)); f.transport.dispose();
});

await check('rate change snapshots exact exhausted endpoint before normal EOF replay', async () => {
  const f = fixture(8671 / 44100, 44100); f.transport.seek(0.1); await f.transport.play();
  f.requests[0].resolve(); await flush(); const previous = f.context.nodes.slice();
  f.context.advance(1, false); const firstState = f.states.length; f.transport.setRate(0.5);
  assert.equal(f.states[firstState].status, 'paused'); assert.equal(f.states[firstState].position, 8671 / 44100);
  assert.ok(previous.every(node => (node.stopped || node.ended) && node.disconnected)); await flush();
  const replay = musicNodes(f.context).at(-1);
  assert.deepEqual(replay.startArgs, [101, 0, 8671 / 44100]); assert.equal(replay.playbackRate.value, 0.5);
  paired(f.context); f.transport.dispose();
});

console.log(`${passed} controlled transport scenarios passed`);
