import { useQuery } from '@tanstack/react-query';
import { api } from '../api/client';
import type { ProjectFlow } from '../api/flow';
import type { Artefact, Project, ProjectDetail } from '../api/types';

/** The queries every workspace view shares: the project list, the open project, its artefacts and its flow. */
export function useWorkspaceData(activeProjectId: string | null) {
  const projects = useQuery({
    queryKey: ['projects'],
    queryFn: () => api.get<{ projects: Project[] }>('/api/projects'),
    refetchInterval: 10_000,
  });
  const detail = useQuery({
    queryKey: ['project', activeProjectId],
    queryFn: () => api.get<ProjectDetail>(`/api/projects/${activeProjectId}`),
    enabled: Boolean(activeProjectId),
    refetchInterval: 5_000,
  });
  const artefacts = useQuery({
    queryKey: ['artefacts', activeProjectId],
    queryFn: () => api.get<{ artefacts: Artefact[] }>(`/api/projects/${activeProjectId}/artefacts`),
    enabled: Boolean(activeProjectId),
    refetchInterval: 5_000,
  });
  const flow = useQuery({
    queryKey: ['flow', activeProjectId],
    queryFn: () => api.get<ProjectFlow>(`/api/projects/${activeProjectId}/flow`),
    enabled: Boolean(activeProjectId),
    refetchInterval: 5_000,
  });
  return { projects, detail, artefacts, flow };
}
