export interface RunContextItem { layer: string; label: string; chars?: number; totalChars?: number }
export interface ArtefactRun {
  agentId: string; agentName: string; kind: string; role: string; fields: string[]; provider: string; model: string;
  system?: string; user?: string; context: RunContextItem[]; tokens: { prompt: number; completion: number }; at: string; reused?: boolean;
}
export interface ArtefactRunResponse {
  run: ArtefactRun | null; detail?: boolean; canRegenerate?: boolean; phase?: number; field?: string | null;
  artefact?: { type: string; title: string; version: number };
}

export const ROLE_LABEL: Record<string, string> = { reason: 'Reasoning model', generate: 'Generation model', light: 'Fast model', plan: 'Planning model', vision: 'Vision model' };
export const LAYER_LABEL: Record<string, string> = {
  instructions: 'Agent instructions', project: 'Project', canon: 'Rules and templates', memory: 'Team memory', input: 'Your brief', revision: 'Changes requested',
  upstream: 'Earlier stages', sibling: 'This stage', attached: 'Attached material', stage: 'Whole stage',
};

/** The context a run was given, grouped by layer with sizes. */
export function groupContext(items: RunContextItem[]): Array<{ layer: string; label: string; chars: number; items: RunContextItem[] }> {
  const order: string[] = [];
  const by = new Map<string, RunContextItem[]>();
  for (const i of items) {
    if (!by.has(i.layer)) { by.set(i.layer, []); order.push(i.layer); }
    by.get(i.layer)!.push(i);
  }
  return order.map((layer) => ({ layer, label: LAYER_LABEL[layer] ?? layer, items: by.get(layer)!, chars: by.get(layer)!.reduce((n, i) => n + (i.chars ?? 0), 0) }));
}

export const approxTokens = (chars: number) => Math.max(1, Math.round(chars / 4));
