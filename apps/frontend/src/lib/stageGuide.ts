/**
 * Pure guidance logic: given the state of a stage, which of the four steps are done / current,
 * and what should the user do NEXT, in plain words. Kept free of React so it is unit-tested.
 */
export type StepId = 'describe' | 'plan' | 'generate' | 'review';
export interface StepInfo {
  id: StepId;
  label: string;
  hint: string;
  state: 'done' | 'current' | 'attention' | 'error' | 'todo';
}
export type NextTone = 'info' | 'warning' | 'success' | 'error' | 'neutral';
export type NextAction = 'review-plan' | 'update-plan' | 'generate' | 'open-gate' | 'wait' | 'none';
export interface NextStep { tone: NextTone; title: string; body: string; action: NextAction }

export interface GuideInput {
  stageStatus: string;
  canEdit: boolean;
  /** names of upstream stages that still need approval */
  blockedOn: string[];
  hasPlan: boolean;
  planBuilding: boolean;
  planOutdated: boolean;
  planFresh: boolean;
  generating: boolean;
  hasOutputs: boolean;
  reviewerRole?: string;
  staleReason?: string | null;
}

const LABELS: Record<StepId, [string, string]> = {
  describe: ['Describe', 'Say what you need'],
  plan: ['Review plan', 'Check what will be made'],
  generate: ['Generate', 'The agent writes it'],
  review: ['Review & approve', 'Sign off the result'],
};

const mk = (states: [StepInfo['state'], StepInfo['state'], StepInfo['state'], StepInfo['state']]): StepInfo[] =>
  (['describe', 'plan', 'generate', 'review'] as StepId[]).map((id, i) => ({ id, label: LABELS[id][0], hint: LABELS[id][1], state: states[i]! }));

export function deriveGuide(i: GuideInput): { steps: StepInfo[]; next: NextStep } {
  const reviewer = i.reviewerRole ? `the ${i.reviewerRole}` : 'a reviewer';

  if (i.stageStatus === 'APPROVED') {
    return {
      steps: mk(['done', 'done', 'done', 'done']),
      next: {
        tone: 'success', action: 'none', title: 'This stage is approved',
        body: i.staleReason
          ? `Heads up: ${i.staleReason}. You can regenerate selected artifacts below, or approve as-is.`
          : 'The next stage can continue. If something changed, regenerate just the artifacts you need below.',
      },
    };
  }
  if (i.stageStatus === 'ESCALATED') {
    return {
      steps: mk(['done', 'done', 'error', 'todo']),
      next: { tone: 'error', action: 'none', title: 'Automation could not finish this stage', body: 'A human needs to step in. Check the activity log and the build errors, then retry or fix it manually.' },
    };
  }
  if (i.generating || i.stageStatus === 'IN_PROGRESS') {
    return {
      steps: mk(['done', 'done', 'current', 'todo']),
      next: { tone: 'info', action: 'wait', title: 'Generating — you can leave this page', body: 'The agent keeps working in the background and the files appear as tabs as they finish. Nothing can be edited until it completes.' },
    };
  }
  if (i.stageStatus === 'PENDING_REVIEW') {
    return {
      steps: mk(['done', 'done', 'done', 'attention']),
      next: { tone: 'warning', action: 'open-gate', title: 'Your review is needed', body: `Read the generated outputs, then approve or request changes — ${reviewer} signs this gate.` },
    };
  }
  if (i.blockedOn.length) {
    return {
      steps: mk(['todo', 'todo', 'todo', 'todo']),
      next: { tone: 'warning', action: 'none', title: `Waiting for ${i.blockedOn.join(', ')}`, body: 'This stage starts once the stage(s) before it are approved.' },
    };
  }
  if (!i.canEdit) {
    return {
      steps: mk(['todo', 'todo', 'todo', 'todo']),
      next: { tone: 'neutral', action: 'none', title: 'Read-only for you', body: `You can follow progress here. A member with write access to this stage (${reviewer} team) runs it.` },
    };
  }
  const amend = i.stageStatus === 'AMEND_REQUESTED';
  if (i.planBuilding) {
    return { steps: mk(['done', 'current', 'todo', 'todo']), next: { tone: 'info', action: 'wait', title: 'Building the plan…', body: 'The planner is working out which artifacts apply to this project (about 30 seconds). It is shared with every open tab.' } };
  }
  if (!i.hasPlan) {
    return {
      steps: mk(['current', 'todo', 'todo', 'todo']),
      next: {
        tone: 'info', action: 'review-plan',
        title: amend ? 'Changes were requested — update your instructions' : 'Start here: describe what you need',
        body: amend ? 'The reviewer’s feedback is pre-filled below. Adjust it, then click Review plan.' : 'Write what this stage should produce (a sentence or two is enough), then click Review plan. Nothing is generated until you confirm.',
      },
    };
  }
  if (i.planOutdated) {
    return {
      steps: mk(['done', 'attention', 'todo', 'todo']),
      next: { tone: 'warning', action: 'update-plan', title: 'Your plan is out of date', body: 'You changed something after the plan was reviewed. Click Update plan so what is generated matches what you asked for.' },
    };
  }
  if (i.planFresh) {
    return {
      steps: mk(['done', 'done', 'current', 'todo']),
      next: { tone: 'success', action: 'generate', title: 'Plan is final — ready to generate', body: 'Tick the artifacts you want, then click Generate. You can leave the page while it runs.' },
    };
  }
  return { steps: mk(['done', 'current', 'todo', 'todo']), next: { tone: 'info', action: 'review-plan', title: 'Review the plan', body: 'Check the artifacts and settings below.' } };
}
