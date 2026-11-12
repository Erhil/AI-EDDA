import { ApiError } from './api';
import type { WriterHeaders } from './uploads';
export type JobRow = { id: string; kind: string; state: string; label: string; project_id: string | null; progress?: unknown; error?: unknown; coordinator_error?: unknown };
export type JobDetail = JobRow & { context?: Record<string, unknown>; result?: Record<string, unknown>; publication_result?: Record<string, unknown> };
async function request<T>(path: string, signal: AbortSignal, method = 'GET', headers?: WriterHeaders): Promise<T> {
 const response = await fetch(path, { method, signal, headers, credentials: 'same-origin' });
 if (!response.ok) { let detail: unknown; try { detail = (await response.json()).detail; } catch (e) { if (signal.aborted) throw e; detail = response.statusText; } throw new ApiError(response.status, detail); }
 return response.json() as Promise<T>;
}
export const listJobs = (query: URLSearchParams, signal: AbortSignal) => request<{items: JobRow[]; next_cursor: string | null}>(`/api/jobs?${query}`, signal);
export const getJob = (id: string, signal: AbortSignal) => request<JobDetail>(`/api/jobs/${encodeURIComponent(id)}`, signal);
export const cancelJob = (id: string, signal: AbortSignal, headers?: WriterHeaders) => request<boolean>(`/api/jobs/${encodeURIComponent(id)}/cancel`, signal, 'POST', headers);
