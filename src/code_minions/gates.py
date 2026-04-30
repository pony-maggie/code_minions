from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class GateFinding:
    code: str
    severity: str
    stage: str
    message: str
    repair_hint: str = ""
    source: str = ""
    paths: list[str] | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["paths"] = list(self.paths or [])
        return data


def finding_from_dict(data: dict[str, Any]) -> GateFinding:
    return GateFinding(
        code=str(data.get("code") or "unknown"),
        severity=str(data.get("severity") or "error"),
        stage=str(data.get("stage") or "runtime"),
        message=str(data.get("message") or ""),
        repair_hint=str(data.get("repair_hint") or ""),
        source=str(data.get("source") or ""),
        paths=[str(path) for path in data.get("paths") or []],
    )


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug[:80] or "runtime-failure"


def _runtime_code(output: str, hint: str) -> str:
    for line in output.splitlines():
        normalized = line.strip()
        if not normalized:
            continue
        if ":" in normalized:
            return _slug(normalized.split(":", 1)[0] + "-" + normalized.split(":", 1)[1])
        return _slug(normalized)
    return _slug(hint.split(".", 1)[0])


def delivery_issues_to_findings(
    issues: list[dict[str, str]],
    *,
    source: str,
) -> list[GateFinding]:
    findings: list[GateFinding] = []
    for issue in issues:
        findings.append(GateFinding(
            code=str(issue.get("code") or "delivery-profile-issue"),
            severity=str(issue.get("severity") or "error"),
            stage="preflight",
            message=str(issue.get("message") or ""),
            repair_hint=str(issue.get("repair_hint") or ""),
            source=source,
            paths=[],
        ))
    return findings


_TSC_DIAGNOSTIC_RE = re.compile(
    r"(?P<path>[^\s()]+\.(?:ts|tsx))\(\d+,\d+\): error TS(?P<ts_code>\d+): (?P<message>.+)"
)


def _typescript_runtime_findings(output: str, *, source: str) -> list[GateFinding]:
    findings: list[GateFinding] = []
    seen: set[tuple[str, str]] = set()
    for match in _TSC_DIAGNOSTIC_RE.finditer(output):
        path = match.group("path")
        ts_code = match.group("ts_code")
        message = match.group("message").strip()
        lowered = message.lower()

        if ts_code in {"2322", "2345"} and "not assignable to" in lowered:
            code = "typescript-type-contract-mismatch"
            repair_hint = (
                "Preserve the shared TypeScript type contract across files. Import and use the "
                "existing exported domain types instead of widening them to generic `string` or "
                "replacing unions/enums with incompatible values."
            )
        elif ts_code in {"2305", "2724"} or "has no exported member" in lowered:
            code = "typescript-missing-export"
            repair_hint = (
                "Import the existing exported symbol exactly as declared, or add a single canonical "
                "export if the type/constant is genuinely shared. Do not invent near-match names."
            )
        else:
            continue

        key = (code, path)
        if key in seen:
            continue
        seen.add(key)
        findings.append(GateFinding(
            code=code,
            severity="error",
            stage="runtime",
            message=f"TypeScript TS{ts_code}: {message}",
            repair_hint=repair_hint,
            source=source,
            paths=[path],
        ))
    return findings


def runtime_findings_for_output(output: str, *, source: str) -> list[GateFinding]:
    from code_minions.failure_playbook import failure_hints_for_output

    findings: list[GateFinding] = _typescript_runtime_findings(output, source=source)
    for hint in failure_hints_for_output(output):
        code = _runtime_code(output, hint)
        findings.append(GateFinding(
            code=code,
            severity="error",
            stage="runtime",
            message="Runtime failure matched the failure playbook.",
            repair_hint=hint,
            source=source,
            paths=[],
        ))
    return findings


def findings_to_text(findings: list[GateFinding]) -> str:
    if not findings:
        return ""
    lines = ["Gate findings:"]
    for finding in findings:
        lines.append(
            f"- {finding.severity} {finding.stage}/{finding.code}: {finding.message}"
        )
        if finding.repair_hint:
            lines.append(f"  repair: {finding.repair_hint}")
    return "\n".join(lines)


def findings_to_dicts(findings: list[GateFinding]) -> list[dict[str, Any]]:
    return [finding.to_dict() for finding in findings]
