import { http, HttpResponse } from 'msw';
import type { createDemoStore } from './store';
import { pcm, peak } from './media';
export function createMediaHandlers(store: ReturnType<typeof createDemoStore>) {
  const audio = (id: string) => store.read().projects.find(p => p.project.id === id)?.project.audio;
  const fail = () => HttpResponse.json({ detail: 'Demo media request is invalid' }, { status: 404 });
  const integer = (v: string | null) => v !== null && /^\d+$/.test(v) && Number.isSafeInteger(Number(v));
  const levels = [256, 1024, 4096, 16384, 65536, 262144, 1440000];
  return [
    http.get('/api/projects/:id/audio/pcm', ({ params, request }) => {
      const a = audio(String(params.id)); const q = new URL(request.url).searchParams;
      if (!a || q.get('audio_hash') !== a.sha256 || !integer(q.get('start_sample')) || !integer(q.get('frame_count'))) return fail();
      const start = Math.min(Number(q.get('start_sample')), a.frames); const count = Math.min(Number(q.get('frame_count')), a.frames - start);
      if (!Number(q.get('frame_count')) || Number(q.get('frame_count')) > a.sample_rate * 30) return fail();
      return new HttpResponse(pcm(start, count), { headers: { 'Content-Type': 'application/octet-stream', 'X-Audio-Hash': a.sha256, 'X-Source-Start-Sample': String(start), 'X-Sample-Rate': String(a.sample_rate), 'X-Channels': String(a.channels), 'X-Frames': String(count) } });
    }),
    http.get('/api/projects/:id/audio/waveform', ({ params, request }) => {
      const a = audio(String(params.id)); if (!a || new URL(request.url).searchParams.get('audio_hash') !== a.sha256) return fail();
      return HttpResponse.json({ hash: a.sha256, sample_rate: a.sample_rate, channels: a.channels, frames: a.frames, levels: levels.map(n => ({ samples_per_peak: n, count: Math.ceil(a.frames / n) })) });
    }),
    http.get('/api/projects/:id/audio/waveform/peaks', ({ params, request }) => {
      const a = audio(String(params.id)); const q = new URL(request.url).searchParams;
      if (!a || q.get('audio_hash') !== a.sha256 || !integer(q.get('level')) || !integer(q.get('start')) || !integer(q.get('count'))) return fail();
      const level = Number(q.get('level')), start = Number(q.get('start')), count = Number(q.get('count')), size = levels[level];
      if (!size || count > 16384 || start + count > Math.ceil(a.frames / size)) return fail();
      const manifest = { hash: a.sha256, sample_rate: a.sample_rate, channels: a.channels, frames: a.frames, levels: levels.map(n => ({ samples_per_peak: n, count: Math.ceil(a.frames / n) })) };
      return HttpResponse.json({ manifest, level, start, peaks: Array.from({ length: count }, (_, i) => [peak((start + i) * size, Math.min((start + i + 1) * size, a.frames))]) });
    }),
    http.get('/api/projects/:id/covers/:coverId', async ({ params }) => {
      const project = store.read().projects.find(p => p.project.id === params.id)?.project;
      const coverId = project?.cover?.path.match(/^assets\/([0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12})\/cover\.jpg$/i)?.[1];
      if (!coverId || coverId !== String(params.coverId)) return fail();
      const response = await fetch('/demo-cover.jpg');
      return new HttpResponse(await response.arrayBuffer(), { headers: { 'Content-Type': 'image/jpeg' } });
    }),
  ];
}
