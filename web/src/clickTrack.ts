export type ClickEvent = { timeSec: number; kind: 'beat' | 'downbeat' | 'hit'; lane?: 0 | 1 | 2 | 3 };
export type ClickLevels = { metronome: number; hits: number };

/** A 20 ms sine burst with a linear decay: (1 - t / 0.02) * sin(2πft).
 * Frequencies are 1000 Hz for beats, 1500 Hz for downbeats, and
 * 600 + 150 * lane Hz for hits (lane defaults to zero). Samples add without limiting.
 */
export function renderClickTrack(events: readonly ClickEvent[], startSample: number, frames: number, sampleRate: number, levels: ClickLevels): Float32Array {
  if (!Number.isSafeInteger(startSample) || startSample < 0 || !Number.isSafeInteger(frames) || frames < 0 || !Number.isSafeInteger(startSample + frames) || !Number.isSafeInteger(sampleRate) || sampleRate <= 0) throw new RangeError('Invalid sample window or rate');
  if (!levels || ![levels.metronome, levels.hits].every(x => Number.isFinite(x) && x >= 0 && x <= 1)) throw new RangeError('Invalid levels');
  if (!Array.isArray(events)) throw new TypeError('Invalid events');
  const validated = events.map(event => {
    if (!event || !Number.isFinite(event.timeSec) || event.timeSec < 0 || !['beat', 'downbeat', 'hit'].includes(event.kind) || (event.lane !== undefined && ![0, 1, 2, 3].includes(event.lane))) throw new RangeError('Invalid event');
    const sample = Math.round(event.timeSec * sampleRate);
    if (!Number.isSafeInteger(sample)) throw new RangeError('Invalid event sample');
    return { event, sample };
  });
  const output = new Float32Array(frames);
  const length = Math.ceil(sampleRate * 0.02);
  for (const { event, sample } of validated) {
    const frequency = event.kind === 'beat' ? 1000 : event.kind === 'downbeat' ? 1500 : 600 + 150 * (event.lane ?? 0);
    const gain = event.kind === 'hit' ? levels.hits : levels.metronome;
    const first = Math.max(0, startSample - sample);
    const end = Math.min(length, startSample + frames - sample);
    for (let i = first; i < end; i++) {
      const t = i / sampleRate;
      output[sample + i - startSample] += gain * (1 - t / 0.02) * Math.sin(2 * Math.PI * frequency * t);
    }
  }
  return output;
}
