import { useEffect, useRef, useState } from 'react';
import WaveSurfer from 'wavesurfer.js';
import type { AudioAsset } from './generated/ui-contract';
import { loadWaveformPeaks } from './waveformClient';
import './waveform.css';

interface Props {
  projectId: string;
  audio: AudioAsset;
  viewport: { start: number; span: number };
  position: number;
  loop?: [number, number];
  onSeek: (time: number) => void;
}

export function WaveformView({ projectId, audio, viewport, position, loop, onSeek }: Props) {
  const container = useRef<HTMLDivElement>(null);
  const [state, setState] = useState<'loading' | 'ready' | 'error'>('loading');
  const [retry, setRetry] = useState(0);
  const { start, span } = viewport;

  useEffect(() => {
    const node = container.current;
    if (!node || !Number.isFinite(start) || start < 0 || !Number.isFinite(span) || span <= 0) return;
    const controller = new AbortController();
    let wave: WaveSurfer | undefined;
    setState('loading');
    loadWaveformPeaks(projectId, audio, start, start + span, 4096, controller.signal).then(data => {
      if (controller.signal.aborted) return;
      wave = WaveSurfer.create({
        container: node, height: 96, interact: false, cursorWidth: 0,
        peaks: [new Float32Array([0, 0])], duration: span,
        renderFunction: (_peaks, ctx) => {
          const { width, height } = ctx.canvas;
          ctx.fillStyle = '#7aa2f7';
          data.peaks.forEach((channels, index) => {
            const binStart = (data.sourceStartSample + index * data.samplesPerPeak) / audio.sample_rate;
            const binEnd = Math.min((data.sourceStartSample + (index + 1) * data.samplesPerPeak) / audio.sample_rate, data.frames / audio.sample_rate);
            const x1 = Math.max(0, (binStart - start) / span * width);
            const x2 = Math.min(width, (binEnd - start) / span * width);
            if (x2 <= x1) return;
            const min = Math.min(...channels.map(pair => pair[0]));
            const max = Math.max(...channels.map(pair => pair[1]));
            ctx.fillRect(x1, (1 - max) * height / 2, Math.max(1, x2 - x1), Math.max(1, (max - min) * height / 2));
          });
        },
      });
      setState('ready');
    }).catch(() => { if (!controller.signal.aborted) setState('error'); });
    return () => { controller.abort(); wave?.destroy(); };
  }, [projectId, audio.sha256, audio.sample_rate, audio.channels, audio.frames, start, span, retry]);

  const fraction = (time: number) => (time - start) / span;
  const loopStart = loop ? Math.max(0, fraction(loop[0])) : 0;
  const loopEnd = loop ? Math.min(1, fraction(loop[1])) : 0;
  const cursor = fraction(position);
  return <div className="waveform-view">
    <div className="waveform-surface" onClick={event => {
      const rect = event.currentTarget.getBoundingClientRect();
      if (rect.width > 0 && span > 0) onSeek(Math.max(0, Math.min(audio.frames / audio.sample_rate, start + (event.clientX - rect.left) / rect.width * span)));
    }}>
      <div ref={container} />
      {loop && loopEnd > loopStart && <div className="waveform-loop" style={{ left: `${loopStart * 100}%`, width: `${(loopEnd - loopStart) * 100}%` }} />}
      {cursor >= 0 && cursor <= 1 && <div className="waveform-position" style={{ left: `${cursor * 100}%` }} />}
    </div>
    {state === 'loading' && <p>Loading waveform…</p>}
    {state === 'error' && <p>Could not load waveform. <button type="button" onClick={() => setRetry(value => value + 1)}>Retry</button></p>}
  </div>;
}
