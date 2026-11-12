import { ApiError } from './api';
import type { AudioAsset } from './generated/ui-contract';

export type WriterHeaders = Record<string, string>;
export type Job = {
  state: string;
  kind?: string;
  context?: { project_id?: string };
  error?: unknown;
  publication_result?: { asset_id: string; audio: AudioAsset };
};

export function uploadAudio(projectId: string, requestId: string, file: File, headers: WriterHeaders,
  onProgress: (loaded: number, total: number) => void, signal: AbortSignal): Promise<{ job_id: string; upload_required: boolean }> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    const abort = () => xhr.abort();
    const cleanup = () => signal.removeEventListener('abort', abort);
    if (signal.aborted) { reject(new DOMException('Aborted', 'AbortError')); return; }
    xhr.open('POST', `/api/projects/${encodeURIComponent(projectId)}/audio-imports`);
    xhr.setRequestHeader('Content-Type', 'application/octet-stream');
    xhr.setRequestHeader('X-Request-Id', requestId);
    xhr.setRequestHeader('X-File-Name', encodeURIComponent(file.name));
    xhr.setRequestHeader('X-File-Size', String(file.size));
    for (const [key, value] of Object.entries(headers)) xhr.setRequestHeader(key, value);
    xhr.upload.onprogress = event => onProgress(event.loaded, event.total);
    xhr.onload = () => {
      cleanup();
      if (xhr.status < 200 || xhr.status >= 300) {
        let detail: unknown;
        try { detail = JSON.parse(xhr.responseText).detail; } catch { detail = xhr.statusText; }
        reject(new ApiError(xhr.status, detail)); return;
      }
      try { resolve(JSON.parse(xhr.responseText)); } catch (error) { reject(error); }
    };
    xhr.onerror = () => { cleanup(); reject(new TypeError('Network request failed')); };
    xhr.onabort = () => { cleanup(); reject(new DOMException('Aborted', 'AbortError')); };
    signal.addEventListener('abort', abort, { once: true });
    try { xhr.send(file); } catch (error) { cleanup(); reject(error); }
  });
}

async function jobRequest<T>(path: string, method: string, headers?: WriterHeaders): Promise<T> {
  const response = await fetch(`/api${path}`, { method, headers, credentials: 'same-origin' });
  if (!response.ok) {
    let detail: unknown;
    try { detail = (await response.json()).detail; } catch { detail = response.statusText; }
    throw new ApiError(response.status, detail);
  }
  return response.json() as Promise<T>;
}
export const getJob = (jobId: string) => jobRequest<Job>(`/jobs/${encodeURIComponent(jobId)}`, 'GET');
export const cancelJob = (jobId: string, headers: WriterHeaders) => jobRequest<boolean>(`/jobs/${encodeURIComponent(jobId)}/cancel`, 'POST', headers);
