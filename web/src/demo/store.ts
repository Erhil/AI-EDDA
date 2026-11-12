import type { ObjectRef, ProjectView } from '../generated/ui-contract';
import { createFixtures } from './fixtures';

export const DEMO_STORAGE_KEY = 'ragnarock-demo-v1';
export interface DemoState {
  projects: ProjectView[];
  receipts: Record<string, unknown>;
  analysisRefs: Record<string, ObjectRef[]>;
  draftDecisions: Record<string, string>;
}
export interface StorageLike { getItem(key: string): string | null; setItem(key: string, value: string): void; removeItem(key: string): void }
export class DemoStorageError extends Error {
  constructor(public readonly reason: 'corrupt' | 'write', message: string) { super(message); this.name = 'DemoStorageError'; }
}
const copy = <T>(value: T): T => JSON.parse(JSON.stringify(value)) as T;
function seed(): DemoState {
  const projects = createFixtures();
  return { projects, receipts: {}, analysisRefs: Object.fromEntries(projects.map(({ project }) => [project.id!, project.analysis ? [project.analysis] : []])), draftDecisions: {} };
}
const object = (x: unknown): x is Record<string, unknown> => x !== null && typeof x === 'object' && !Array.isArray(x);
const finite = (x: unknown): x is number => typeof x === 'number' && Number.isFinite(x);
const text = (x: unknown): x is string => typeof x === 'string' && x.length > 0;
const string = (x: unknown): x is string => typeof x === 'string';
const optional = (o: Record<string, unknown>, key: string, check: (x: unknown) => boolean) => o[key] === undefined || check(o[key]);
function validRef(x: unknown): boolean { return object(x) && text(x.id) && Number.isInteger(x.revision) && (x.revision as number) > 0; }
function validProvenance(x: unknown): boolean { return x === undefined || object(x); }
function validProjectView(x: unknown): boolean {
  if (!object(x) || !object(x.project) || !object(x.grid) || !Array.isArray(x.charts)) return false;
  const p = x.project, g = x.grid;
  if (!text(p.id) || !Number.isInteger(p.revision) || (p.revision as number) < 1 || !object(p.metadata) ||
      !optional(p, 'grid_revision', n => Number.isInteger(n) && (n as number) >= 1) || !validProvenance(p.provenance)) return false;
  if (!optional(p.metadata, 'title', string) || !optional(p.metadata, 'artist', string) || !optional(p.metadata, 'mapper', string) ||
      !optional(p.metadata, 'subtitle', y => typeof y === 'string') || !optional(p.metadata, 'explicit', y => typeof y === 'boolean')) return false;
  if (p.audio !== undefined && p.audio !== null && (!object(p.audio) || !text(p.audio.path) || !text(p.audio.sha256) ||
      !finite(p.audio.duration_sec) || p.audio.duration_sec <= 0 || !finite(p.audio.sample_rate) || p.audio.sample_rate <= 0 ||
      !finite(p.audio.channels) || p.audio.channels <= 0 || !finite(p.audio.frames) || p.audio.frames <= 0 || !text(p.audio.subtype))) return false;
  if (p.cover !== undefined && p.cover !== null && (!object(p.cover) || !text(p.cover.path) || !text(p.cover.sha256) || !finite(p.cover.width) || p.cover.width <= 0 || !finite(p.cover.height) || p.cover.height <= 0)) return false;
  if (!optional(p, 'analysis', y => y === null || validRef(y)) || !optional(p, 'plan_inputs', y => y === null || validRef(y)) ||
      !optional(p, 'chart_heads', y => Array.isArray(y) && y.every(h => object(h) && text(h.id) && Number.isInteger(h.revision) && (h.revision as number) > 0))) return false;
  if (!Number.isInteger(g.revision) || (g.revision as number) < 1 || !Array.isArray(g.segments) || g.segments.length === 0 || !validProvenance(p.provenance)) return false;
  if (!g.segments.every(s => object(s) && finite(s.start_time_sec) && s.start_time_sec >= 0 && finite(s.start_beat_q) && finite(s.bpm_quarter) && s.bpm_quarter > 0 && Number.isInteger(s.grid_division) && (s.grid_division as number) > 0)) return false;
  if (!optional(g, 'meter', y => object(y) && Number.isInteger(y.numerator) && (y.numerator as number) > 0 && Number.isInteger(y.denominator) && (y.denominator as number) > 0)) return false;
  if (!optional(g, 'anchors', y => Array.isArray(y) && y.every(a => object(a) && finite(a.beat_q) && finite(a.time_sec) && a.time_sec >= 0))) return false;
  return x.charts.every(c => object(c) && text(c.id) && Number.isInteger(c.revision) && (c.revision as number) > 0 &&
    Number.isInteger(c.grid_revision) && (c.grid_revision as number) > 0 && (c.metadata === undefined || object(c.metadata)) &&
    (!object(c.metadata) || optional(c.metadata, 'name', string) && optional(c.metadata, 'rank', y => y === null || finite(y)) && optional(c.metadata, 'speed', y => y === null || finite(y))) &&
    validProvenance(c.provenance) && (c.events === undefined || Array.isArray(c.events) && c.events.every(e => object(e) && text(e.id) &&
      validProvenance(e.provenance) && object(e.time_ref) && (e.time_ref.kind === 'beat' ? finite(e.time_ref.beat_q) && optional(e.time_ref, 'micro_offset_sec', finite) : e.time_ref.kind === 'audio' && finite(e.time_ref.time_sec)) &&
      (e.notes === undefined || Array.isArray(e.notes) && e.notes.every(n => object(n) && text(n.id) && Number.isInteger(n.lane) && (n.lane as number) >= 0 && validProvenance(n.provenance))))));
}
function valid(value: unknown): value is DemoState {
  if (!object(value)) return false;
  const v = value as unknown as DemoState;
  return Array.isArray(v.projects) && v.projects.every(validProjectView) && object(v.receipts) && object(v.analysisRefs) &&
    Object.values(v.analysisRefs).every(refs => Array.isArray(refs) && refs.every(validRef)) && object(v.draftDecisions) &&
    Object.values(v.draftDecisions).every(text);
}
/** Single-tab state. Updates are committed to storage before they become visible. */
export function createDemoStore(storage: StorageLike) {
  let state: DemoState;
  const raw = storage.getItem(DEMO_STORAGE_KEY);
  if (raw === null) state = seed();
  else {
    try { const parsed: unknown = JSON.parse(raw); if (!valid(parsed)) throw new Error(); state = parsed; }
    catch { throw new DemoStorageError('corrupt', 'Demo data is invalid. Reset demo storage to recover.'); }
  }
  return {
    read(): DemoState { return copy(state); },
    update(fn: (draft: DemoState) => void): void {
      const next = copy(state); fn(next);
      try { storage.setItem(DEMO_STORAGE_KEY, JSON.stringify(next)); }
      catch { throw new DemoStorageError('write', 'Could not save demo data.'); }
      state = next;
    },
    reset(): void {
      try { storage.removeItem(DEMO_STORAGE_KEY); }
      catch { throw new DemoStorageError('write', 'Could not reset demo data.'); }
      state = seed();
    },
  };
}
/** Remove corrupt demo data before creating a new store. */
export function recoverDemoStorage(storage: StorageLike): void { storage.removeItem(DEMO_STORAGE_KEY); }
