/** Single error taxonomy for every service. HTTP handlers map these to JSON responses. */
export const ERROR_CODES = [
  'AUTH_FAILED',
  'FORBIDDEN',
  'VALIDATION_FAILED',
  'GUARDRAIL_BLOCKED',
  'GATE_CONFLICT',
  'NOT_FOUND',
  'RATE_LIMITED',
  'PROVIDER_ERROR',
  'PROVIDER_EXHAUSTED',
  'TOOL_ERROR',
  'BUILD_LOOP_ESCALATED',
  'INTERNAL',
] as const;

export type ErrorCode = (typeof ERROR_CODES)[number];

const DEFAULT_STATUS: Record<ErrorCode, number> = {
  AUTH_FAILED: 401,
  FORBIDDEN: 403,
  VALIDATION_FAILED: 400,
  GUARDRAIL_BLOCKED: 422,
  GATE_CONFLICT: 409,
  NOT_FOUND: 404,
  RATE_LIMITED: 429,
  PROVIDER_ERROR: 502,
  PROVIDER_EXHAUSTED: 503,
  TOOL_ERROR: 502,
  BUILD_LOOP_ESCALATED: 409,
  INTERNAL: 500,
};

/** Application error. `details` is safe-to-log structured context; never raw secrets/bodies. */
export class SdlcError extends Error {
  readonly code: ErrorCode;
  readonly httpStatus: number;
  readonly details?: Record<string, unknown>;

  constructor(
    code: ErrorCode,
    message: string,
    opts?: { httpStatus?: number; details?: Record<string, unknown>; cause?: unknown },
  ) {
    super(message, opts?.cause !== undefined ? { cause: opts.cause } : undefined);
    this.name = 'SdlcError';
    this.code = code;
    this.httpStatus = opts?.httpStatus ?? DEFAULT_STATUS[code];
    this.details = opts?.details;
  }
}

export function isSdlcError(err: unknown): err is SdlcError {
  return err instanceof SdlcError;
}

/** Wrap unknown thrown values into SdlcError without losing the original. */
export function toSdlcError(err: unknown, fallbackMessage = 'Internal error'): SdlcError {
  if (isSdlcError(err)) return err;
  const message = err instanceof Error ? err.message : fallbackMessage;
  return new SdlcError('INTERNAL', message, { cause: err });
}
