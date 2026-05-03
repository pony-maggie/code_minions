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


def test_runtime_findings_classify_board_cell_class_contract_regression() -> None:
    findings = runtime_findings_for_output(
        "\n".join([
            "FAIL  tests/GameInteraction.test.tsx > Given 空棋盘，When 黑方点击一个空交叉点",
            "Error: expect(element).toHaveClass(\"black\")",
            "Expected the element to have class:",
            "  black",
            "Received:",
            "",
            "❯ tests/GameInteraction.test.tsx:15:16",
        ]),
        source="react-vite",
    )

    assert [finding.code for finding in findings] == [
        "react-board-cell-class-contract-regression"
    ]
    assert findings[0].paths == ["tests/GameInteraction.test.tsx"]
    assert "Preserve" in findings[0].repair_hint
    assert "black" in findings[0].repair_hint


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


def test_runtime_findings_classify_board_testid_contract_mismatch() -> None:
    findings = runtime_findings_for_output(
        "\n".join([
            "FAIL  src/App.test.tsx > App > renders the board centered on desktop",
            "TestingLibraryElementError: Unable to find an element by: [data-testid=\"gomoku-board\"]",
            '  <svg class="board-svg" data-testid="board-svg" viewBox="0 0 480 480">',
            " ❯ src/App.test.tsx:12:28",
        ]),
        source="react-vite",
    )

    assert [finding.code for finding in findings] == [
        "react-board-testid-contract-mismatch"
    ]
    assert findings[0].paths == ["src/App.test.tsx"]
    assert "gomoku-board" in findings[0].repair_hint
    assert "board-svg" in findings[0].repair_hint


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


def test_runtime_findings_classify_board_cell_data_stone_not_updated() -> None:
    findings = runtime_findings_for_output(
        "\n".join([
            "FAIL  src/App.test.tsx > Gomoku core interaction > should place black stone on first click and switch to white",
            "Error: expect(element).toHaveAttribute(\"data-stone\", \"black\")",
            "Expected the element to have attribute:",
            "  data-stone=\"black\"",
            "Received:",
            "  null",
            "❯ src/App.test.tsx:22:18",
        ]),
        source="react-vite",
    )

    assert [finding.code for finding in findings] == [
        "react-board-cell-data-stone-not-updated"
    ]
    assert findings[0].paths == ["src/App.test.tsx"]
    assert "cell.stone" in findings[0].repair_hint
    assert "data-stone" in findings[0].repair_hint


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


def test_runtime_findings_classify_hook_batched_turn_actions() -> None:
    findings = runtime_findings_for_output(
        "\n".join([
            "FAIL  src/__tests__/useGameState.test.ts > useGameState > 白方落子 > 白方点击空位置显示白子",
            "AssertionError: expected 'black' to be 'white' // Object.is equality",
            "Expected: \"white\"",
            "Received: \"black\"",
            " ❯ src/__tests__/useGameState.test.ts:63:42",
            "     61|         result.current.handleCellClick(6, 6); // 白方",
            "     62|       });",
            "     63|       expect(result.current.board[6][6]).toBe('white');",
        ]),
        source="react-vite",
    )

    assert [finding.code for finding in findings] == [
        "react-hook-batched-turn-actions-use-stale-state"
    ]
    assert findings[0].paths == ["src/__tests__/useGameState.test.ts"]
    assert "separate `act`" in findings[0].repair_hint
    assert "latest `result.current.handleCellClick`" in findings[0].repair_hint


def test_runtime_findings_classify_turn_based_winner_status_mismatch() -> None:
    findings = runtime_findings_for_output(
        "\n".join([
            "FAIL  tests/game.test.tsx > Game Win Detection > 白方纵向连续五子",
            "Error: expect(element).toHaveTextContent()",
            "Expected element to have text content:",
            "  白棋胜",
            "Received:",
            "  黑棋胜",
            "❯ tests/game.test.tsx:53:44",
        ]),
        source="react-vite",
    )

    assert [finding.code for finding in findings] == [
        "turn-based-board-game-winner-status-mismatch"
    ]
    assert findings[0].paths == ["tests/game.test.tsx"]
    assert "move sequence" in findings[0].repair_hint


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


def test_runtime_findings_classify_impossible_public_win_sequence() -> None:
    findings = runtime_findings_for_output(
        "\n".join([
            "FAIL  tests/GameResult.test.tsx > 五子棋胜负判定 > 横向五子 > Given 黑方横向连续五子",
            "Error: expect(element).toHaveTextContent()",
            "Expected element to have text content:",
            "  黑方胜利",
            "Received:",
            "  白方回合",
            "❯ tests/GameResult.test.tsx:34:52",
        ]),
        source="react-vite",
    )

    assert "turn-based-board-game-impossible-public-win-sequence" in [
        finding.code for finding in findings
    ]
    finding = next(
        f for f in findings if f.code == "turn-based-board-game-impossible-public-win-sequence"
    )
    assert finding.paths == ["tests/GameResult.test.tsx"]
    assert "9-click" in finding.repair_hint
    assert "pure helper" in finding.repair_hint


def test_runtime_findings_classify_white_win_sequence_that_gives_black_early_win() -> None:
    findings = runtime_findings_for_output(
        "\n".join([
            "FAIL  tests/winDetection.test.ts > makeMove with win/draw detection > sets winner when white makes 5-in-a-row vertically",
            "AssertionError: expected 'black' to be 'white' // Object.is equality",
            "Expected: \"white\"",
            "Received: \"black\"",
            "❯ tests/winDetection.test.ts:152:26",
        ]),
        source="react-vite",
    )

    assert [finding.code for finding in findings] == [
        "turn-based-board-game-invalid-white-win-sequence"
    ]
    assert findings[0].paths == ["tests/winDetection.test.ts"]
    assert "do not share one row" in findings[0].repair_hint


def test_runtime_findings_classify_hook_white_win_sequence_that_gives_black_early_win() -> None:
    findings = runtime_findings_for_output(
        "\n".join([
            "FAIL  src/__tests__/useGameState.test.ts > useGameState > win detection > detects diagonal win for white",
            "AssertionError: expected 'black' to be 'white' // Object.is equality",
            "Expected: \"white\"",
            "Received: \"black\"",
            "❯ src/__tests__/useGameState.test.ts:198:37",
            "  198|       expect(result.current.winner).toBe('white');",
        ]),
        source="react-vite",
    )

    assert [finding.code for finding in findings] == [
        "turn-based-board-game-invalid-white-win-sequence"
    ]
    assert findings[0].paths == ["src/__tests__/useGameState.test.ts"]
    assert "White wins through the public move/click API" in findings[0].repair_hint


def test_runtime_findings_classify_draw_test_created_accidental_win() -> None:
    findings = runtime_findings_for_output(
        "\n".join([
            "FAIL  tests/GameResult.test.tsx > 五子棋胜负判定 > 平局判定 > Given 棋盘已满且无人五连",
            "AssertionError: expected <div data-testid=\"winner-display\"></div> to be null",
            "Received:",
            "<div",
            "  data-testid=\"winner-display\"",
            ">",
            "  黑方",
            "</div>",
            "❯ tests/GameResult.test.tsx:201:29",
        ]),
        source="react-vite",
    )

    assert "turn-board-game-draw-test-created-accidental-win" in [
        finding.code for finding in findings
    ]
    finding = next(
        f for f in findings if f.code == "turn-board-game-draw-test-created-accidental-win"
    )
    assert finding.paths == ["tests/GameResult.test.tsx"]
    assert "sequentially filling" in finding.repair_hint
    assert "draw helper" in finding.repair_hint


def test_runtime_findings_classify_draw_helper_board_with_accidental_win() -> None:
    findings = runtime_findings_for_output(
        "\n".join([
            "FAIL  tests/game-logic.test.ts > getGameStatus > 棋盘已满且无人五连",
            "AssertionError: expected 'white-wins' to be 'draw' // Object.is equality",
            "Expected: \"draw\"",
            "Received: \"white-wins\"",
            "❯ tests/game-logic.test.ts:113:22",
        ]),
        source="react-vite",
    )

    assert [finding.code for finding in findings] == [
        "turn-board-game-draw-test-created-accidental-win"
    ]
    assert findings[0].paths == ["tests/game-logic.test.ts"]
    assert "proven no-five board" in findings[0].repair_hint


def test_runtime_findings_classify_missing_white_win_from_invalid_sequence() -> None:
    findings = runtime_findings_for_output(
        "\n".join([
            "FAIL  tests/App.test.tsx > 胜负判定与获胜棋子高亮 > 白方纵向五子获胜",
            "TestingLibraryElementError: Unable to find an element with the text: /白.*胜|白方.*赢/i.",
            "❯ tests/App.test.tsx:62:21",
        ]),
        source="react-vite",
    )

    assert "turn-based-board-game-invalid-white-win-sequence" in [
        finding.code for finding in findings
    ]
    finding = next(
        f for f in findings if f.code == "turn-based-board-game-invalid-white-win-sequence"
    )
    assert finding.paths == ["tests/App.test.tsx"]
    assert "five white target cells" in finding.repair_hint
    assert "turns 2/4/6/8/10" in finding.repair_hint


def test_runtime_findings_classify_missing_winner_display_from_invalid_sequence() -> None:
    findings = runtime_findings_for_output(
        "\n".join([
            "FAIL  tests/App.game.test.tsx > App 组件 - 胜负判定 > 白方获胜 - 纵向五子",
            'TestingLibraryElementError: Unable to find an element by: [data-testid="winner-display"]',
            "❯ tests/App.game.test.tsx:94:36",
            "     94|       const winnerDisplay = screen.getByTestId('winner-display');",
            "     95|       expect(winnerDisplay).toHaveTextContent('white');",
        ]),
        source="react-vite",
    )

    assert "turn-based-board-game-invalid-white-win-sequence" in [
        finding.code for finding in findings
    ]
    finding = next(
        f for f in findings if f.code == "turn-based-board-game-invalid-white-win-sequence"
    )
    assert finding.paths == ["tests/App.game.test.tsx"]
    assert "turns 2/4/6/8/10" in finding.repair_hint


def test_runtime_findings_classify_draw_text_missing_after_public_board_fill() -> None:
    findings = runtime_findings_for_output(
        "\n".join([
            "FAIL  tests/App.test.tsx > 胜负判定与获胜棋子高亮 > 平局判定",
            "TestingLibraryElementError: Unable to find an element with the text: /平.*局|平手/i.",
            "          class=\"cell occupied winning\"",
            "❯ tests/App.test.tsx:246:21",
        ]),
        source="react-vite",
    )

    assert "turn-board-game-draw-test-created-accidental-win" in [
        finding.code for finding in findings
    ]
    finding = next(
        f for f in findings if f.code == "turn-board-game-draw-test-created-accidental-win"
    )
    assert finding.paths == ["tests/App.test.tsx"]
    assert "draw helper" in finding.repair_hint


def test_runtime_findings_classify_draw_display_missing_after_accidental_win() -> None:
    findings = runtime_findings_for_output(
        "\n".join([
            "FAIL  tests/App.game.test.tsx > App 组件 - 胜负判定 > 平局判定",
            'TestingLibraryElementError: Unable to find an element by: [data-testid="draw-display"]',
            '          data-testid="winner-display"',
            "        winner: black",
            "❯ tests/App.game.test.tsx:217:34",
            "    217|       const drawDisplay = screen.getByTestId('draw-display');",
        ]),
        source="react-vite",
    )

    assert "turn-board-game-draw-test-created-accidental-win" in [
        finding.code for finding in findings
    ]
    finding = next(
        f for f in findings if f.code == "turn-board-game-draw-test-created-accidental-win"
    )
    assert finding.paths == ["tests/App.game.test.tsx"]
    assert "draw helper" in finding.repair_hint


def test_runtime_findings_classify_winner_state_not_updated() -> None:
    findings = runtime_findings_for_output(
        "\n".join([
            "FAIL  tests/UndoRedo.test.tsx > Undo and Restart functionality > AC3: 游戏已经结束，用户点击悔棋",
            "Error: expect(element).toHaveTextContent()",
            "Expected element to have text content:",
            "  游戏结束，黑方获胜",
            "Received:",
            "  当前回合: 白方",
            "❯ tests/UndoRedo.test.tsx:76:45",
        ]),
        source="react-vite",
    )

    assert [finding.code for finding in findings] == [
        "turn-based-board-game-winner-state-not-updated"
    ]
    assert findings[0].paths == ["tests/UndoRedo.test.tsx"]
    assert "setWinner" in findings[0].repair_hint
    assert "placeStone" in findings[0].repair_hint


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


def test_runtime_findings_classify_presentational_board_test_expects_game_state() -> None:
    findings = runtime_findings_for_output(
        "\n".join([
            "FAIL  src/components/Board.test.tsx > Board > Black horizontal win > declares winner",
            "TestingLibraryElementError: Unable to find an element with the text: /黑方获胜/i.",
            " ❯ src/components/Board.test.tsx:37:33",
            "     35|       render(<Board board={board} onCellClick={() => {}} />)",
            "     36|",
            "     37|       const winnerText = screen.getByText(/黑方获胜/i)",
        ]),
        source="react-vite",
    )

    assert [finding.code for finding in findings] == [
        "react-presentational-board-test-expects-game-state"
    ]
    assert findings[0].paths == ["src/components/Board.test.tsx"]
    assert "App" in findings[0].repair_hint
    assert "winningCells" in findings[0].repair_hint


def test_runtime_findings_classify_duplicate_rendered_board_cells() -> None:
    findings = runtime_findings_for_output(
        "\n".join([
            "FAIL  tests/App.game.test.tsx > App 组件 - 胜负判定 > 黑方获胜",
            'TestingLibraryElementError: Found multiple elements by: [data-testid="cell-7-3"]',
            "     28|       render(<App />);",
            "     35|       render(<App />);",
            "❯ tests/App.game.test.tsx:39:25",
        ]),
        source="react-vite",
    )

    assert "react-testing-library-duplicate-render-without-cleanup" in [
        finding.code for finding in findings
    ]
    finding = next(
        f for f in findings if f.code == "react-testing-library-duplicate-render-without-cleanup"
    )
    assert finding.paths == ["tests/App.game.test.tsx"]
    assert "unmount" in finding.repair_hint


def test_runtime_findings_classify_board_fill_timeout() -> None:
    findings = runtime_findings_for_output(
        "\n".join([
            "FAIL  tests/App.test.tsx > 五子棋游戏 - 胜负判定 > 棋盘已满且无人五连，显示平局",
            "Error: Test timed out in 5000ms.",
            "If this is a long-running test, pass a timeout value as the last argument or configure it globally with \"testTimeout\".",
            "❯ tests/App.test.tsx:133:44",
        ]),
        source="react-vite",
    )

    assert [finding.code for finding in findings] == [
        "turn-board-game-board-fill-test-timeout"
    ]
    assert findings[0].paths == ["tests/App.test.tsx"]
    assert "225" in findings[0].repair_hint


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
