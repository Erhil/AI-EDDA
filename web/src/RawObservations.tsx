import { useEffect, useState } from 'react';
import type { ProjectView } from './generated/ui-contract';

type Data = { analysis_id: string; analysis_revision: number; audio_asset_id: string; audio_hash: string; start_sec: number; end_sec: number; beat_times: number[]; downbeat_times: number[]; onset_times: number[]; times: number[]; onset_envelope: number[]; rms: number[]; activity: number[] };
export default function RawObservations({ view, viewport }: { view: ProjectView; viewport: { start: number; span: number } }) {
  const [enabled, setEnabled] = useState(false);
  const [result, setResult] = useState<{ key: string; data: Data } | null>(null);
  const [error, setError] = useState('');
  const { id, analysis, audio } = view.project;
  const start = viewport.start, span = viewport.span, end = Math.min(start + span, audio?.duration_sec ?? 0);
  const asset = audio?.path.match(/^audio\/([^/]+)\/song\.ogg$/)?.[1];
  const key = JSON.stringify([id, analysis?.id, analysis?.revision, audio?.sha256, asset, start, span, end, enabled]);
  useEffect(() => {
    setResult(null); setError('');
    if (!enabled || !id || !analysis || !audio || !asset || !Number.isFinite(start) || !Number.isFinite(span) || start < 0 || span <= 0 || span > 60 || end <= start) return;
    const controller = new AbortController();
    const params = new URLSearchParams({ start_sec: String(start), end_sec: String(end) });
    fetch(`/api/projects/${encodeURIComponent(id)}/analysis/${encodeURIComponent(analysis.id)}/observations?${params}`, { credentials: 'same-origin', signal: controller.signal }).then(async response => { if (!response.ok) throw new Error(`HTTP ${response.status}`); return response.json() as Promise<Data>; }).then(data => {
      const names = ['beat_times', 'downbeat_times', 'onset_times', 'times', 'onset_envelope', 'rms', 'activity'] as const;
      if (data.analysis_id !== analysis.id || data.analysis_revision !== analysis.revision || data.audio_hash !== audio.sha256 || data.audio_asset_id !== asset || data.start_sec !== start || data.end_sec !== end || names.some(name => !Array.isArray(data[name]) || data[name].length > 4096 || data[name].some(n => typeof n !== 'number' || !Number.isFinite(n))) || [data.beat_times, data.downbeat_times, data.onset_times, data.times].some(values => values.some(n => n < start || n > end)) || [data.onset_envelope, data.rms, data.activity].some(values => values.length !== data.times.length)) throw new Error('Некорректный ответ анализа.');
      if (!controller.signal.aborted) setResult({ key, data });
    }).catch(e => { if (!controller.signal.aborted) setError(String(e)); });
    return () => controller.abort();
  }, [key]);
  const data = result?.key === key ? result.data : null;
  return <div><label><input type="checkbox" checked={enabled} onChange={e => setEnabled(e.target.checked)}/> Сырые наблюдения анализа</label>{enabled && <>{span > 60 ? <p>Увеличьте масштаб: видимый диапазон должен быть не больше 60 секунд.</p> : !analysis || !audio ? <p>Выберите анализ и аудио.</p> : error ? <p role="alert">{error}</p> : <><small>Сырые наблюдения: beats ●, downbeats ◆, onsets |. Это не ноты карты.</small><svg aria-label="Сырые наблюдения beats downbeats onsets" viewBox="0 0 1000 48" preserveAspectRatio="none" style={{ width: '100%', height: 48, display: 'block', background: '#222' }}>{data && <>{data.beat_times.map((t, i) => <circle key={`b${i}`} cx={(t-start)/span*1000} cy="10" r="3" fill="#65bfff"/>)}{data.downbeat_times.map((t, i) => <rect key={`d${i}`} x={(t-start)/span*1000-3} y="21" width="6" height="6" fill="#ffcd56" transform={`rotate(45 ${(t-start)/span*1000} 24)`}/>)}{data.onset_times.map((t, i) => <line key={`o${i}`} x1={(t-start)/span*1000} x2={(t-start)/span*1000} y1="34" y2="44" stroke="#ff8e8e" strokeWidth="2"/>)}</>}</svg></>}</>}</div>;
}
