"""Simple secret redactor used when persisting LLM/tool call logs.

v1 is pattern-based (key/token env var names + common secret patterns).
It's best-effort; never pretend to be a full DLP solution.
"""
from __future__ import annotations

import re

# Patterns that look like an API key / token (heuristic)
_SECRET_PATTERNS = [
    # Common env-var name assignments:  ANTHROPIC_API_KEY=sk-xxx
    re.compile(r"\b([A-Z][A-Z0-9_]*(?:_API_KEY|_TOKEN|_SECRET|_PASSWORD))\s*[:=]\s*\S+"),
    # Quoted versions: "api_key": "sk-xxx"
    re.compile(r'("(?:api[_-]?key|token|secret|password)"\s*:\s*")([^"]+)(")', re.IGNORECASE),
    # Bearer tokens
    re.compile(r"(Bearer\s+)([A-Za-z0-9_\-\.]{12,})"),
    # sk-* / api-* / pk_* / xoxb- style provider prefixes
    re.compile(r"\b(sk|api|pk)[_-][A-Za-z0-9]{16,}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
]


def redact_secrets(text: str) -> str:
    """Replace anything that looks like a key/token with [REDACTED].

    Best-effort: callers should still avoid deliberately serializing secrets.
    """
    out = text
    # First three patterns preserve context (show the key name), fourth/fifth
    # match the whole token so we just replace it entirely.
    out = _SECRET_PATTERNS[0].sub(lambda m: f"{m.group(1)}=[REDACTED]", out)
    out = _SECRET_PATTERNS[1].sub(lambda m: f"{m.group(1)}[REDACTED]{m.group(3)}", out)
    out = _SECRET_PATTERNS[2].sub(lambda m: f"{m.group(1)}[REDACTED]", out)
    out = _SECRET_PATTERNS[3].sub("[REDACTED]", out)
    out = _SECRET_PATTERNS[4].sub("[REDACTED]", out)
    return out
