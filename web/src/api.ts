import type { ProjectView, CommandEnvelope, SongProject, SongMetadata } from './generated/ui-contract';

export class ApiError extends Error {
  constructor(public status: number, public detail: unknown) {
    super(typeof detail === 'string' ? detail : `HTTP ${status}`);
  }
}

async function request<T>(path: string, method = 'GET', body?: unknown, headers?: Record<string, string>): Promise<T> {
  const response = await fetch(`/api${path}`, {
    method, credentials: 'same-origin',
    headers: { ...(body === undefined ? {} : { 'Content-Type': 'application/json' }), ...headers },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) {
    let detail: unknown;
    try { detail = (await response.json()).detail; } catch { detail = response.statusText; }
    throw new ApiError(response.status, detail);
  }
  return response.json() as Promise<T>;
}

const projectPath = (id: string) => `/projects/${encodeURIComponent(id)}`;
export const api = {
  login: (password: string) => request<{ authenticated: boolean }>('/session', 'POST', { password }),
  logout: () => request<{ authenticated: boolean }>('/session', 'DELETE'),
  list: () => request<SongProject[]>('/projects'),
  create: (request_id: string, metadata: SongMetadata) => request<ProjectView>('/projects', 'POST', { request_id, metadata }),
  open: (id: string) => request<ProjectView>(projectPath(id)),
  lease: (id: string, tab_id: string, action: 'acquire' | 'heartbeat' | 'takeover', lease_id?: string) =>
    request<{ lease_id: string; ttl: number; heartbeat_interval: number }>(`${projectPath(id)}/writer-lease`, 'POST', { tab_id, action, lease_id }),
  command: (id: string, command: CommandEnvelope, tabId: string, leaseId: string) =>
    request<ProjectView>(`${projectPath(id)}/commands`, 'POST', command, { 'X-Tab-Id': tabId, 'X-Writer-Lease': leaseId }),
  preview: (id: string, kind: string, payload: Record<string, unknown>) =>
    request<ProjectView>(`${projectPath(id)}/preview`, 'POST', { kind, payload }),
};

export function commandEnvelope(view: ProjectView, command_id: string, kind: string, payload: Record<string, unknown>): CommandEnvelope {
  return { command_id, kind, payload, base_project_revision: view.project.revision ?? 0,
    expected_grid_revision: view.grid.revision ?? 0,
    expected_chart_revisions: Object.fromEntries(view.charts.map(chart => [chart.id!, chart.revision ?? 0])) };
}
