import { ApiError } from './api';
import { getJob, type WriterHeaders } from './uploads';
import type { CoverAsset } from './generated/ui-contract';
export type CoverJob = { state: string; kind?: string; context?: { project_id?: string }; error?: unknown; publication_result?: { asset_id: string; cover: CoverAsset } };
export const getCoverJob = async (id: string) => await getJob(id) as unknown as CoverJob;
export function uploadCover(projectId: string, requestId: string, file: File, headers: WriterHeaders, progress: (a: number, b: number) => void, signal: AbortSignal): Promise<{ job_id: string }> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest(); const abort = () => xhr.abort(); const cleanup = () => signal.removeEventListener('abort', abort);
    if (signal.aborted) { reject(new DOMException('Aborted', 'AbortError')); return; }
    xhr.open('POST', `/api/projects/${encodeURIComponent(projectId)}/cover-imports`);
    xhr.setRequestHeader('Content-Type', 'application/octet-stream'); xhr.setRequestHeader('X-Request-Id', requestId); xhr.setRequestHeader('X-File-Name', encodeURIComponent(file.name)); xhr.setRequestHeader('X-File-Size', String(file.size));
    for (const [key, value] of Object.entries(headers)) xhr.setRequestHeader(key, value);
    xhr.upload.onprogress = e => progress(e.loaded, e.total);
    xhr.onload = () => { cleanup(); if (xhr.status < 200 || xhr.status >= 300) { let detail: unknown; try { detail = JSON.parse(xhr.responseText).detail; } catch { detail = xhr.statusText; } reject(new ApiError(xhr.status, detail)); return; } try { resolve(JSON.parse(xhr.responseText)); } catch (e) { reject(e); } };
    xhr.onerror = () => { cleanup(); reject(new TypeError('Network request failed')); }; xhr.onabort = () => { cleanup(); reject(new DOMException('Aborted', 'AbortError')); };
    signal.addEventListener('abort', abort, { once: true }); try { xhr.send(file); } catch (e) { cleanup(); reject(e); }
  });
}
