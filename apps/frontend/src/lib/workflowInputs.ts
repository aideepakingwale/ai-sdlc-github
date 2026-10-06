/** What a stage may take as input: the requirements entry plus outputs of the stages it depends on,
 *  directly or through others. Never its own outputs, and never those of stages that come after it. */
export interface StageIO { key: string; dependsOn: string[]; outputs: string[]; inputs?: string[] }

export function upstreamOutputs(stages: StageIO[], key: string): string[] {
  const byKey = new Map(stages.map((s) => [s.key, s]));
  const seen = new Set<string>();
  const walk = (k: string) => {
    for (const dep of byKey.get(k)?.dependsOn ?? []) {
      if (dep !== key && !seen.has(dep)) { seen.add(dep); walk(dep); }
    }
  };
  walk(key);
  const out = new Set<string>(['requirements']);
  for (const s of stages) if (seen.has(s.key)) s.outputs.forEach((o) => out.add(o));
  return [...out];
}

/** Choices to show for a stage: what upstream can supply, plus any already-selected input nothing upstream
 *  produces (flagged `unmet`) so it can still be seen and removed. */
export function inputChoices(stages: StageIO[], key: string): Array<{ name: string; unmet: boolean }> {
  const ok = upstreamOutputs(stages, key);
  const mine = stages.find((s) => s.key === key)?.inputs ?? [];
  return [...ok.map((name) => ({ name, unmet: false })), ...mine.filter((i) => !ok.includes(i)).map((name) => ({ name, unmet: true }))];
}
