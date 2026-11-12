import type { AudioAsset } from './generated/ui-contract';
import type { PcmFragment } from './audioClient';
import { renderClickTrack, type ClickEvent, type ClickLevels } from './clickTrack';

export type TransportStatus = 'paused' | 'playing' | 'buffering' | 'error';
export interface TransportState { position: number; status: TransportStatus; error: Error | null }
export type FragmentLoader = (startSample: number, frameCount: number, signal: AbortSignal) => Promise<PcmFragment>;

export class Transport {
  private readonly context: AudioContext;
  private readonly ownsContext: boolean;
  private readonly cache = new Map<number, AudioBuffer>();
  private readonly pending = new Map<number, AbortController>();
  private sources: AudioBufferSourceNode[] = [];
  private position = 0;
  private rate = 1;
  private status: TransportStatus = 'paused';
  private error: Error | null = null;
  private generation = 0;
  private anchorTime = 0;
  private anchorPosition = 0;
  private scheduledEnd = 0;
  private scheduledFrames = 0;
  private nextSourcePosition = 0;
  private timer: ReturnType<typeof setInterval> | null = null;
  private disposed = false;
  private clicks: ClickEvent[] = [];
  private clickLevels: ClickLevels = { metronome: 0, hits: 0 };
  private loop: [number, number] | null = null;

  constructor(private readonly audio: AudioAsset, private readonly loader: FragmentLoader,
    private readonly onState: (state: TransportState) => void, context?: AudioContext) {
    this.context = context ?? new AudioContext();
    this.ownsContext = !context;
  }

  private get duration(): number { return this.audio.frames / this.audio.sample_rate; }
  private get chunkFrames(): number { return 30 * this.audio.sample_rate; }
  private emit(): void { this.onState({ position: this.position, status: this.status, error: this.error }); }
  private updatePosition(): void {
    if (this.status === 'playing' && this.loop) {
      const [a, b] = this.loop;
      const frames = this.anchorPosition * this.audio.sample_rate + Math.min(this.scheduledFrames, Math.max(0, this.context.currentTime - this.anchorTime) * this.rate * this.audio.sample_rate);
      this.position = (a + ((frames - a) % (b - a))) / this.audio.sample_rate;
      return;
    }
    if (this.status === 'playing') this.position = this.context.currentTime >= this.scheduledEnd
      ? this.nextSourcePosition
      : this.anchorPosition + Math.min(this.scheduledFrames, Math.max(0, this.context.currentTime - this.anchorTime) * this.rate * this.audio.sample_rate) / this.audio.sample_rate;
  }
  private reset(): void {
    this.generation++;
    for (const controller of this.pending.values()) controller.abort();
    this.pending.clear();
    for (const source of this.sources) { try { source.stop(); } catch { /* already stopped */ } source.disconnect(); }
    this.sources = [];
    if (this.timer !== null) clearInterval(this.timer);
    this.timer = null;
  }
  private load(start: number): void {
    if (start >= this.audio.frames || this.cache.has(start) || this.pending.has(start)) return;
    const controller = new AbortController();
    const generation = this.generation;
    this.pending.set(start, controller);
    void this.loader(start, Math.min(this.chunkFrames, this.audio.frames - start), controller.signal).then(fragment => {
      if (generation !== this.generation || this.disposed) return;
      const buffer = this.context.createBuffer(fragment.channels, fragment.frames, fragment.sampleRate);
      for (let channel = 0; channel < fragment.channels; channel++) {
        const data = buffer.getChannelData(channel);
        for (let frame = 0; frame < fragment.frames; frame++) data[frame] = fragment.samples[frame * fragment.channels + channel];
      }
      this.cache.set(start, buffer);
      this.pending.delete(start);
      this.tick();
    }).catch((reason: unknown) => {
      if (generation !== this.generation || this.disposed) return;
      this.pending.delete(start);
      this.updatePosition();
      this.reset();
      this.status = 'error';
      this.error = reason instanceof Error ? reason : new Error(String(reason));
      this.emit();
    });
  }
  private tick(): void {
    if (this.status !== 'playing' && this.status !== 'buffering') return;
    this.updatePosition();
    if (this.loop) { if (this.loop[1] - this.loop[0] <= this.chunkFrames) this.tickLoop(); else this.tickLongLoop(); return; }
    if (this.position >= this.duration) { this.reset(); this.position = this.duration; this.status = 'paused'; this.emit(); return; }
    if (this.status === 'playing' && this.context.currentTime >= this.scheduledEnd) {
      this.position = this.nextSourcePosition;
      this.status = 'buffering';
    }
    const current = Math.min(this.audio.frames - 1, Math.floor(this.position * this.audio.sample_rate));
    const currentStart = Math.floor(current / this.chunkFrames) * this.chunkFrames;
    for (const key of this.cache.keys()) if (key !== currentStart && key !== currentStart + this.chunkFrames) this.cache.delete(key);
    this.load(currentStart);
    this.load(currentStart + this.chunkFrames);
    let nextPosition = this.status === 'playing' ? this.nextSourcePosition : this.position;
    while (nextPosition < this.duration) {
      const start = Math.floor(Math.round(nextPosition * this.audio.sample_rate) / this.chunkFrames) * this.chunkFrames;
      const buffer = this.cache.get(start);
      if (!buffer) break;
      const offset = Math.max(0, nextPosition - start / this.audio.sample_rate);
      const length = buffer.duration - offset;
      if (length <= 0) break;
      const when = this.status === 'playing' ? this.scheduledEnd : this.context.currentTime;
      const source = this.context.createBufferSource();
      source.buffer = buffer; source.playbackRate.value = this.rate; source.connect(this.context.destination);
      source.onended = () => { source.disconnect(); this.sources = this.sources.filter(item => item !== source); };
      source.start(when, offset, length);
      this.sources.push(source);
      const clickBuffer = this.context.createBuffer(1, buffer.length, buffer.sampleRate);
      clickBuffer.getChannelData(0).set(renderClickTrack(this.clicks, start, buffer.length, buffer.sampleRate, this.clickLevels));
      const overlay = this.context.createBufferSource();
      overlay.buffer = clickBuffer; overlay.playbackRate.value = this.rate; overlay.connect(this.context.destination);
      overlay.onended = () => { overlay.disconnect(); this.sources = this.sources.filter(item => item !== overlay); };
      overlay.start(when, offset, length);
      this.sources.push(overlay);
      if (this.status === 'buffering') { this.anchorTime = when; this.anchorPosition = nextPosition; this.scheduledFrames = 0; this.status = 'playing'; }
      const nextSourcePosition = Math.min(this.duration, start / this.audio.sample_rate + buffer.duration);
      this.scheduledFrames = nextSourcePosition >= this.duration
        ? (this.duration - this.anchorPosition) * this.audio.sample_rate
        : this.scheduledFrames + length * this.audio.sample_rate;
      this.scheduledEnd = this.anchorTime + this.scheduledFrames / this.audio.sample_rate / this.rate;
      nextPosition = nextSourcePosition;
      this.nextSourcePosition = nextPosition;
    }
    this.emit();
  }
  private tickLoop(): void {
    if (!this.loop || this.status === 'playing') { this.emit(); return; }
    const [a, b] = this.loop;
    const first = Math.floor(a / this.chunkFrames) * this.chunkFrames;
    const last = Math.floor((b - 1) / this.chunkFrames) * this.chunkFrames;
    for (const key of this.cache.keys()) if (key !== first && key !== last) this.cache.delete(key);
    this.load(first); this.load(last);
    const left = this.cache.get(first); const right = this.cache.get(last);
    if (!left || !right) { this.emit(); return; }
    const music = this.context.createBuffer(left.numberOfChannels, b - a, this.audio.sample_rate);
    for (let channel = 0; channel < music.numberOfChannels; channel++) {
      const output = music.getChannelData(channel);
      const split = Math.min(b, first + this.chunkFrames);
      output.set(left.getChannelData(channel).subarray(a - first, split - first));
      if (last !== first) output.set(right.getChannelData(channel).subarray(0, b - last), split - a);
    }
    const overlay = this.context.createBuffer(1, b - a, this.audio.sample_rate);
    const events = this.clicks.filter(event => { const frame = Math.round(event.timeSec * this.audio.sample_rate); return frame >= a && frame < b; });
    overlay.getChannelData(0).set(renderClickTrack(events, a, b - a, this.audio.sample_rate, this.clickLevels));
    const when = this.context.currentTime; const offset = this.position - a / this.audio.sample_rate;
    for (const buffer of [music, overlay]) {
      const source = this.context.createBufferSource(); source.buffer = buffer; source.loop = true;
      source.playbackRate.value = this.rate; source.connect(this.context.destination); source.start(when, offset);
      this.sources.push(source);
    }
    this.anchorTime = when; this.anchorPosition = this.position; this.scheduledFrames = Infinity; this.scheduledEnd = Infinity; this.status = 'playing'; this.emit();
  }
  private tickLongLoop(): void {
    if (!this.loop) return;
    const [a, b] = this.loop; const sampleRate = this.audio.sample_rate;
    if (this.status === 'playing' && this.context.currentTime >= this.scheduledEnd) this.status = 'buffering';
    const current = Math.floor(this.position * sampleRate);
    const currentStart = Math.floor(current / this.chunkFrames) * this.chunkFrames;
    const next = Math.min(currentStart + this.chunkFrames, b) >= b ? a : currentStart + this.chunkFrames;
    const keep = new Set([currentStart, Math.floor(next / this.chunkFrames) * this.chunkFrames, Math.floor(a / this.chunkFrames) * this.chunkFrames]);
    for (const key of this.cache.keys()) if (!keep.has(key)) this.cache.delete(key);
    for (const key of keep) this.load(key);
    let cursor = this.status === 'playing' ? this.nextSourcePosition * sampleRate : this.position * sampleRate;
    const events = this.clicks.filter(event => { const frame = Math.round(event.timeSec * sampleRate); return frame >= a && frame < b; });
    while (true) {
      const consumed = this.status === 'playing' ? Math.max(0, this.context.currentTime - this.anchorTime) * this.rate * sampleRate : 0;
      if (this.status === 'playing' && this.scheduledFrames - consumed >= this.chunkFrames) break;
      const start = Math.floor(cursor / this.chunkFrames) * this.chunkFrames;
      const buffer = this.cache.get(start); if (!buffer) break;
      const end = Math.min(start + buffer.length, b); const length = end - cursor; if (length <= 0) break;
      const when = this.status === 'playing' ? this.scheduledEnd : this.context.currentTime;
      const click = this.context.createBuffer(1, buffer.length, sampleRate);
      click.getChannelData(0).set(renderClickTrack(events, start, buffer.length, sampleRate, this.clickLevels));
      for (const data of [buffer, click]) {
        const source = this.context.createBufferSource(); source.buffer = data; source.playbackRate.value = this.rate; source.connect(this.context.destination);
        source.onended = () => { source.disconnect(); this.sources = this.sources.filter(item => item !== source); };
        source.start(when, (cursor - start) / sampleRate, length / sampleRate); this.sources.push(source);
      }
      if (this.status === 'buffering') { this.anchorTime = when; this.anchorPosition = cursor / sampleRate; this.scheduledFrames = 0; this.status = 'playing'; }
      this.scheduledFrames += length; this.scheduledEnd = this.anchorTime + this.scheduledFrames / sampleRate / this.rate;
      cursor = end === b ? a : end; this.nextSourcePosition = cursor / sampleRate;
    }
    this.emit();
  }
  setLoop(start: number | null, end?: number): void {
    let next: [number, number] | null = null;
    if (start !== null) {
      if (!Number.isFinite(start) || end === undefined || !Number.isFinite(end) || start < 0 || start >= end || end > this.duration) throw new RangeError('Invalid loop');
      const a = Math.round(start * this.audio.sample_rate); const b = Math.round(end * this.audio.sample_rate);
      if (a >= b || b > this.audio.frames) throw new RangeError('Invalid loop');
      next = [a, b];
    }
    const resume = this.status === 'playing' || this.status === 'buffering';
    this.updatePosition(); this.reset(); this.loop = next;
    if (next && (this.position * this.audio.sample_rate < next[0] || this.position * this.audio.sample_rate >= next[1])) this.position = next[0] / this.audio.sample_rate;
    this.status = 'paused'; this.emit(); if (resume) void this.play();
  }
  async play(): Promise<void> {
    if (this.disposed || this.status === 'playing' || this.status === 'buffering') return;
    if (this.position >= this.duration) this.position = 0;
    this.error = null; this.status = 'buffering';
    this.emit();
    const generation = this.generation;
    try { await this.context.resume(); } catch (reason) {
      if (generation !== this.generation) return;
      this.status = 'error'; this.error = reason instanceof Error ? reason : new Error(String(reason)); this.emit(); return;
    }
    if (generation !== this.generation || this.disposed) return;
    this.tick(); this.timer = setInterval(() => this.tick(), 50);
  }
  pause(): void { this.updatePosition(); this.reset(); this.status = 'paused'; this.emit(); }
  setClicks(events: readonly ClickEvent[], levels: ClickLevels): void {
    renderClickTrack(events, 0, 0, this.audio.sample_rate, levels);
    const snapshot = events.map(event => ({ ...event }));
    const levelSnapshot = { ...levels };
    this.pause();
    this.clicks = snapshot;
    this.clickLevels = levelSnapshot;
  }
  seek(seconds: number): void {
    const resume = this.status === 'playing' || this.status === 'buffering';
    this.reset(); this.position = Math.max(0, Math.min(this.duration, Number.isFinite(seconds) ? seconds : 0));
    if (this.loop && (this.position * this.audio.sample_rate < this.loop[0] || this.position * this.audio.sample_rate >= this.loop[1])) this.position = this.loop[0] / this.audio.sample_rate;
    this.status = 'paused'; this.emit(); if (resume) void this.play();
  }
  setRate(rate: number): void {
    if (!Number.isFinite(rate) || rate < 0.5 || rate > 2) throw new RangeError('Rate must be between 0.5 and 2');
    const resume = this.status === 'playing' || this.status === 'buffering';
    this.updatePosition(); this.reset(); this.rate = rate; this.status = 'paused'; this.emit(); if (resume) void this.play();
  }
  dispose(): void { this.reset(); this.cache.clear(); this.disposed = true; if (this.ownsContext) void this.context.close(); }
}
