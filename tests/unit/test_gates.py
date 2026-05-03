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


def test_delivery_issues_preserve_repair_hint_and_paths() -> None:
    findings = delivery_issues_to_findings(
        [
            {
                "code": "unresolved-relative-import",
                "severity": "error",
                "message": "`src/hooks/useGameState.ts` imports `./types`.",
                "repair_hint": "Import `../types`.",
                "paths": ["src/hooks/useGameState.ts", "src/types.ts"],
            }
        ],
        source="react-vite",
    )

    assert findings == [
        GateFinding(
            code="unresolved-relative-import",
            severity="error",
            stage="preflight",
            message="`src/hooks/useGameState.ts` imports `./types`.",
            repair_hint="Import `../types`.",
            source="react-vite",
            paths=["src/hooks/useGameState.ts", "src/types.ts"],
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


def test_runtime_findings_classify_npm_unpublished_dependency_version() -> None:
    findings = runtime_findings_for_output(
        "npm error code ETARGET\n"
        "npm error notarget No matching version found for @testing-library/user-event@^16.0.1.",
        source="react-vite",
    )

    assert findings[0].stage == "runtime"
    assert findings[0].severity == "error"
    assert findings[0].code == "npm-error-code-etarget"
    assert "published npm version" in findings[0].repair_hint


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


def test_runtime_findings_classify_react_hook_missing_imports() -> None:
    findings = runtime_findings_for_output(
        "\n".join([
            "src/hooks/useGame.ts(20,29): error TS2304: Cannot find name 'useState'.",
            "src/hooks/useGame.ts(26,20): error TS2304: Cannot find name 'useCallback'.",
        ]),
        source="react-vite",
    )

    assert [finding.code for finding in findings] == ["typescript-missing-react-hook-import"]
    assert findings[0].paths == ["src/hooks/useGame.ts"]
    assert "import { useCallback, useState } from 'react'" in findings[0].repair_hint


def test_runtime_findings_classify_missing_local_symbol() -> None:
    findings = runtime_findings_for_output(
        "src/hooks/useGame.ts(60,10): error TS2304: Cannot find name 'isWinningCell'.",
        source="react-vite",
    )

    assert [finding.code for finding in findings] == ["typescript-missing-symbol"]
    assert findings[0].paths == ["src/hooks/useGame.ts"]
    assert "Define or import `isWinningCell`" in findings[0].repair_hint


def test_runtime_findings_classify_local_declaration_not_exported() -> None:
    findings = runtime_findings_for_output(
        'src/hooks/useGame.ts(2,31): error TS2459: Module \'"../utils/gameLogic"\' '
        "declares 'StoneColor' locally, but it is not exported.",
        source="react-vite",
    )

    assert [finding.code for finding in findings] == [
        "typescript-local-declaration-not-exported"
    ]
    assert findings[0].paths == ["src/hooks/useGame.ts"]
    assert "canonical source module" in findings[0].repair_hint


def test_runtime_findings_classify_implicit_any_diagnostics() -> None:
    findings = runtime_findings_for_output(
        "\n".join([
            "src/App.tsx(30,21): error TS7006: Parameter 'row' implicitly has an 'any' type.",
            "src/App.tsx(30,26): error TS7006: Parameter 'rowIndex' implicitly has an 'any' type.",
            "src/Board.tsx(31,23): error TS7006: Parameter 'cell' implicitly has an 'any' type.",
        ]),
        source="react-vite",
    )

    assert [finding.code for finding in findings] == [
        "typescript-implicit-any",
        "typescript-implicit-any",
    ]
    assert findings[0].paths == ["src/App.tsx"]
    assert "Annotate callback parameters" in findings[0].repair_hint


def test_runtime_findings_classify_missing_winning_element_assertion() -> None:
    findings = runtime_findings_for_output(
        "\n".join([
            "FAIL  tests/GameState.test.tsx > 胜负判定与获胜棋子高亮 > 黑方横向五连",
            "Error: expect(received).toBeInTheDocument()",
            "received value must be an HTMLElement or an SVGElement.",
            " ❯ tests/GameState.test.tsx:57:60",
            "     57|         expect(cell.querySelector('.stone.black.winning')).toBeInTheDocument()",
        ]),
        source="react-vite",
    )

    assert [finding.code for finding in findings] == [
        "react-testing-library-null-element-assertion"
    ]
    assert findings[0].paths == ["tests/GameState.test.tsx"]
    assert "winningCells" in findings[0].repair_hint


def test_runtime_findings_classify_turn_based_board_game_accidental_early_win() -> None:
    findings = runtime_findings_for_output(
        "\n".join([
            "FAIL  tests/GameState.test.tsx > 胜负判定与获胜棋子高亮 > 白方纵向五连",
            "Error: expect(element).toHaveTextContent()",
            "Expected element to have text content:",
            "  /白方/",
            "Received:",
            "  胜者: 黑方",
            " ❯ tests/GameState.test.tsx:94:26",
        ]),
        source="react-vite",
    )

    assert [finding.code for finding in findings] == [
        "turn-based-board-game-accidental-early-win"
    ]
    assert findings[0].paths == ["tests/GameState.test.tsx"]
    assert "filler moves" in findings[0].repair_hint


def test_runtime_findings_classify_user_event_import_mismatch() -> None:
    findings = runtime_findings_for_output(
        'src/App.test.tsx(39,13): error TS2339: Property \'user\' does not exist on type '
        '\'typeof import("/worktree/node_modules/@testing-library/user-event/dist/types/index")\'.',
        source="react-vite",
    )

    assert [finding.code for finding in findings] == [
        "testing-library-user-event-import-mismatch"
    ]
    assert findings[0].paths == ["src/App.test.tsx"]
    assert "default export" in findings[0].repair_hint


def test_runtime_findings_classify_jsx_missing_required_props() -> None:
    findings = runtime_findings_for_output(
        "src/App.tsx(9,8): error TS2739: Type '{}' is missing the following properties "
        "from type 'BoardProps': cells, onCellClick",
        source="react-vite",
    )

    assert [finding.code for finding in findings] == [
        "typescript-jsx-missing-required-props"
    ]
    assert findings[0].paths == ["src/App.tsx"]
    assert "Pass the required props" in findings[0].repair_hint


def test_runtime_findings_classify_board_cell_state_player_type_mismatch() -> None:
    findings = runtime_findings_for_output(
        "\n".join([
            "src/components/Board.tsx(31,52): error TS2345: Argument of type 'StoneColor' is not assignable to parameter of type '\"black\" | \"white\"'.",
            "  Type '\"empty\"' is not assignable to type '\"black\" | \"white\"'.",
            "src/utils/checkWinner.ts(11,7): error TS2367: This comparison appears to be unintentional because the types '\"black\" | \"white\"' and '\"empty\"' have no overlap.",
        ]),
        source="react-vite",
    )

    assert [finding.code for finding in findings] == [
        "turn-board-cell-state-player-type-mismatch"
    ]
    assert findings[0].paths == ["src/components/Board.tsx"]
    assert "CellState" in findings[0].repair_hint
    assert "Player" in findings[0].repair_hint


def test_runtime_findings_classify_missing_assertive_live_region() -> None:
    findings = runtime_findings_for_output(
        "\n".join([
            "FAIL  tests/StatusPanel.test.tsx > StatusPanel > 无障碍支持 > 有用于屏幕阅读器播报的 live region",
            "Error: expect(received).toBeInTheDocument()",
            "received value must be an HTMLElement or an SVGElement.",
            "const liveRegion = document.querySelector('[aria-live=\"assertive\"]');",
            "❯ tests/StatusPanel.test.tsx:180:26",
        ]),
        source="react-vite",
    )

    assert "react-a11y-live-region-missing" in [finding.code for finding in findings]
    finding = next(item for item in findings if item.code == "react-a11y-live-region-missing")
    assert finding.paths == ["tests/StatusPanel.test.tsx"]
    assert "aria-live=\"assertive\"" in finding.repair_hint


def test_runtime_findings_classify_status_panel_label_mismatch() -> None:
    findings = runtime_findings_for_output(
        "\n".join([
            "FAIL  tests/StatusPanel.test.tsx > StatusPanel > 无障碍支持 > 游戏结束时播报胜者或平局",
            "AssertionError: expected '结果：黑方获胜' to contain '黑子获胜'",
            "Expected: \"黑子获胜\"",
            "Received: \"结果：黑方获胜\"",
            "❯ tests/StatusPanel.test.tsx:194:39",
        ]),
        source="react-vite",
    )

    assert [finding.code for finding in findings] == [
        "react-status-panel-label-contract-mismatch"
    ]
    assert findings[0].paths == ["tests/StatusPanel.test.tsx"]
    assert "same player terminology" in findings[0].repair_hint


def test_runtime_findings_classify_missing_status_history_section() -> None:
    findings = runtime_findings_for_output(
        "\n".join([
            "FAIL  tests/StatusPanel.test.tsx > StatusPanel > 落子历史显示 > 显示最近5步落子历史",
            "expect(screen.getByText(/落子历史/)).toBeInTheDocument();",
            "❯ tests/StatusPanel.test.tsx:151:21",
        ]),
        source="react-vite",
    )

    assert [finding.code for finding in findings] == [
        "react-status-panel-history-section-missing"
    ]
    assert findings[0].paths == ["tests/StatusPanel.test.tsx"]
    assert "落子历史" in findings[0].repair_hint


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
