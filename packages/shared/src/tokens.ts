/**
 * Cheap deterministic token estimator (~4 chars/token for English/code).
 * Used only for context-compression thresholds; exact usage comes from
 * provider responses and is recorded in the audit log.
 */
export function estimateTokens(text: string): number {
  if (text.length === 0) return 0;
  return Math.ceil(text.length / 4);
}

export function estimateTokensAll(texts: readonly string[]): number {
  return texts.reduce((sum, t) => sum + estimateTokens(t), 0);
}
