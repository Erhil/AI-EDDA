import { forwardRef, useEffect, useImperativeHandle, useMemo, useRef, useState } from 'react';
import type { AudioAsset, Chart, TimingGrid } from './generated/ui-contract';
import { loadPcmFragment } from './audioClient';
import { Transport, type TransportState } from './transport';
import { transportEvents } from './transportEvents';

export interface AudioTransportHandle { pause(): void; toggle(): void; seek(seconds: number): void }
export default forwardRef<AudioTransportHandle, { projectId: string; audio: AudioAsset; grid: TimingGrid; chart: Chart; onPositionChange?: (position: number) => void; onLoopChange?: (loop: [number, number] | null) => void }>(function AudioTransport({ projectId, audio, grid, chart, onPositionChange, onLoopChange }, ref) {
  const transport = useRef<Transport | null>(null);
  const [state, setState] = useState<TransportState>({ position: 0, status: 'paused', error: null });
  const [rate, setRate] = useState(1);
  const [a, setA] = useState('0'); const [b, setB] = useState(''); const [loop, setLoop] = useState<[number, number] | null>(null);
  const [error, setError] = useState('');
  const [metro, setMetro] = useState(false); const [hits, setHits] = useState(false);
  const [metroLevel, setMetroLevel] = useState(0.5); const [hitsLevel, setHitsLevel] = useState(0.5);
  useEffect(() => { onPositionChange?.(state.position); }, [state.position, onPositionChange]);
  useEffect(() => { onLoopChange?.(loop); }, [loop, onLoopChange]);
  const duration = audio.frames / audio.sample_rate;
  const derived = useMemo(() => { try { return { events: transportEvents(grid, chart, duration), error: '' }; } catch (e) { return { events: [], error: e instanceof Error ? e.message : String(e) }; } }, [grid, chart, duration]);
  const levels = { metronome: metro ? metroLevel : 0, hits: hits ? hitsLevel : 0 };
  const get = () => {
    if (!transport.current) {
      const instance = new Transport(audio, (start, count, signal) => loadPcmFragment(projectId, audio, start, count, signal), next => { if (transport.current === instance) setState(next); });
      transport.current = instance; instance.setRate(rate); instance.setClicks(derived.events, levels); if (loop) instance.setLoop(...loop);
    }
    return transport.current;
  };
  const toggle = () => { if (state.status === 'playing' || state.status === 'buffering') transport.current?.pause(); else void get().play(); };
  useImperativeHandle(ref, () => ({ toggle, pause: () => transport.current?.pause(), seek: seconds => get().seek(seconds) }));
  useEffect(() => { transport.current?.setClicks(derived.events, { metronome: metro ? metroLevel : 0, hits: hits ? hitsLevel : 0 }); }, [derived, metro, hits, metroLevel, hitsLevel]);
  useEffect(() => () => { const old = transport.current; transport.current = null; old?.dispose(); }, []);
  const applyLoop = () => { const start = Number(a), end = Number(b); if (!a.trim() || !b.trim() || !Number.isFinite(start) || !Number.isFinite(end) || start < 0 || start >= end || end > duration || Math.round(start * audio.sample_rate) >= Math.round(end * audio.sample_rate)) { setError('Укажите границы A и B внутри аудио, где A меньше B.'); return; } try { transport.current?.setLoop(start, end); setLoop([start, end]); setError(''); } catch (e) { setError(e instanceof Error ? e.message : String(e)); } };
  return <section aria-label="Воспроизведение"><button onClick={toggle}>{state.status === 'playing' || state.status === 'buffering' ? 'Пауза' : 'Воспроизвести'}</button> <span>{state.position.toFixed(2)} / {duration.toFixed(2)} с</span> <input aria-label="Позиция" type="range" min={0} max={duration} step="0.01" value={state.position} onChange={e => get().seek(Number(e.target.value))}/><label>Скорость <select value={rate} onChange={e => { const value = Number(e.target.value); setRate(value); transport.current?.setRate(value); }}>{[0.5, 0.75, 1, 1.5, 2].map(value => <option key={value} value={value}>{value}×</option>)}</select></label><div><label>A, с <input type="number" step="any" value={a} onChange={e => setA(e.target.value)}/></label><label>B, с <input type="number" step="any" value={b} onChange={e => setB(e.target.value)}/></label><button onClick={applyLoop}>Включить повтор</button><button onClick={() => { transport.current?.setLoop(null); setLoop(null); setError(''); }}>Сбросить повтор</button>{loop && <span> Повтор включён</span>}</div><div><label><input type="checkbox" checked={metro} onChange={e => setMetro(e.target.checked)}/> Метроном</label><label>Громкость метронома <input type="range" min="0" max="1" step="0.05" value={metroLevel} onChange={e => setMetroLevel(Number(e.target.value))}/></label><label><input type="checkbox" checked={hits} onChange={e => setHits(e.target.checked)}/> Удары карты</label><label>Громкость ударов <input type="range" min="0" max="1" step="0.05" value={hitsLevel} onChange={e => setHitsLevel(Number(e.target.value))}/></label></div>{(error || derived.error) && <p role="alert">{error || derived.error}</p>}{state.status === 'buffering' && <span> Загрузка…</span>}{state.error && <p role="alert">Ошибка воспроизведения: {state.error.message}</p>}</section>;
});
