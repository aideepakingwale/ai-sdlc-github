"""Single error taxonomy — mirrors @sdlc/shared errors.ts so API responses are contract-identical."""

from typing import Any

ERROR_STATUS: dict[str, int] = {
    "AUTH_FAILED": 401,
    "FORBIDDEN": 403,
    "VALIDATION_FAILED": 400,
    "GUARDRAIL_BLOCKED": 422,
    "GATE_CONFLICT": 409,
    "NOT_FOUND": 404,
    "RATE_LIMITED": 429,
    "PROVIDER_ERROR": 502,
    "PROVIDER_EXHAUSTED": 503,
    "TOOL_ERROR": 502,
    "BUILD_LOOP_ESCALATED": 409,
    "INTERNAL": 500,
}


class SdlcError(Exception):
    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        if code not in ERROR_STATUS:
            raise ValueError(f"unknown error code {code}")
        self.code = code
        self.message = message
        self.http_status = ERROR_STATUS[code]
        self.details = details or {}
