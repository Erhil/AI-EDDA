import { ApiError } from './api';
import type { WriterHeaders } from './uploads';
export type ExportRequest = { request_id: string; kind: 'native' | 'game'; project_revision: number; chart_ids: string[]; folder_name: string | null };
export type ExportJob = { state: string; error?: unknown; coordinator_error?: unknown; progress?: unknown; publication_result?: unknown };
export type ExportMetadata = { project_id: string; export_id: string; kind: string; project_revision: number; download_url: string; server_folder: string | null; site_check?: { passed: boolean; errors: unknown[] } };
async function request<T>(path: string, signal: AbortSignal, method = 'GET', body?: unknown, headers?: WriterHeaders): Promise<T> {
 const response = await fetch(path, { method, signal, credentials: 'same-origin', headers: { ...(body === undefined ? {} : { 'Content-Type': 'application/json' }), ...headers }, body: body === undefined ? undefined : JSON.stringify(body) });
 if (!response.ok) { let detail: unknown; try { detail = (await response.json()).detail; } catch (e) { if (signal.aborted) throw e; detail = response.statusText; } throw new ApiError(response.status, detail); }
 return response.json() as Promise<T>;
}
const root = (id: string) => `/api/projects/${encodeURIComponent(id)}/exports`;
export const startExport = (id: string, body: ExportRequest, headers: WriterHeaders, signal: AbortSignal) => request<{job_id: string; export_id: string}>(root(id), signal, 'POST', body, headers);
export const exportJob = (id: string, signal: AbortSignal) => request<ExportJob>(`/api/jobs/${encodeURIComponent(id)}`, signal);
export const cancelExport = (id: string, headers: WriterHeaders, signal: AbortSignal) => request<unknown>(`/api/jobs/${encodeURIComponent(id)}/cancel`, signal, 'POST', undefined, headers);
export async function exportMetadata(project: string, id: string, signal: AbortSignal): Promise<ExportMetadata> { const path = `${root(project)}/${encodeURIComponent(id)}`; const result = await request<ExportMetadata>(path, signal); if (result.project_id !== project || result.export_id !== id || result.download_url !== `${path}/download`) throw new Error('Некорректные данные экспорта'); return result; }
