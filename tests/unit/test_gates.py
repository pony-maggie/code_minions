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


def test_runtime_findings_classify_typescript_contract_diagnostics() -> None:
    findings = runtime_findings_for_output(
        "\n".join([
            "src/components/Board.tsx(22,15): error TS2322: Type 'string | null' is not assignable to type 'Cell'.",
            "  Type 'string' is not assignable to type 'Cell'.",
            "src/hooks/useGameState.ts(2,10): error TS2724: '../types' has no exported member named 'BoardSize'. Did you mean 'BOARD_SIZE'?",
        ]),
        source="react-vite",
    )

    assert [finding.code for finding in findings] == [
        "typescript-type-contract-mismatch",
        "typescript-missing-export",
    ]
    assert findings[0].paths == ["src/components/Board.tsx"]
    assert "shared TypeScript type contract" in findings[0].repair_hint
    assert findings[1].paths == ["src/hooks/useGameState.ts"]
    assert "existing exported symbol" in findings[1].repair_hint


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
