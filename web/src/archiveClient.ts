import { ApiError } from './api';

export type ArchiveKind = 'native' | 'game';
export type ImportResult = { project_id: string; revision: number; archive_kind: ArchiveKind };
export type ImportJob = { state: string; progress?: unknown; error?: unknown; publication_result?: ImportResult; coordinator_error?: unknown };

export function uploadProjectArchive(kind: ArchiveKind, requestId: string, file: File,
  onProgress: (loaded: number, total: number) => void, signal: AbortSignal): Promise<{ job_id: string; upload_required: true; verifying: boolean }> {
  return new Promise((resolve, reject) => {
    if (signal.aborted) { reject(new DOMException('Aborted', 'AbortError')); return; }
    const xhr = new XMLHttpRequest();
    const abort = () => xhr.abort();
    const cleanup = () => signal.removeEventListener('abort', abort);
    xhr.open('POST', '/api/projects/imports');
    xhr.setRequestHeader('Content-Type', 'application/octet-stream');
    xhr.setRequestHeader('X-Request-Id', requestId);
    xhr.setRequestHeader('X-Archive-Kind', kind);
    xhr.setRequestHeader('X-File-Name', encodeURIComponent(file.name));
    xhr.setRequestHeader('X-File-Size', String(file.size));
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

async function jobRequest<T>(path: string, method: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(path, { method, credentials: 'same-origin', signal });
  if (!response.ok) {
    let detail: unknown;
    try { detail = (await response.json()).detail; } catch (error) {
      if (signal?.aborted || (error instanceof Error && error.name === 'AbortError')) throw error;
      detail = response.statusText;
    }
    throw new ApiError(response.status, detail);
  }
  return response.json() as Promise<T>;
}
export const getImportJob = (jobId: string, signal?: AbortSignal) => jobRequest<ImportJob>(`/api/jobs/${encodeURIComponent(jobId)}`, 'GET', signal);
export const cancelImportJob = (jobId: string, signal?: AbortSignal) => jobRequest<boolean>(`/api/jobs/${encodeURIComponent(jobId)}/cancel`, 'POST', signal);
