import type { AudioAsset } from './generated/ui-contract';
import { ApiError } from './api';

type Peak = [number, number];
interface Level { samples_per_peak: number; count: number }
interface Manifest { hash: string; sample_rate: number; channels: number; frames: number; levels: Level[] }
export interface WaveformPeaks { peaks: Peak[][]; sourceStartSample: number; samplesPerPeak: number; frames: number }

const integer = (n: unknown): n is number => typeof n === 'number' && Number.isSafeInteger(n) && n >= 0;
async function json(url: string, signal?: AbortSignal): Promise<any> {
  const response = await fetch(url, { method: 'GET', credentials: 'same-origin', signal });
  if (!response.ok) {
    let detail: unknown;
    try { detail = (await response.json()).detail; } catch (error) {
      if (signal?.aborted || (error instanceof Error && error.name === 'AbortError')) throw error;
      detail = response.statusText;
    }
    throw new ApiError(response.status, detail);
  }
  return response.json();
}
function validateManifest(value: any, audio: AudioAsset): asserts value is Manifest {
  if (!value || value.hash !== audio.sha256 || value.sample_rate !== audio.sample_rate ||
      value.channels !== audio.channels || value.frames !== audio.frames || !Array.isArray(value.levels) || !value.levels.length ||
      !value.levels.every((level: any, i: number) => level && integer(level.samples_per_peak) && level.samples_per_peak > 0 &&
        integer(level.count) && level.count === Math.ceil(audio.frames / level.samples_per_peak) &&
        (i === 0 || level.samples_per_peak > value.levels[i - 1].samples_per_peak))) {
    throw new Error('Invalid waveform manifest');
  }
}
export async function loadWaveformPeaks(projectId: string, audio: AudioAsset, startSec: number, endSec: number, targetBins: number, signal?: AbortSignal): Promise<WaveformPeaks> {
  if (!projectId || !audio.sha256 || !integer(audio.frames) || !integer(audio.sample_rate) || !audio.sample_rate ||
      !integer(audio.channels) || !audio.channels || !Number.isFinite(startSec) || !Number.isFinite(endSec) ||
      startSec < 0 || endSec < startSec || !integer(targetBins) || !targetBins) throw new RangeError('Invalid waveform request');
  const base = `/api/projects/${encodeURIComponent(projectId)}/audio/waveform`;
  const query = new URLSearchParams({ audio_hash: audio.sha256 });
  const manifest = await json(`${base}?${query}`, signal);
  validateManifest(manifest, audio);
  const startSample = Math.min(startSec * audio.sample_rate, audio.frames);
  const endSample = Math.min(endSec * audio.sample_rate, audio.frames);
  if (startSample >= endSample) return { peaks: [], sourceStartSample: Math.floor(startSample), samplesPerPeak: manifest.levels[0].samples_per_peak, frames: audio.frames };
  const limit = Math.min(targetBins, 16384);
  const index = manifest.levels.findIndex(level => Math.ceil(endSample / level.samples_per_peak) - Math.floor(startSample / level.samples_per_peak) <= limit);
  if (index < 0) throw new RangeError('No waveform level fits requested bin limit');
  const level = manifest.levels[index];
  const start = Math.floor(startSample / level.samples_per_peak);
  const count = Math.ceil(endSample / level.samples_per_peak) - start;
  query.set('level', String(index)); query.set('start', String(start)); query.set('count', String(count));
  const result = await json(`${base}/peaks?${query}`, signal);
  validateManifest(result?.manifest, audio);
  const sameManifest = result.manifest.hash === manifest.hash && result.manifest.sample_rate === manifest.sample_rate &&
    result.manifest.channels === manifest.channels && result.manifest.frames === manifest.frames &&
    result.manifest.levels.length === manifest.levels.length && result.manifest.levels.every((item: Level, i: number) =>
      item.samples_per_peak === manifest.levels[i].samples_per_peak && item.count === manifest.levels[i].count);
  if (!sameManifest || result.level !== index || result.start !== start ||
      !Array.isArray(result.peaks) || result.peaks.length !== count ||
      !result.peaks.every((bin: any) => Array.isArray(bin) && bin.length === audio.channels && bin.every((pair: any) =>
        Array.isArray(pair) && pair.length === 2 && pair.every((v: unknown) => typeof v === 'number' && Number.isFinite(v)) && pair[0] <= pair[1]))) {
    throw new Error('Invalid waveform peaks');
  }
  return { peaks: result.peaks, sourceStartSample: start * level.samples_per_peak, samplesPerPeak: level.samples_per_peak, frames: audio.frames };
}
