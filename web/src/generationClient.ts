import { ApiError } from './api';
import type { WriterHeaders } from './uploads';
import type { ArrangementRequest, Chart, ChartDraft, GeneratorCapabilities, MusicalPlan, ObjectRef, TimingGrid } from './generated/ui-contract';

type PlanResponse = { project_id: string; project_revision: number; grid_revision: number; analysis: ObjectRef | null; plan_inputs: ObjectRef | null; plan: MusicalPlan };
type DraftInspection = { draft: ChartDraft; base_chart: Chart; grid: TimingGrid; applicable: boolean; rejected: boolean; reasons: string[] };
type Rejection = { draft_id: string; decision_id: string; draft_sha256: string };

async function request<T>(path: string, signal?: AbortSignal, body?: unknown, headers?: WriterHeaders): Promise<T> {
  const response = await fetch(path, {
    method: body === undefined ? 'GET' : 'POST', credentials: 'same-origin', signal,
    headers: body === undefined ? undefined : { ...headers, 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
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
const projectPath = (projectId: string) => `/api/projects/${encodeURIComponent(projectId)}`;
export const capabilities = (signal?: AbortSignal) => request<GeneratorCapabilities>('/api/generation-capabilities', signal);
export const getPlan = (projectId: string, signal?: AbortSignal) => request<PlanResponse>(`${projectPath(projectId)}/musical-plan`, signal);
export const startGeneration = (projectId: string, arrangement: Readonly<ArrangementRequest>, headers: WriterHeaders, signal?: AbortSignal) => request<{ job_id: string; draft_id: string }>(`${projectPath(projectId)}/generation-jobs`, signal, arrangement, headers);
export const inspectDraft = (projectId: string, draftId: string, signal?: AbortSignal) => request<DraftInspection>(`${projectPath(projectId)}/drafts/${encodeURIComponent(draftId)}`, signal);
export const rejectDraft = (projectId: string, draftId: string, decisionId: string, headers: WriterHeaders, signal?: AbortSignal) => request<Rejection>(`${projectPath(projectId)}/drafts/${encodeURIComponent(draftId)}/reject`, signal, { decision_id: decisionId }, headers);
