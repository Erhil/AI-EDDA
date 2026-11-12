import { ApiError } from './api';
import type { ObjectRef, TargetProfile } from './generated/ui-contract';

export type TargetResponse = { project_id: string; project_revision: number; chart_id: string; chart_revision: number; target_ref: ObjectRef | null; target: TargetProfile | null };
export async function getTarget(projectId: string, chartId: string, signal?: AbortSignal): Promise<TargetResponse> {
  const response = await fetch(`/api/projects/${encodeURIComponent(projectId)}/charts/${encodeURIComponent(chartId)}/target`, { credentials: 'same-origin', signal });
  if (!response.ok) {
    let detail: unknown;
    try { detail = (await response.json()).detail; } catch (error) {
      if (signal?.aborted || (error instanceof Error && error.name === 'AbortError')) throw error;
      detail = response.statusText;
    }
    throw new ApiError(response.status, detail);
  }
  return response.json() as Promise<TargetResponse>;
}
