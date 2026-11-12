import { ApiError } from './api';
export type Kind = 'analysis' | 'draft';
export type Item = { id: string; error?: string; audio_asset_id?: string; audio_sha256?: string; revision?: number; chart_id?: string; base_chart_revision?: number; generator_id?: string; seed?: number; event_count?: number; rejected?: boolean };
export type Catalog = { kind: Kind; items: Item[]; next_cursor: string | null };
export async function getCatalog(project: string, kind: Kind, after: string | null, signal: AbortSignal): Promise<Catalog> {
  const query = new URLSearchParams({ kind, limit: '20' }); if (after) query.set('after', after);
  const response = await fetch(`/api/projects/${encodeURIComponent(project)}/artifacts?${query}`, { credentials: 'same-origin', signal });
  if (!response.ok) { let detail: unknown; try { detail = (await response.json()).detail; } catch (error) { if (signal.aborted || (error instanceof Error && error.name === 'AbortError')) throw error; detail = response.statusText; } throw new ApiError(response.status, detail); }
  return response.json() as Promise<Catalog>;
}
