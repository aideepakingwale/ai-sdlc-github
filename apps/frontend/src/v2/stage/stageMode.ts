/** Which of the seven stage states the conversation is in (the same states the design mock shows). */
export type StageMode = 'new' | 'plan' | 'running' | 'review' | 'amend' | 'approved' | 'escalated';

export function stageMode(i: { status: string; streaming: boolean; hasPlan: boolean; hasQuestions: boolean }): StageMode {
  if (i.streaming || i.status === 'IN_PROGRESS') return 'running';
  if (i.status === 'APPROVED') return 'approved';
  if (i.status === 'ESCALATED') return 'escalated';
  if (i.status === 'PENDING_REVIEW') return 'review';
  if (i.status === 'AMEND_REQUESTED') return 'amend';
  return i.hasPlan && !i.hasQuestions ? 'plan' : 'new';
}
