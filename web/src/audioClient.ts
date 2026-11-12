import type { AudioAsset } from './generated/ui-contract';
import { ApiError } from './api';

export interface PcmFragment {
  samples: Float32Array;
  sourceStartSample: number;
  sampleRate: number;
  channels: number;
  frames: number;
  hash: string;
}

export async function loadPcmFragment(projectId: string, audio: AudioAsset, startSample: number, frameCount: number, signal?: AbortSignal): Promise<PcmFragment> {
  const integer = (n: number) => Number.isSafeInteger(n) && n >= 0;
  if (!integer(startSample) || !integer(frameCount) || frameCount === 0 ||
      !integer(audio.frames) || !integer(audio.sample_rate) || audio.sample_rate === 0 ||
      !integer(audio.channels) || audio.channels === 0 || frameCount > 30 * audio.sample_rate) {
    throw new RangeError('Invalid PCM request');
  }
  const query = new URLSearchParams({ audio_hash: audio.sha256, start_sample: String(startSample), frame_count: String(frameCount) });
  const response = await fetch(`/api/projects/${encodeURIComponent(projectId)}/audio/pcm?${query}`, { method: 'GET', credentials: 'same-origin', signal });
  if (!response.ok) {
    let detail: unknown;
    try { detail = (await response.json()).detail; } catch (error) {
      if (error && typeof error === 'object' && 'name' in error && error.name === 'AbortError') throw error;
      detail = response.statusText;
    }
    throw new ApiError(response.status, detail);
  }
  const header = (name: string): string => {
    const value = response.headers.get(name);
    if (value === null || value === '') throw new Error(`Missing PCM header: ${name}`);
    return value;
  };
  const numberHeader = (name: string): number => {
    const value = header(name);
    if (!/^\d+$/.test(value) || !integer(Number(value))) throw new Error(`Invalid PCM header: ${name}`);
    return Number(value);
  };
  const hash = header('X-Audio-Hash');
  const sourceStartSample = numberHeader('X-Source-Start-Sample');
  const sampleRate = numberHeader('X-Sample-Rate');
  const channels = numberHeader('X-Channels');
  const frames = numberHeader('X-Frames');
  const expectedStart = Math.min(startSample, audio.frames);
  if (hash !== audio.sha256 || sourceStartSample !== expectedStart || sampleRate !== audio.sample_rate ||
      channels !== audio.channels || frames !== Math.min(frameCount, audio.frames - expectedStart)) {
    throw new Error('PCM response does not match audio asset');
  }
  const bytes = await response.arrayBuffer();
  if (bytes.byteLength !== frames * channels * 4) throw new Error('Invalid PCM byte length');
  const view = new DataView(bytes);
  const samples = new Float32Array(frames * channels);
  for (let i = 0; i < samples.length; i++) {
    const value = view.getFloat32(i * 4, true);
    if (!Number.isFinite(value)) throw new Error('Invalid PCM sample');
    samples[i] = value;
  }
  return { samples, sourceStartSample, sampleRate, channels, frames, hash };
}
