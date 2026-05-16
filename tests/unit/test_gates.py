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


def test_runtime_findings_classify_board_test_null_array_inference() -> None:
    findings = runtime_findings_for_output(
        "\n".join([
            'src/components/Board.test.tsx(50,7): error TS2322: Type \'"black"\' is not assignable to type \'null\'.',
            'src/components/Board.test.tsx(59,7): error TS2322: Type \'"white"\' is not assignable to type \'null\'.',
        ]),
        source="react-vite",
    )

    assert [finding.code for finding in findings] == [
        "react-board-test-null-array-inference"
    ]
    assert findings[0].paths == ["src/components/Board.test.tsx"]
    assert "BoardState" in findings[0].repair_hint
    assert "Array.from" in findings[0].repair_hint


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


def test_runtime_findings_classify_user_event_timer_method_misuse() -> None:
    findings = runtime_findings_for_output(
        "src/App.test.tsx(31,10): error TS2339: Property 'advanceTimersByTime' does not exist on type 'UserEvent'.",
        source="react-vite",
    )

    finding = next(item for item in findings if item.code == "testing-library-user-event-timer-method")
    assert "vi.advanceTimersByTime" in finding.repair_hint


def test_runtime_findings_classify_optional_or_criteria_overassertion() -> None:
    findings = runtime_findings_for_output(
        "\n".join([
            "FAIL src/App.test.tsx > start prompt > renders start button or enter hint",
            "expect(element).toHaveTextContent()",
            "received value must be a Node.",
            "document.querySelector('.enter-hint')",
        ]),
        source="react-vite",
    )

    finding = next(item for item in findings if item.code == "react-optional-or-criteria-overasserted")
    assert "OR acceptance criteria" in finding.repair_hint
    assert "at least one supported" in finding.repair_hint


def test_runtime_findings_classify_missing_local_symbol() -> None:
    findings = runtime_findings_for_output(
        "src/hooks/useGame.ts(60,10): error TS2304: Cannot find name 'isWinningCell'.",
        source="react-vite",
    )

    assert [finding.code for finding in findings] == ["typescript-missing-symbol"]
    assert findings[0].paths == ["src/hooks/useGame.ts"]
    assert "Define or import `isWinningCell`" in findings[0].repair_hint


def test_runtime_findings_classify_missing_css_module_declarations() -> None:
    findings = runtime_findings_for_output(
        "src/components/Board.tsx(2,20): error TS2307: Cannot find module './Board.module.css' "
        "or its corresponding type declarations.",
        source="react-vite",
    )

    assert [finding.code for finding in findings] == [
        "react-vite-css-module-types-missing"
    ]
    assert findings[0].paths == ["src/components/Board.tsx"]
    assert "vite/client" in findings[0].repair_hint


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


def test_runtime_findings_classify_react_missing_named_export_function() -> None:
    findings = runtime_findings_for_output(
        "\n".join([
            "TypeError: findWinningCells is not a function",
            " ❯ src/App.tsx:27:21",
            "     25|     if (winner) {",
            "     26|       setGameStatus('won');",
            "     27|       const cells = findWinningCells(newBoard, row, col, currentStone);",
            "       |                     ^",
        ]),
        source="react-vite",
    )

    assert [finding.code for finding in findings] == [
        "react-runtime-missing-named-function-export"
    ]
    assert findings[0].paths == ["src/App.tsx"]
    assert "export" in findings[0].repair_hint
    assert "findWinningCells" in findings[0].repair_hint


def test_runtime_findings_classify_hook_test_private_state_mutator() -> None:
    findings = runtime_findings_for_output(
        "\n".join([
            "TypeError: result.current._setState is not a function",
            " ❯ tests/useGame.test.ts:42:35",
            "     40|       act(() => {",
            "     41|         // @ts-ignore - accessing internal for test setup",
            "     42|         result.current['_setState']({",
        ]),
        source="react-vite",
    )

    assert "react-hook-test-private-state-mutator" in [
        finding.code for finding in findings
    ]
    finding = next(
        item for item in findings
        if item.code == "react-hook-test-private-state-mutator"
    )
    assert finding.paths == ["tests/useGame.test.ts"]
    assert "public hook API" in finding.repair_hint
    assert "_setState" in finding.repair_hint


def test_runtime_findings_classify_board_root_class_contract_mismatch() -> None:
    findings = runtime_findings_for_output(
        "\n".join([
            "FAIL  src/components/Board.test.tsx > Board 组件 > 空棋盘渲染 > 渲染 15x15 线网",
            "Error: expect(element).toHaveClass(\"board\")",
            "Expected the element to have class:",
            "  board",
            "Received:",
            "  board-container",
            "❯ src/components/Board.test.tsx:14:21",
        ]),
        source="react-vite",
    )

    assert [finding.code for finding in findings] == [
        "react-board-root-class-contract-mismatch"
    ]
    assert findings[0].paths == ["src/components/Board.test.tsx"]
    assert "data-testid=\"board\"" in findings[0].repair_hint
    assert "board-container" in findings[0].repair_hint


def test_runtime_findings_classify_board_cell_text_vs_accessible_name_mismatch() -> None:
    findings = runtime_findings_for_output(
        "\n".join([
            "FAIL  src/Board.test.tsx > Board > stone placement > places black stone on click",
            "Error: expect(element).toHaveTextContent()",
            "Expected element to have text content:",
            "  /黑/",
            "Received:",
            "  ●",
            "❯ src/Board.test.tsx:48:25",
        ]),
        source="react-vite",
    )

    assert [finding.code for finding in findings] == [
        "react-board-cell-text-accessible-name-mismatch"
    ]
    assert findings[0].paths == ["src/Board.test.tsx"]
    assert "accessible name" in findings[0].repair_hint


def test_runtime_findings_classify_board_cell_literal_text_vs_symbol_mismatch() -> None:
    findings = runtime_findings_for_output(
        "\n".join([
            "FAIL  tests/GameInteraction.test.tsx > 核心落子交互与回合管理 > Given 空棋盘",
            "Error: expect(element).toHaveTextContent()",
            "Expected element to have text content:",
            "  黑子",
            "Received:",
            "  ●",
            "❯ tests/GameInteraction.test.tsx:21:18",
        ]),
        source="react-vite",
    )

    assert [finding.code for finding in findings] == [
        "react-board-cell-text-accessible-name-mismatch"
    ]
    assert findings[0].paths == ["tests/GameInteraction.test.tsx"]
    assert "accessible name" in findings[0].repair_hint


def test_runtime_findings_classify_broad_empty_cell_role_query() -> None:
    findings = runtime_findings_for_output(
        "\n".join([
            'FAIL  src/App.test.tsx > App > shows empty board initially',
            'TestingLibraryElementError: Found multiple elements with the role "button" and name `/, 空$/`',
            'Name "行1列1, 空":',
            '  <button aria-label="行1列1, 空" data-testid="cell-0-0" />',
            " ❯ src/App.test.tsx:18:25",
        ]),
        source="react-vite",
    )

    assert findings[0].code == "react-board-empty-cell-query-too-broad"
    assert findings[0].paths == ["src/App.test.tsx"]
    assert "getAllByRole" in findings[0].repair_hint
    assert "cell-0-0" in findings[0].repair_hint


def test_runtime_findings_classify_zero_based_coordinate_accessible_name() -> None:
    findings = runtime_findings_for_output(
        "\n".join([
            "FAIL  src/Board.test.tsx > Board > exposes coordinates",
            "TestingLibraryElementError: Unable to find an accessible element with the role \"button\" and name `/^行0列0$/`",
            'Name "行1列1, 空":',
            '  <button aria-label="行1列1, 空" data-testid="cell-0-0" />',
            " ❯ src/Board.test.tsx:25:32",
        ]),
        source="react-vite",
    )

    assert [finding.code for finding in findings] == [
        "react-board-coordinate-accessible-name-mismatch"
    ]
    assert findings[0].paths == ["src/Board.test.tsx"]
    assert "1-based" in findings[0].repair_hint
    assert "state suffix" in findings[0].repair_hint


def test_runtime_findings_classify_presentational_board_occupied_click_contract() -> None:
    findings = runtime_findings_for_output(
        "\n".join([
            "FAIL  src/__tests__/Board.test.tsx > Board > 点击已有棋子的位置不调用onCellClick",
            "AssertionError: expected \"spy\" to not be called at all, but actually been called 1 times",
            "Received:",
            "  1st spy call:",
            "    Array [",
            "      7,",
            "      7,",
            "    ]",
            " ❯ src/__tests__/Board.test.tsx:47:33",
        ]),
        source="react-vite",
    )

    assert [finding.code for finding in findings] == [
        "react-presentational-board-occupied-click-contract"
    ]
    assert findings[0].paths == ["src/__tests__/Board.test.tsx"]
    assert "App" in findings[0].repair_hint
    assert "presentational" in findings[0].repair_hint


def test_runtime_findings_classify_missing_reset_button_contract() -> None:
    findings = runtime_findings_for_output(
        "\n".join([
            "TestingLibraryElementError: Unable to find an element by: [data-testid=\"reset-button\"]",
            "❯ src/App.test.tsx:129:31",
            "  129|       await user.click(screen.getByTestId('reset-button'))",
        ]),
        source="react-vite",
    )

    assert [finding.code for finding in findings] == [
        "react-vite-missing-stable-control-testid"
    ]
    assert findings[0].paths == ["src/App.test.tsx"]
    assert "`reset-button`" in findings[0].repair_hint


def test_runtime_findings_classify_last_move_reset_null_contract() -> None:
    findings = runtime_findings_for_output(
        "\n".join([
            "FAIL  src/hooks/useGameState.test.ts > useGameState > reset game > should reset all state",
            "AssertionError: expected undefined to be null",
            "❯ src/hooks/useGameState.test.ts:172:39",
            "  172|       expect(result.current.lastMove).toBeNull()",
        ]),
        source="react-vite",
    )

    assert [finding.code for finding in findings] == [
        "turn-based-board-game-last-move-reset-contract"
    ]
    assert findings[0].paths == ["src/hooks/useGameState.test.ts"]
    assert "`lastMove`" in findings[0].repair_hint


def test_runtime_findings_classify_nullable_win_result_without_guard() -> None:
    findings = runtime_findings_for_output(
        "\n".join([
            "src/App.tsx(32,9): error TS18047: 'result' is possibly 'null'.",
            "src/App.tsx(33,17): error TS18047: 'result' is possibly 'null'.",
            "src/App.tsx(34,23): error TS18047: 'result' is possibly 'null'.",
            "src/App.tsx(35,21): error TS18047: 'result' is possibly 'null'.",
        ]),
        source="react-vite",
    )

    assert [finding.code for finding in findings] == [
        "turn-based-board-game-nullable-win-result-unguarded"
    ]
    assert findings[0].paths == ["src/App.tsx"]
    assert "if (result)" in findings[0].repair_hint


def test_runtime_findings_classify_stale_hook_test_end_game_api() -> None:
    findings = runtime_findings_for_output(
        "\n".join([
            "src/useGameState.test.ts(85,24): error TS2339: Property 'endGame' does not exist on type '{ stones: Stone[]; currentPlayer: StoneColor; lastMove: StonePosition | null; gameOver: boolean; winner: StoneColor | null; winningCells: StonePosition[] | null; makeMove: (row: number, col: number) => boolean; resetGame: () => void; getStoneAt: (row: number, col: number) => Stone | undefined; }'.",
            "src/useGameState.test.ts(104,24): error TS2339: Property 'endGame' does not exist on type '{ stones: Stone[]; currentPlayer: StoneColor; lastMove: StonePosition | null; gameOver: boolean; winner: StoneColor | null; winningCells: StonePosition[] | null; makeMove: (row: number, col: number) => boolean; resetGame: () => void; getStoneAt: (row: number, col: number) => Stone | undefined; }'.",
        ]),
        source="react-vite",
    )

    assert [finding.code for finding in findings] == [
        "turn-based-board-game-stale-hook-test-api"
    ]
    assert findings[0].paths == ["src/useGameState.test.ts"]
    assert "Do not leave tests calling `endGame`" in findings[0].repair_hint


def test_runtime_findings_classify_hook_winner_state_not_updated() -> None:
    findings = runtime_findings_for_output(
        "\n".join([
            "FAIL  src/hooks/useGameState.test.ts > useGameState > 游戏结束后禁止落子 > Given 已经出现胜者",
            "AssertionError: expected null to be 'black' // Object.is equality",
            "❯ src/hooks/useGameState.test.ts:130:37",
            "130|       expect(result.current.winner).toBe('black')",
        ]),
        source="react-vite",
    )

    assert "turn-based-board-game-hook-winner-state-not-updated" in [
        finding.code for finding in findings
    ]
    finding = next(
        item for item in findings if item.code == "turn-based-board-game-hook-winner-state-not-updated"
    )
    assert finding.paths == ["src/hooks/useGameState.test.ts"]
    assert "handleCellClick" in finding.repair_hint
    assert "win detection" in finding.repair_hint


def test_runtime_findings_classify_vitest_user_event_fake_timer_timeout() -> None:
    findings = runtime_findings_for_output(
        "\n".join([
            "FAIL  tests/movement.test.tsx > Keyboard controls - Arrow keys > arrow up changes direction to up",
            "Error: Test timed out in 5000ms.",
            "If this is a long-running test, pass a timeout value as the last argument or configure it globally with \"testTimeout\".",
            "❯ tests/movement.test.tsx:58:3",
        ]),
        source="react-vite",
    )

    assert "react-vite-user-event-fake-timer-timeout" in [
        finding.code for finding in findings
    ]
    finding = next(
        item for item in findings
        if item.code == "react-vite-user-event-fake-timer-timeout"
    )
    assert finding.paths == ["tests/movement.test.tsx"]
    assert "advanceTimers" in finding.repair_hint
    assert "act" in finding.repair_hint


def test_runtime_findings_classify_react_component_default_import_mismatch() -> None:
    findings = runtime_findings_for_output(
        "\n".join([
            "Error: Element type is invalid: expected a string (for built-in components)",
            "but got: undefined. You likely forgot to export your component from the file it's defined in,",
            "or you might have mixed up default and named imports.",
            "❯ tests/DirectionControl.integration.test.tsx:22:13",
        ]),
        source="react-vite",
    )

    assert "react-component-import-export-mismatch" in [
        finding.code for finding in findings
    ]
    finding = next(
        item for item in findings
        if item.code == "react-component-import-export-mismatch"
    )
    assert finding.paths == ["tests/DirectionControl.integration.test.tsx"]
    assert "default import" in finding.repair_hint
    assert "export default" in finding.repair_hint


def test_runtime_findings_classify_testing_library_left_from_right_opposite_direction() -> None:
    findings = runtime_findings_for_output(
        "\n".join([
            "FAIL  src/hooks/useGameController.test.tsx > useGameController > keyboard controls > responds to ArrowLeft key",
            "Error: expect(element).toHaveTextContent()",
            "",
            "Expected element to have text content:",
            "  left",
            "Received:",
            "  right",
            "❯ src/hooks/useGameController.test.tsx:162:47",
            "FAIL  src/hooks/useGameController.test.tsx > useGameController > 180-degree reversal prevention > rejects direction change from left to right",
        ]),
        source="react-vite",
    )

    finding = next(
        item for item in findings
        if item.code == "react-grid-invalid-opposite-direction-test"
    )
    assert finding.paths == ["src/hooks/useGameController.test.tsx"]
    assert "pressing left should be rejected" in finding.repair_hint


def test_runtime_findings_classify_generated_test_ambiguous_text_query() -> None:
    findings = runtime_findings_for_output(
        "\n".join([
            "FAIL  tests/App.test.tsx > App > initial state > shows initial score of 0",
            "TestingLibraryElementError: Found multiple elements with the text: /分数: 0/",
            "❯ tests/App.test.tsx:28:21",
        ]),
        source="react-vite",
    )

    finding = next(
        item for item in findings
        if item.code == "react-generated-test-ambiguous-text-query"
    )
    assert finding.stage == "generated-test-contract"
    assert finding.paths == ["tests/App.test.tsx"]
    assert "Anchor" in finding.repair_hint


def test_runtime_findings_classify_generated_test_brittle_long_timer_state() -> None:
    findings = runtime_findings_for_output(
        "\n".join([
            "FAIL  tests/App.test.tsx > App > game over > shows game over UI when game ends",
            "TestingLibraryElementError: Unable to find an element by: [data-testid=\"game-over\"]",
            "vi.advanceTimersByTime(TICK_INTERVAL * 11 + 50)",
            "❯ tests/App.test.tsx:165:21",
        ]),
        source="react-vite",
    )

    finding = next(
        item for item in findings
        if item.code == "react-generated-test-brittle-long-timer-state"
    )
    assert finding.stage == "generated-test-contract"
    assert finding.paths == ["tests/App.test.tsx"]
    assert "one timer tick per act" in finding.repair_hint


def test_runtime_findings_classify_testing_library_split_text_query() -> None:
    findings = runtime_findings_for_output(
        "\n".join([
            "TestingLibraryElementError: Unable to find an element with the text: /当前分数:\\s*0/.",
            "This could be because the text is broken up by multiple elements.",
            "❯ tests/App.test.tsx:12:19",
        ]),
        source="react-vite",
    )

    assert "react-testing-library-split-text-query" in [
        finding.code for finding in findings
    ]
    finding = next(
        item for item in findings
        if item.code == "react-testing-library-split-text-query"
    )
    assert finding.paths == ["tests/App.test.tsx"]
    assert "accessible" in finding.repair_hint
    assert "textContent" in finding.repair_hint


def test_runtime_findings_classify_missing_stable_start_control() -> None:
    findings = runtime_findings_for_output(
        "\n".join([
            'TestingLibraryElementError: Unable to find an element by: [data-testid="start-button"]',
            "<main>",
            "  Ready",
            "</main>",
            "❯ tests/DirectionControl.integration.test.tsx:152:31",
        ]),
        source="react-vite",
    )

    assert "react-vite-missing-stable-control-testid" in [
        finding.code for finding in findings
    ]
    finding = next(
        item for item in findings
        if item.code == "react-vite-missing-stable-control-testid"
    )
    assert finding.paths == ["tests/DirectionControl.integration.test.tsx"]
    assert "start-button" in finding.repair_hint
    assert "placeholder" in finding.repair_hint.lower()


def test_runtime_findings_classify_missing_stable_non_control_testid() -> None:
    findings = runtime_findings_for_output(
        "\n".join([
            'TestingLibraryElementError: Unable to find an element by: [data-testid="game-title"]',
            "<main>",
            "  <div data-testid=\"game-board\" />",
            "</main>",
            "❯ tests/DirectionControl.integration.test.tsx:114:21",
        ]),
        source="react-vite",
    )

    assert "react-vite-missing-stable-testid" in [
        finding.code for finding in findings
    ]
    finding = next(
        item for item in findings
        if item.code == "react-vite-missing-stable-testid"
    )
    assert finding.paths == ["tests/DirectionControl.integration.test.tsx"]
    assert "game-title" in finding.repair_hint
    assert "stable DOM contract" in finding.repair_hint


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


def test_runtime_findings_classify_jsx_inside_ts_test_file() -> None:
    findings = runtime_findings_for_output(
        "\n".join([
            "FAIL  tests/useGame.test.ts [ tests/useGame.test.ts ]",
            "Error: Transform failed with 1 error:",
            "/worktree/tests/useGame.test.ts:10:18: ERROR: Expected \">\" but found \"/\"",
            "  10 |        render(<App />);",
            "     |                    ^",
        ]),
        source="react-vite",
    )

    assert [finding.code for finding in findings] == [
        "react-vite-jsx-in-ts-test-file"
    ]
    assert findings[0].paths == ["tests/useGame.test.ts"]
    assert ".tsx" in findings[0].repair_hint


def test_runtime_findings_classify_typescript_jsx_inside_ts_test_file() -> None:
    findings = runtime_findings_for_output(
        "\n".join([
            "src/hooks/useGameState.test.ts(14,7): error TS1005: '>' expected.",
            "src/hooks/useGameState.test.ts(14,12): error TS1005: ')' expected.",
            "src/hooks/useGameState.test.ts(17,6): error TS1109: Expression expected.",
        ]),
        source="react-vite",
    )

    assert "react-vite-jsx-in-ts-test-file" in [finding.code for finding in findings]
    finding = next(item for item in findings if item.code == "react-vite-jsx-in-ts-test-file")
    assert finding.paths == ["src/hooks/useGameState.test.ts"]
    assert ".tsx" in finding.repair_hint


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
