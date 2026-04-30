from code_minions.gates import (
    GateFinding,
    delivery_issues_to_findings,
    findings_to_text,
    runtime_findings_for_output,
)


def test_delivery_issues_convert_to_preflight_findings() -> None:
    findings = delivery_issues_to_findings(
        [
            {
                "code": "missing-required-file",
                "severity": "error",
                "message": "Delivery profile requires `package.json`.",
            }
        ],
        source="react-vite",
    )

    assert findings == [
        GateFinding(
            code="missing-required-file",
            severity="error",
            stage="preflight",
            message="Delivery profile requires `package.json`.",
            repair_hint="",
            source="react-vite",
            paths=[],
        )
    ]


def test_runtime_findings_include_failure_playbook_hint() -> None:
    findings = runtime_findings_for_output(
        "ReferenceError: document is not defined",
        source="react-vite",
    )

    assert findings[0].stage == "runtime"
    assert findings[0].severity == "error"
    assert findings[0].code == "referenceerror-document-is-not-defined"
    assert "jsdom" in findings[0].repair_hint.lower()


def test_findings_to_text_groups_by_stage_and_severity() -> None:
    text = findings_to_text([
        GateFinding(
            code="missing-test-file",
            severity="warning",
            stage="preflight",
            message="No test file found.",
            repair_hint="Add a test.",
            source="react-vite",
            paths=[],
        )
    ])

    assert "Gate findings:" in text
    assert "- warning preflight/missing-test-file: No test file found." in text
    assert "repair: Add a test." in text
