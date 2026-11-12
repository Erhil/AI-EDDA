import { useCallback, useEffect, useRef, useState } from 'react';
import { api, ApiError, commandEnvelope } from './api';
import type { CommandEnvelope, ProjectView } from './generated/ui-contract';

type Status = 'saved' | 'saving' | 'error' | 'readonly';
type Pending = { envelope: CommandEnvelope; projectId: string; phase: 'sending' | 'ambiguous' };

export function useProject(onTransportStop?: () => void) {
  const [view, setView] = useState<ProjectView | null>(null);
  const [selectedChart, setSelectedChart] = useState<string | null>(null);
  const [status, setStatus] = useState<Status>('readonly');
  const [canWrite, setCanWrite] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const current = useRef<ProjectView | null>(null);
  const project = useRef<string | null>(null);
  const generation = useRef(0);
  const intentEpoch = useRef(0);
  const refreshingConflict = useRef(false);
  const lease = useRef<{ id: string; expires: number } | null>(null);
  const pending = useRef<Pending | null>(null);
  const [canRetry, setCanRetry] = useState(false);
  const refreshNeeded = useRef(false);
  const queue = useRef<Promise<unknown>>(Promise.resolve());
  const tab = useRef(crypto.randomUUID());
  const stop = useRef(onTransportStop);
  stop.current = onTransportStop;

  const acquire = useCallback(async (action: 'acquire' | 'heartbeat' | 'takeover') => {
    const id = project.current, gen = generation.current;
    if (!id) return;
    try {
      const result = await api.lease(id, tab.current, action, lease.current?.id);
      if (generation.current !== gen) return;
      lease.current = { id: result.lease_id, expires: Date.now() + result.ttl * 1000 };
      setCanWrite(true);
    } catch (e) {
      if (generation.current !== gen) return;
      lease.current = null; setCanWrite(false); setError(e);
    }
  }, []);

  const open = useCallback(async (id: string) => {
    const gen = ++generation.current;
    intentEpoch.current++; refreshingConflict.current = false;
    project.current = id; current.current = null; lease.current = null; setCanWrite(false); pending.current = null; refreshNeeded.current = false; setCanRetry(false);
    queue.current = Promise.resolve(); setView(null); setSelectedChart(null); setStatus('readonly'); setError(null); stop.current?.();
    try {
      const result = await api.open(id);
      if (generation.current !== gen) return;
      current.current = result; setView(result); setStatus('saved');
      await acquire('acquire');
    } catch (e) { if (generation.current === gen) { setError(e); setStatus('error'); } }
  }, [acquire]);

  useEffect(() => {
    const timer = window.setInterval(() => {
      if (!lease.current) return;
      if (Date.now() >= lease.current.expires) { lease.current = null; setCanWrite(false); return; }
      void acquire('heartbeat');
    }, 10000);
    return () => { window.clearInterval(timer); generation.current++; lease.current = null; setCanWrite(false); };
  }, [acquire]);

  const send = useCallback(async (item: Pending, gen: number) => {
    if (generation.current !== gen || project.current !== item.projectId || pending.current !== item) return;
    if (!lease.current || Date.now() >= lease.current.expires) {
      lease.current = null; setCanWrite(false);
      if (item.phase === 'sending') { pending.current = null; setCanRetry(false); }
      return;
    }
    stop.current?.();
    setStatus('saving'); setError(null);
    try {
      const result = await api.command(item.projectId, item.envelope, tab.current, lease.current.id);
      if (generation.current !== gen) return;
      current.current = result; setView(result); pending.current = null; refreshNeeded.current = true; setCanRetry(false); setStatus('saved');
      try {
        const latest = await api.open(item.projectId);
        if (generation.current !== gen || project.current !== item.projectId) return;
        stop.current?.(); current.current = latest; setView(latest); refreshNeeded.current = false;
      } catch (e) {
        if (generation.current === gen && project.current === item.projectId) { refreshNeeded.current = true; setCanRetry(true); setError(e); setStatus('error'); }
      }
    } catch (e) {
      if (generation.current !== gen) return;
      setError(e);
      if (e instanceof ApiError && e.status === 409 && e.detail === 'Writer lease required') {
        lease.current = null; setCanWrite(false);
      }
      if (e instanceof ApiError && JSON.stringify(e.detail).includes('STALE_REVISION')) {
        intentEpoch.current++; refreshingConflict.current = true;
        pending.current = null; setCanRetry(false); queue.current = Promise.resolve();
        try { const latest = await api.open(item.projectId); if (generation.current === gen && project.current === item.projectId) { current.current = latest; setView(latest); refreshNeeded.current = false; setCanRetry(false); } } catch { if (generation.current === gen && project.current === item.projectId) { refreshNeeded.current = true; setCanRetry(true); } }
        finally { if (generation.current === gen) refreshingConflict.current = false; }
      } else if (e instanceof ApiError && e.status >= 400 && e.status < 500) {
        pending.current = null; setCanRetry(false);
      } else {
        item.phase = 'ambiguous'; setCanRetry(true);
      }
      if (generation.current === gen) setStatus('error');
    }
  }, []);

  const mutate = useCallback((kind: string, payload: Record<string, unknown>, base?: ProjectView) => {
    const envelope = base ? commandEnvelope(base, crypto.randomUUID(), kind, payload) : null
    const gen = generation.current, epoch = intentEpoch.current, id = project.current;
    if (refreshingConflict.current) return queue.current;
    const work = async () => {
      if (!id || gen !== generation.current || epoch !== intentEpoch.current || refreshingConflict.current || refreshNeeded.current || !current.current || pending.current) return;
      if (!lease.current || Date.now() >= lease.current.expires) { lease.current = null; setCanWrite(false); return; }
      const item: Pending = { projectId: id, envelope: envelope ?? commandEnvelope(current.current, crypto.randomUUID(), kind, payload), phase: 'sending' };
      pending.current = item; setCanRetry(false); await send(item, gen);
    };
    queue.current = queue.current.then(work, work); return queue.current;
  }, [send]);
  const retry = useCallback(() => {
    const item = pending.current, gen = generation.current;
    if (!item && refreshNeeded.current) {
      const id = project.current, refreshGen = generation.current;
      if (!id) return Promise.resolve();
      const work = async () => {
        if (generation.current !== refreshGen || project.current !== id || !refreshNeeded.current) return;
        try {
          const latest = await api.open(id);
          if (generation.current === refreshGen && project.current === id) { current.current = latest; setView(latest); refreshNeeded.current = false; setCanRetry(false); setError(null); setStatus('saved'); }
        } catch (e) { if (generation.current === refreshGen && project.current === id) { refreshNeeded.current = true; setCanRetry(true); setError(e); setStatus('error'); } }
      };
      queue.current = queue.current.then(work, work); return queue.current;
    }
    if (refreshNeeded.current) return Promise.resolve();
    if (!item || item.phase !== 'ambiguous') return Promise.resolve();
    if (!lease.current || Date.now() >= lease.current.expires) { lease.current = null; setCanWrite(false); return Promise.resolve(); }
    const work = () => {
      if (!lease.current || Date.now() >= lease.current.expires) { lease.current = null; setCanWrite(false); setStatus('readonly'); return; }
      item.phase = 'sending'; setCanRetry(false); return send(item, gen);
    };
    queue.current = queue.current.then(work, work); return queue.current;
  }, [send]);
  const selectChart = useCallback((id: string | null) => { stop.current?.(); setSelectedChart(id); }, []);
  const preview = useCallback((kind: string, payload: Record<string, unknown>) => {
    if (!project.current) return Promise.reject(new Error('No project open'));
    return api.preview(project.current, kind, payload);
  }, []);
  const writerHeaders = useCallback(() => {
    if (!project.current || !lease.current || Date.now() >= lease.current.expires) return null;
    return { 'X-Tab-Id': tab.current, 'X-Writer-Lease': lease.current.id };
  }, []);
  return { writerHeaders, view, selectedChart, status, error, canWrite, canRetry, open, selectChart, mutate, retry, preview, takeover: () => acquire('takeover') };
}


