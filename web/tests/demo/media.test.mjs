import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { strict as assert } from 'node:assert';
import { test } from 'node:test';
import { setupServer } from 'msw/node';
const ts = createRequire(import.meta.url)('typescript');
const encode = s => `data:text/javascript;base64,${Buffer.from(s).toString('base64')}`;
const media = encode(ts.transpileModule(readFileSync(new URL('../../src/demo/media.ts', import.meta.url), 'utf8'), { compilerOptions: { module: ts.ModuleKind.ESNext } }).outputText);
let source = ts.transpileModule(readFileSync(new URL('../../src/demo/mediaHandlers.ts', import.meta.url), 'utf8'), { compilerOptions: { module: ts.ModuleKind.ESNext } }).outputText;
source = source.replaceAll('/api/', 'http://localhost/api/');
source = source.replace("'./media'", `'${media}'`).replace("'msw'", `'${import.meta.resolve('msw')}'`);
const { createMediaHandlers } = await import(encode(source));
const audio = { sha256: 'abc', frames: 1440000, channels: 1, sample_rate: 48000 };
const cover = { path: 'assets/00000000-0000-4000-8000-000000000777/cover.jpg' };
const server = setupServer(...createMediaHandlers({ read: () => ({ projects: [{ project: { id: 'p', audio, cover } }] }) }));
server.listen({ onUnhandledRequest: 'error' });
test('PCM seek and end return exact frames and headers', async () => {
  const get = (start, count) => fetch(`http://localhost/api/projects/p/audio/pcm?audio_hash=abc&start_sample=${start}&frame_count=${count}`);
  let r = await get(1439998, 10); assert.equal(r.headers.get('x-frames'), '2'); assert.equal(r.headers.get('x-source-start-sample'), '1439998'); assert.equal(r.headers.get('x-audio-hash'), 'abc'); assert.equal((await r.arrayBuffer()).byteLength, 8);
  r = await get(1500000, 10); assert.equal(r.headers.get('x-source-start-sample'), '1440000'); assert.equal((await r.arrayBuffer()).byteLength, 0);
  r = await get(100, 2); assert.equal(new DataView(await r.arrayBuffer()).getFloat32(0, true), -0.25);
});
test('waveform peaks match the generated signal', async () => {
  const base = 'http://localhost/api/projects/p/audio/waveform';
  const manifest = await (await fetch(`${base}?audio_hash=abc`)).json(); assert.equal(manifest.frames, audio.frames);
  const result = await (await fetch(`${base}/peaks?audio_hash=abc&level=0&start=0&count=1`)).json(); assert.deepEqual(result.peaks, [[[-0.25, 0.25]]]);
});
test('cover route requires the exact asset UUID', async () => {
  const invalid = await fetch('http://localhost/api/projects/p/covers/000');
  assert.equal(invalid.status, 404);
});
process.on('exit', () => server.close());

