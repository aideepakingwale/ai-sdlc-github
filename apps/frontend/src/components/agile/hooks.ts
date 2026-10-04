import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { agileApi, type AgileOverview, type BacklogList } from '../../api/agile';

export function useOverview(projectId: string) {
  return useQuery({ queryKey: ['agile', projectId], queryFn: () => agileApi.overview(projectId), refetchInterval: 8_000, enabled: Boolean(projectId) });
}

export function useBacklog(projectId: string, iteration?: string) {
  return useQuery<BacklogList>({
    queryKey: ['agile-backlog', projectId, iteration ?? 'all'],
    queryFn: () => agileApi.backlog(projectId, iteration ? { iteration } : undefined),
    refetchInterval: 8_000,
  });
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
    },
  });
}

export const errorText = (e: unknown): string => (e instanceof Error ? e.message : 'Something went wrong');
export type { AgileOverview };
