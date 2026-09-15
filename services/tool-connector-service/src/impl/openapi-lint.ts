import { parse } from 'yaml';

export interface LintViolation {
  code: string;
  message: string;
  path: string;
  severity: string;
}

/**
 * Spectral-style OpenAPI 3.x linter: implements the
 * high-signal subset of spectral:oas rules without the heavy dependency chain.
 * Deterministic and side-effect free, hence `cacheable` in the registry.
 */
export function lintOpenapi(openapiYaml: string): { result: 'PASS' | 'FAIL'; violations: LintViolation[] } {
  const violations: LintViolation[] = [];
  let doc: unknown;
  try {
    doc = parse(openapiYaml);
  } catch (err) {
    return {
      result: 'FAIL',
      violations: [{ code: 'parse-error', message: `Invalid YAML: ${String(err)}`, path: '/', severity: 'error' }],
    };
  }
  if (typeof doc !== 'object' || doc === null) {
    return {
      result: 'FAIL',
      violations: [{ code: 'invalid-document', message: 'Document is not an object', path: '/', severity: 'error' }],
    };
  }
  const root = doc as Record<string, unknown>;

  if (typeof root.openapi !== 'string' || !root.openapi.startsWith('3.')) {
    violations.push({ code: 'oas3-schema', message: 'openapi must be a 3.x version string', path: '/openapi', severity: 'error' });
  }

  const info = root.info as Record<string, unknown> | undefined;
  if (!info) violations.push({ code: 'info-required', message: 'info object is required', path: '/info', severity: 'error' });
  else {
    if (!info.title) violations.push({ code: 'info-title', message: 'info.title is required', path: '/info/title', severity: 'error' });
    if (!info.version) violations.push({ code: 'info-version', message: 'info.version is required', path: '/info/version', severity: 'error' });
    if (!info.description)
      violations.push({ code: 'info-description', message: 'info.description is recommended', path: '/info/description', severity: 'warn' });
  }

  const paths = root.paths as Record<string, unknown> | undefined;
  if (!paths || Object.keys(paths).length === 0) {
    violations.push({ code: 'paths-required', message: 'paths must contain at least one entry', path: '/paths', severity: 'error' });
  } else {
    const opIds = new Set<string>();
    const METHODS = ['get', 'put', 'post', 'delete', 'options', 'head', 'patch', 'trace'];
    for (const [p, item] of Object.entries(paths)) {
      if (!p.startsWith('/')) {
        violations.push({ code: 'path-keys-slash', message: `Path '${p}' must begin with '/'`, path: `/paths/${p}`, severity: 'error' });
      }
      if (typeof item !== 'object' || item === null) continue;
      for (const [method, op] of Object.entries(item as Record<string, unknown>)) {
        if (!METHODS.includes(method) || typeof op !== 'object' || op === null) continue;
        const oper = op as Record<string, unknown>;
        const where = `/paths/${p}/${method}`;
        if (typeof oper.operationId !== 'string' || oper.operationId.length === 0) {
          violations.push({ code: 'operation-operationId', message: 'operationId is required', path: where, severity: 'error' });
        } else if (opIds.has(oper.operationId)) {
          violations.push({ code: 'operation-operationId-unique', message: `Duplicate operationId '${oper.operationId}'`, path: where, severity: 'error' });
        } else {
          opIds.add(oper.operationId);
        }
        const responses = oper.responses as Record<string, unknown> | undefined;
        if (!responses || Object.keys(responses).length === 0) {
          violations.push({ code: 'operation-responses', message: 'At least one response is required', path: `${where}/responses`, severity: 'error' });
        }
        if (!oper.summary && !oper.description) {
          violations.push({ code: 'operation-description', message: 'summary or description is recommended', path: where, severity: 'warn' });
        }
      }
    }
  }

  const hasErrors = violations.some((v) => v.severity === 'error');
  return { result: hasErrors ? 'FAIL' : 'PASS', violations };
}
