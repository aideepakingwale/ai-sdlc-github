"""Deterministic guardrail layer (Module 2 §1, hardened in).

Responsible-AI posture: every rule is a named, versioned, regex-based check so
verdicts are reproducible and auditable — no probabilistic filter whose
behaviour can drift. Rules carry a category, an action and a human-readable
description, and the full inventory is exposed read-only at
`GET /api/governance/guardrails` for transparency.

Enforcement points (all audited):
  - chat input                     → block  (guardrail.input_blocked)
  - skill execution input          → block
  - gate review comments           → block  (amend feedback is injected into
                                             regeneration prompts — a prompt-
                                             injection channel, so it is
                                             screened like any other input)
  - chat/skill output              → mask   (guardrail.output_masked)
  - artifact bodies before persist → mask   (guardrail.artifact_masked)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from ..domain.errors import SdlcError

GuardrailCategory = Literal["pii", "injection", "secret"]
GuardrailAction = Literal["block", "mask"]

GUARDRAILS_VERSION = 2


@dataclass(frozen=True)
class GuardrailRule:
    name: str
    category: GuardrailCategory
    action: GuardrailAction
    description: str
    pattern: re.Pattern[str]


# ---------------------------------------------------------------- secrets
# Shared between input (block: never accept pasted credentials) and output
# (mask: never emit credentials, even if a model hallucinates or echoes one).
_SECRET_PATTERNS: list[tuple[str, str, str]] = [
    ("aws-access-key", r"\bAKIA[0-9A-Z]{16}\b", "AWS access key ID"),
    ("aws-secret-key", r"\baws_secret_access_key\s*[=:]\s*\S+", "AWS secret access key assignment"),
    ("private-key-block",
     r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----",
     "PEM private key block"),
    ("bearer-token",
     r"\bBearer\s+[A-Za-z0-9\-_]{20,}\.[A-Za-z0-9\-_]{5,}\.[A-Za-z0-9\-_]{5,}\b",
     "Bearer JWT credential"),
    ("raw-jwt", r"\beyJ[A-Za-z0-9\-_]{10,}\.eyJ[A-Za-z0-9\-_]{10,}\.[A-Za-z0-9\-_]{10,}\b",
     "Raw JWT token"),
    ("github-pat", r"\bgh[pousr]_[A-Za-z0-9]{36,}\b", "GitHub personal access token"),
    ("slack-token", r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b", "Slack API token"),
    ("generic-api-key", r"\b(sk|pk|rk)-[A-Za-z0-9]{20,}\b", "Generic API key (sk-/pk-/rk-)"),
    ("connection-string",
     r"\b(postgres(ql)?|mysql|mongodb(\+srv)?|redis|amqp)://[^\s:@/]+:[^\s@/]+@",
     "Database connection string with embedded password"),
]

_PII_PATTERNS: list[tuple[str, str, str]] = [
    ("card-number", r"\b(?:\d[ -]?){13,16}\b", "Payment card number"),
    ("uk-nino", r"\b[ABCEGHJ-PRSTW-Z]{2}\s?\d{2}\s?\d{2}\s?\d{2}\s?[A-D]\b",
     "UK National Insurance number"),
    ("ssn", r"\b\d{3}-\d{2}-\d{4}\b", "US Social Security number"),
]

_INJECTION_PATTERNS: list[tuple[str, str, str]] = [
    ("ignore-instructions",
     r"(ignore|disregard|forget)\s+(all\s+)?(previous|prior|above|your)\s+(instructions|rules|prompts|guidelines)",
     "Attempt to override system instructions"),
    ("system-override", r"(you\s+are\s+now|act\s+as|pretend\s+to\s+be)\s+(dan\b|an?\s+unrestricted|an?\s+unfiltered)",
     "Persona-override / jailbreak attempt"),
    ("developer-mode", r"\b(developer|god|sudo)\s+mode\b.{0,40}\b(enable|activate|unlock)|"
     r"\b(enable|activate|unlock)\b.{0,40}\b(developer|god|sudo)\s+mode\b",
     "Fake privileged-mode activation"),
    ("reveal-prompt", r"(reveal|print|show|repeat)\s+(your\s+)?(system\s+prompt|hidden\s+instructions|initial\s+instructions)",
     "System-prompt exfiltration attempt"),
    ("role-smuggling", r"^\s*(system|assistant)\s*:", "Chat-role smuggling in user text"),
]


def _rules(specs: list[tuple[str, str, str]], category: GuardrailCategory,
           action: GuardrailAction, flags: int = re.I) -> list[GuardrailRule]:
    return [
        GuardrailRule(
            name=f"{category}:{name}", category=category, action=action,
            description=desc,
            pattern=re.compile(rx, flags | (re.M if name == "role-smuggling" else 0)),
        )
        for name, rx, desc in specs
    ]


INPUT_RULES: list[GuardrailRule] = [
    *_rules(_PII_PATTERNS, "pii", "block"),
    *_rules(_INJECTION_PATTERNS, "injection", "block"),
    *_rules(_SECRET_PATTERNS, "secret", "block"),
]

OUTPUT_RULES: list[GuardrailRule] = [
    *_rules(_SECRET_PATTERNS, "secret", "mask"),
    *_rules(_PII_PATTERNS, "pii", "mask"),
]


def check_input(text: str) -> list[str]:
    return [r.name for r in INPUT_RULES if r.pattern.search(text)]


def enforce_input(text: str, *, channel: str = "chat") -> None:
    """Blocks the request when any input rule fires. `channel` labels the
    enforcement point (chat | skill | gate_review) for the error and audit."""
    hits = check_input(text)
    if hits:
        raise SdlcError(
            "GUARDRAIL_BLOCKED",
            f"Input blocked by Responsible AI policy ({channel}): {', '.join(hits)}. "
            "Remove personal data, credentials or instruction-override phrasing and retry.",
            {"rules": hits, "channel": channel},
        )


def sanitise_output(text: str) -> tuple[str, list[str]]:
    masked: list[str] = []
    for rule in OUTPUT_RULES:
        if rule.pattern.search(text):
            masked.append(rule.name)
            text = rule.pattern.sub("[REDACTED]", text)
    return text, masked


def inventory() -> dict:
    """Read-only rule inventory for the governance API (patterns included so
    the policy is fully auditable — these are detection rules, not secrets)."""
    def row(r: GuardrailRule, applied_to: str) -> dict:
        return {
            "name": r.name, "category": r.category, "action": r.action,
            "description": r.description, "pattern": r.pattern.pattern,
            "appliedTo": applied_to,
        }

    return {
        "version": GUARDRAILS_VERSION,
        "enforcementPoints": {
            "input": ["chat message", "skill input", "gate review comments"],
            "output": ["chat/skill responses", "artifact bodies before persist"],
        },
        "rules": [
            *[row(r, "input") for r in INPUT_RULES],
            *[row(r, "output") for r in OUTPUT_RULES],
        ],
    }
