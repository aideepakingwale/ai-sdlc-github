import { SdlcError } from './errors.js';

/**
 * Parse LLM JSON output tolerantly: direct parse, then fenced ```json block,
 * then outermost-braces slice. Throws SdlcError(PROVIDER_ERROR) when nothing parses.
 */
export function parseJsonLoose<T>(raw: string): T {
  const tryParse = (s: string): T | undefined => {
    try {
      return JSON.parse(s) as T;
    } catch {
      return undefined;
    }
  };
  const direct = tryParse(raw);
  if (direct !== undefined) return direct;
  const fenced = /```(?:json)?\s*([\s\S]*?)```/.exec(raw)?.[1];
  if (fenced) {
    const parsed = tryParse(fenced.trim());
    if (parsed !== undefined) return parsed;
  }
  const first = raw.indexOf('{');
  const last = raw.lastIndexOf('}');
  if (first >= 0 && last > first) {
    const parsed = tryParse(raw.slice(first, last + 1));
    if (parsed !== undefined) return parsed;
  }
  throw new SdlcError('PROVIDER_ERROR', 'LLM output was not valid JSON');
}
