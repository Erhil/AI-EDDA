import { ApiError } from './api';
import type { Diagnostic, ProjectView } from './generated/ui-contract';

export type ValidationResponse = { view: ProjectView; diagnostics: (Diagnostic & { chart_id: string })[] };

async function readResponse(response: Response, signal?: AbortSignal): Promise<ValidationResponse> {
  if (!response.ok) {
    let detail: unknown;
    try { detail = (await response.json()).detail; } catch (error) {
      if (signal?.aborted || (error instanceof Error && error.name === 'AbortError')) throw error;
      detail = response.statusText;
    }
    throw new ApiError(response.status, detail);
  }
  return response.json() as Promise<ValidationResponse>;
}

export async function getValidation(projectId: string, signal?: AbortSignal): Promise<ValidationResponse> {
  const response = await fetch(`/api/projects/${encodeURIComponent(projectId)}/validation`, { credentials: 'same-origin', signal });
  return readResponse(response, signal);
}

export async function previewValidation(projectId: string, kind: string, payload: unknown, signal?: AbortSignal): Promise<ValidationResponse> {
  const response = await fetch(`/api/projects/${encodeURIComponent(projectId)}/preview-validation`, {
    method: 'POST', credentials: 'same-origin', signal,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ kind, payload }),
  });
  return readResponse(response, signal);
}
