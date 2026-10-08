export type MemoryKind = 'decision' | 'convention' | 'lesson' | 'working_style';
export type MemoryStatus = 'suggested' | 'active' | 'archived' | 'rejected';
export type MemoryScope = 'project' | 'org' | 'user';

export interface MemorySource { type?: string; phase?: number; stage?: string; by?: string }
export interface MemoryEntry {
  id: string; scope: MemoryScope; projectId: string | null; kind: MemoryKind; title: string; body: string;
  stage: number | null; status: MemoryStatus; source: MemorySource; uses: number; lastUsedAt: string | null;
  createdBy: string | null; createdAt: string; updatedAt: string;
}

export const KIND_LABEL: Record<MemoryKind, string> = {
  decision: 'Decision', convention: 'Convention', lesson: 'Lesson', working_style: 'Working style',
};
export const KIND_HINT: Record<MemoryKind, string> = {
  decision: 'Something the team chose, such as a technology or a policy.',
  convention: 'A rule for how things are named or done here.',
  lesson: 'What a reviewer kept asking for, so the agent gets it right first time.',
  working_style: 'How you like to work. Only used when you run a stage.',
};
export const SCOPE_LABEL: Record<MemoryScope, string> = { project: 'This project', org: 'Whole organisation', user: 'Just me' };

/** Where a memory came from, in words a reviewer understands. */
export function sourceLabel(m: Pick<MemoryEntry, 'source'>): string {
  const s = m.source ?? {};
  const where = s.stage ? ` in ${s.stage}` : s.phase ? ` in stage ${s.phase}` : '';
  switch (s.type) {
    case 'clarification': return `From an answer to a clarifying question${where}`;
    case 'change_request': return `From a change request${where}`;
    case 'repeated_instruction': return `From a standing preference in your instructions${where}`;
    case 'manual': return s.by ? `Added by ${s.by}` : 'Added by hand';
    default: return 'Added by hand';
  }
}

export interface MemoryFilter { query?: string; kind?: MemoryKind | 'all' }

export function filterMemories(list: MemoryEntry[], f: MemoryFilter): MemoryEntry[] {
  const q = (f.query ?? '').trim().toLowerCase();
  return list.filter((m) => (!f.kind || f.kind === 'all' || m.kind === f.kind)
    && (!q || `${m.title} ${m.body}`.toLowerCase().includes(q)));
}

export function groupMemories(list: MemoryEntry[]) {
  return {
    suggested: list.filter((m) => m.status === 'suggested'),
    active: list.filter((m) => m.status === 'active'),
    archived: list.filter((m) => m.status === 'archived'),
  };
}
