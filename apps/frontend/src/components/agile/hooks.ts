import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { agileApi, type AgileOverview, type BacklogList } from '../../api/agile';

/** `release` = the release in focus (several run in parallel); omitted = the server picks the busiest one. */
export function useOverview(projectId: string, release?: string | null) {
  return useQuery({ queryKey: ['agile', projectId, release ?? 'auto'], queryFn: () => agileApi.overview(projectId, release ?? undefined), refetchInterval: 8_000, enabled: Boolean(projectId),
    placeholderData: (previous) => previous });   // switching release keeps the screen (no flash back to "Loading…")
}

export type BacklogScope = 'all' | 'pool' | 'release' | 'eligible';
export function useBacklog(projectId: string, iteration?: string, scope?: { release?: string | null; scope: BacklogScope }) {
  return useQuery<BacklogList>({
    queryKey: ['agile-backlog', projectId, iteration ?? 'all', scope?.scope ?? 'all', scope?.release ?? ''],
    queryFn: () => agileApi.backlog(projectId, {
      ...(iteration ? { iteration } : {}), ...(scope && scope.scope !== 'all' ? { scope: scope.scope, release: scope.release ?? undefined } : {}),
    }),
    refetchInterval: 8_000,
  });
}

/** Remember which release the person was looking at (a per-viewer convenience; works without storage). */
export function usePersistedRelease(projectId: string): [string | null, (id: string | null) => void] {
  const key = `devmind:release:${projectId}`;
  const read = (): string | null => { try { return window.localStorage.getItem(key); } catch { return null; } };
  const [id, setId] = useState<string | null>(read);
  const set = (v: string | null): void => {
    setId(v);
    try { if (v) window.localStorage.setItem(key, v); else window.localStorage.removeItem(key); } catch { /* storage unavailable */ }
  };
  return [id, set];
}

/** A mutation that refreshes every Agile view (and the pipeline) when it succeeds. */
export function useAgileMutation<A, R>(projectId: string, fn: (arg: A) => Promise<R>) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['agile', projectId] });
      void qc.invalidateQueries({ queryKey: ['agile-backlog', projectId] });
      void qc.invalidateQueries({ queryKey: ['flow', projectId] });
      void qc.invalidateQueries({ queryKey: ['agile-index', projectId] });
      void qc.invalidateQueries({ queryKey: ['agile-carry', projectId] });
      void qc.invalidateQueries({ queryKey: ['agile-release-epics', projectId] });
      void qc.invalidateQueries({ queryKey: ['agile-questions', projectId] });
    },
  });
}

export const errorText = (e: unknown): string => (e instanceof Error ? e.message : 'Something went wrong');
export type { AgileOverview };
