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
    issues: list[dict[str, Any]],
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
            paths=[str(path) for path in issue.get("paths") or []],
        ))
    return findings


_TSC_DIAGNOSTIC_RE = re.compile(
    r"(?P<path>[^\s()]+\.(?:ts|tsx))\(\d+,\d+\): error TS(?P<ts_code>\d+): (?P<message>.+)"
)
_TSC_MISSING_NAME_RE = re.compile(r"Cannot find name '(?P<name>[^']+)'")
_VITEST_FRAME_PATH_RE = re.compile(
    r"❯\s+(?:\S+\s+)?(?P<path>[^\s:]+\.(?:ts|tsx)):\d+:\d+"
)
REACT_HOOK_NAMES = {
    "useCallback",
    "useContext",
    "useEffect",
    "useId",
    "useImperativeHandle",
    "useLayoutEffect",
    "useMemo",
    "useReducer",
    "useRef",
    "useState",
    "useTransition",
}


def _typescript_runtime_findings(output: str, *, source: str) -> list[GateFinding]:
    findings: list[GateFinding] = []
    seen: set[tuple[str, str]] = set()
    react_hook_imports: dict[str, set[str]] = {}
    react_hook_order: list[str] = []
    implicit_any_paths: list[str] = []
    for match in _TSC_DIAGNOSTIC_RE.finditer(output):
        path = match.group("path")
        ts_code = match.group("ts_code")
        message = match.group("message").strip()
        lowered = message.lower()

        if (
            path.endswith(".test.ts")
            and ts_code in {"1005", "1109"}
            and (
                "'>' expected" in output
                or '">" expected' in output
                or "Expected \">\"" in output
            )
        ):
            code = "react-vite-jsx-in-ts-test-file"
            repair_hint = (
                f"Rename `{path}` to use a `.tsx` extension, or remove JSX from that test file. "
                "React Testing Library tests that render JSX such as `<App />` must live in "
                "`*.test.tsx` so TypeScript/Vite parse JSX correctly."
            )
        elif ts_code == "2304" and "cannot find name" in lowered:
            missing_name_match = _TSC_MISSING_NAME_RE.search(message)
            missing_name = missing_name_match.group("name") if missing_name_match else ""
            if missing_name in REACT_HOOK_NAMES:
                if path not in react_hook_imports:
                    react_hook_imports[path] = set()
                    react_hook_order.append(path)
                react_hook_imports[path].add(missing_name)
                continue

            code = "typescript-missing-symbol"
            repair_hint = (
                f"Define or import `{missing_name}` before using it, or remove the stale export/use site. "
                "Do not leave orphan symbols after splitting implementation across files."
            )
        elif ts_code == "2459" or (
            "declares" in lowered and "locally" in lowered and "not exported" in lowered
        ):
            code = "typescript-local-declaration-not-exported"
            repair_hint = (
                "Import shared types/constants from their canonical source module, or explicitly re-export "
                "them from the module callers already use. Do not import a locally imported symbol as if "
                "it were exported by that intermediate module."
            )
        elif (
            ts_code == "2339"
            and "property 'user' does not exist" in lowered
            and "@testing-library/user-event" in message
        ):
            code = "testing-library-user-event-import-mismatch"
            repair_hint = (
                "`@testing-library/user-event` exposes the userEvent object as the default export. "
                "Use `import userEvent from '@testing-library/user-event'` or "
                "`const user = (await import('@testing-library/user-event')).default`; do not "
                "destructure a non-existent `user` property."
            )
        elif (
            ts_code == "2339"
            and "property 'endgame' does not exist" in lowered
            and "usegamestate" in path.lower()
            and path.endswith((".test.ts", ".test.tsx"))
        ):
            code = "turn-based-board-game-stale-hook-test-api"
            repair_hint = (
                "The hook contract exposes `makeMove`, `resetGame`, winner/game-over state, and query "
                "helpers, but not a test-only `endGame` mutator. Do not leave tests calling `endGame`; "
                "either drive game-over through valid `makeMove` sequences or add a real exported hook "
                "API and update implementation and tests together."
            )
        elif (
            ts_code == "18047"
            and "possibly 'null'" in lowered
            and (
                "'result'" in lowered
                or any(name in output for name in ("checkWin", "winningCells", "winner", "gameOver"))
            )
        ):
            code = "turn-based-board-game-nullable-win-result-unguarded"
            repair_hint = (
                "Win-check helpers often return a result object or `null` when no winner exists. Guard "
                "before reading result fields, for example `const result = checkWin(...); if (result) { "
                "setWinner(result.winner); setWinningCells(result.winningCells); }`, and keep normal "
                "turn switching in the no-win branch."
            )
        elif ts_code == "7006" and "implicitly has an 'any' type" in message:
            if path not in implicit_any_paths:
                implicit_any_paths.append(path)
            continue
        elif (
            ts_code == "2307"
            and "cannot find module" in lowered
            and ".module.css" in lowered
            and "corresponding type declarations" in lowered
        ):
            code = "react-vite-css-module-types-missing"
            repair_hint = (
                "React/Vite TypeScript projects that import CSS modules need Vite client ambient types. "
                "Add `src/vite-env.d.ts` containing `/// <reference types=\"vite/client\" />`, or add an "
                "equivalent `declare module '*.module.css'` type declaration included by `tsconfig.json`."
            )
        elif ts_code == "2739" and "missing the following properties from type" in lowered:
            code = "typescript-jsx-missing-required-props"
            repair_hint = (
                "Pass the required props at the JSX call site or update the component props "
                "contract to match its callers. When a child component gains required props, "
                "update the parent state and handlers in the same change."
            )
        elif (
            ts_code in {"2345", "2367"}
            and '"empty"' in output
            and '"black" | "white"' in output
            and ("stonecolor" in lowered or "no overlap" in lowered)
        ):
            code = "turn-board-cell-state-player-type-mismatch"
            repair_hint = (
                "Separate playable stone/player types from empty board-cell state. Use a `Player` "
                "or `Stone` type of `'black' | 'white'`, and a distinct `CellState` type such as "
                "`Player | 'empty'` or `Player | null`. Winner/check functions should accept only "
                "the playable `Player` type, while board arrays should use `CellState`."
            )
        elif (
            ts_code == "2322"
            and re.search(r"""Type ['"]"?(?:black|white)"?['"] is not assignable to type ['"]null['"]""", message)
        ):
            code = "react-board-test-null-array-inference"
            repair_hint = (
                "Do not type test-board factories as `null[][]` or `(null)[][]` when later assigning "
                "`'black'` or `'white'`. Import the canonical board type, for example "
                "`import type { Board as BoardState } from '../types'`, return `BoardState`, and keep "
                "the `Array.from(..., () => null)` initializer typed through that shared contract."
            )
        elif ts_code in {"2322", "2345"} and "not assignable to" in lowered:
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

        key_path = "" if code == "turn-board-cell-state-player-type-mismatch" else path
        key = (code, key_path)
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
    for path in react_hook_order:
        hooks = sorted(react_hook_imports[path])
        named_import = ", ".join(hooks)
        findings.append(GateFinding(
            code="typescript-missing-react-hook-import",
            severity="error",
            stage="runtime",
            message=(
                f"TypeScript TS2304: `{path}` uses React hook(s) {named_import} "
                "without importing them."
            ),
            repair_hint=(
                f"Add `import {{ {named_import} }} from 'react'` at the top of `{path}` "
                "or remove the hook usage if it is stale."
            ),
            source=source,
            paths=[path],
        ))
    for path in implicit_any_paths:
        findings.append(GateFinding(
            code="typescript-implicit-any",
            severity="error",
            stage="runtime",
            message=f"TypeScript TS7006: `{path}` has callback parameters with implicit `any` types.",
            repair_hint=(
                "Annotate callback parameters or preserve typed arrays/props from the shared domain "
                "types so TypeScript can infer row, cell, index, and state callback parameter types."
            ),
            source=source,
            paths=[path],
        ))
    return findings


def _first_vitest_frame_path(output: str) -> str:
    match = _VITEST_FRAME_PATH_RE.search(output)
    return match.group("path") if match else ""


def _vitest_frame_path_containing(output: str, needle: str) -> str:
    for match in _VITEST_FRAME_PATH_RE.finditer(output):
        path = match.group("path")
        if needle in path:
            return path
    return ""


def _relative_display_path(path: str) -> str:
    normalized = path.replace("\\", "/")
    for marker in ("/src/", "/tests/"):
        if marker in normalized:
            return f"{marker.strip('/')}/{normalized.split(marker, 1)[1]}"
    return normalized.lstrip("./")


def _react_runtime_findings(output: str, *, source: str) -> list[GateFinding]:
    findings: list[GateFinding] = []
    path = _first_vitest_frame_path(output)
    paths = [path] if path else []
    if (
        "Unable to find an element by:" in output
        and "reset-button" in output
        and ("getByTestId('reset-button')" in output or 'data-testid="reset-button"' in output)
    ):
        reset_path = _vitest_frame_path_containing(output, "App") or path
        findings.append(GateFinding(
            code="react-vite-missing-stable-control-testid",
            severity="error",
            stage="runtime",
            message="A React test expected a stable control test id that is missing from the rendered app.",
            repair_hint=(
                "Preserve existing public test contracts when adding features. Render the reset/restart "
                "control with `data-testid=\"reset-button\"` (`reset-button`) if tests already use it, or update all tests "
                "and UI together to a single stable selector. Do not remove earlier task controls while "
                "implementing win/draw logic."
            ),
            source=source,
            paths=[reset_path] if reset_path else paths,
        ))

    if (
        "expected undefined to be null" in output
        and "lastMove" in output
        and "toBeNull()" in output
    ):
        last_move_path = _vitest_frame_path_containing(output, "useGameState") or path
        findings.append(GateFinding(
            code="turn-based-board-game-last-move-reset-contract",
            severity="error",
            stage="runtime",
            message="Game-state reset returned `undefined` for last move where tests expect `null`.",
            repair_hint=(
                "Keep the hook state contract explicit: initialize and reset `lastMove` to `null`, not "
                "`undefined`, and expose it consistently as `Position | null`. If the name changed during "
                "a refactor, update both the hook return value and tests in the same task."
            ),
            source=source,
            paths=[last_move_path] if last_move_path else paths,
        ))

    jsx_in_ts_match = re.search(
        r"(?P<path>[^\s]+\.test\.ts):\d+:\d+:\s+ERROR:\s+Expected [\"']?>[\"']? but found [\"']?/[\"']?",
        output,
    )
    if jsx_in_ts_match and "Transform failed" in output:
        test_path = _relative_display_path(jsx_in_ts_match.group("path"))
        findings.append(GateFinding(
            code="react-vite-jsx-in-ts-test-file",
            severity="error",
            stage="runtime",
            message=f"Vite/esbuild parsed JSX inside TypeScript test file `{test_path}`.",
            repair_hint=(
                f"Rename `{test_path}` to use a `.tsx` extension, or remove JSX from that test file. "
                "React Testing Library tests that call `render(<App />)` must live in `*.test.tsx` "
                "so Vite parses JSX correctly."
            ),
            source=source,
            paths=[test_path],
        ))
    missing_function_match = re.search(
        r"(?:__vite_ssr_import_\d+__\.)?(?P<name>[A-Za-z_$][\w$]*) is not a function",
        output,
    )

    if missing_function_match:
        name = missing_function_match.group("name")
        findings.append(GateFinding(
            code="react-runtime-missing-named-function-export",
            severity="error",
            stage="runtime",
            message=f"React runtime called `{name}`, but the imported value is not a function.",
            repair_hint=(
                f"Trace the import for `{name}` from the failing component/test and make the source module "
                f"export a real `{name}` function, or update the import/use site to the function that actually "
                "exists. Do not leave a named import that TypeScript/Vitest can load as undefined at runtime."
            ),
            source=source,
            paths=paths,
        ))

    if (
        "received value must be an HTMLElement or an SVGElement" in output
        and "aria-live" in output
        and "assertive" in output
    ):
        findings.append(GateFinding(
            code="react-a11y-live-region-missing",
            severity="error",
            stage="runtime",
            message="A React accessibility test expected an assertive live region but the selector returned null.",
            repair_hint=(
                "Render a stable screen-reader live region with `aria-live=\"assertive\"` for status "
                "announcements, including both in-progress turn changes and game-over results. Keep the "
                "selector present even when the message text changes."
            ),
            source=source,
            paths=paths,
        ))

    if (
        "received value must be an HTMLElement or an SVGElement" in output
        and "querySelector(" in output
    ):
        findings.append(GateFinding(
            code="react-testing-library-null-element-assertion",
            severity="error",
            stage="runtime",
            message=(
                "React Testing Library expected an element but a selector returned null."
            ),
            repair_hint=(
                "Verify the product renders the asserted element/class, especially win-state props such "
                "as `winningCells` being passed from App/state hooks into Board/Cell. If the product uses "
                "a different stable DOM contract, update the test selector to match the rendered markup."
            ),
            source=source,
            paths=paths,
        ))

    if (
        "expect(element).toHaveClass" in output
        and "Expected the element to have class:" in output
        and any(player_class in output for player_class in ("black", "white"))
    ):
        findings.append(GateFinding(
            code="react-board-cell-class-contract-regression",
            severity="error",
            stage="runtime",
            message="A board interaction test expected the clicked cell to keep its player class.",
            repair_hint=(
                "Preserve previously passing board-cell DOM contracts across later tasks. If tests "
                "assert clicked cells have `black`/`white` classes, keep those classes on the stable "
                "cell element while adding win-state classes such as `winning`; do not move the only "
                "player marker to a child element unless all existing tests are updated consistently."
            ),
            source=source,
            paths=paths,
        ))

    if (
        "expect(element).toHaveClass" in output
        and "Expected the element to have class:" in output
        and re.search(r"(?m)^\s*board\s*$", output)
        and "board-container" in output
    ):
        findings.append(GateFinding(
            code="react-board-root-class-contract-mismatch",
            severity="error",
            stage="runtime",
            message="A board rendering test expected the board root element to have class `board`.",
            repair_hint=(
                "Keep the board root DOM contract consistent. Put `data-testid=\"board\"` and class "
                "`board` on the actual grid element, and use a separate parent wrapper with "
                "`board-container` when needed for centering/responsive layout. Do not put the board test id "
                "only on the container if tests assert the grid has class `board`."
            ),
            source=source,
            paths=paths,
        ))

    if (
        "gomoku-board" in output
        and "board-svg" in output
    ):
        findings.append(GateFinding(
            code="react-board-testid-contract-mismatch",
            severity="error",
            stage="runtime",
            message="A board test expected a different stable test id than the rendered board uses.",
            repair_hint=(
                "Align the board test id contract. Either render `data-testid=\"gomoku-board\"` on the "
                "interactive board/root element that tests expect, or update tests to use the existing "
                "`board-svg`/cell test ids consistently. Keep one stable id for the board root across App "
                "and Board tests."
            ),
            source=source,
            paths=paths,
        ))

    if (
        "Found multiple elements with the role" in output
        and "button" in output
        and "空" in output
        and "cell-0-0" in output
    ):
        findings.append(GateFinding(
            code="react-board-empty-cell-query-too-broad",
            severity="error",
            stage="runtime",
            message="A board test queried empty cells with a broad accessible-name pattern.",
            repair_hint=(
                "Use `getAllByRole('button', { name: /, 空$/ })` only when asserting the empty-cell count. "
                "For one cell, query an exact accessible name such as `行1列1, 空` or a stable test id such "
                "as `cell-0-0`; broad empty-cell role queries match many board cells."
            ),
            source=source,
            paths=paths,
        ))

    if (
        "Unable to find an accessible element" in output
        and 'role "button"' in output
        and "行0列0" in output
        and "行1列1, 空" in output
    ):
        findings.append(GateFinding(
            code="react-board-coordinate-accessible-name-mismatch",
            severity="error",
            stage="runtime",
            message="A board test used a coordinate accessible name that does not match rendered cells.",
            repair_hint=(
                "Keep board coordinate accessible names consistent. User-facing aria labels should usually "
                "be 1-based and include the state suffix, for example `行1列1, 空`; do not query "
                "0-based labels such as `行0列0` unless the component actually renders that exact contract."
            ),
            source=source,
            paths=paths,
        ))

    if (
        "expect(element).toHaveAttribute" in output
        and "data-stone=" in output
        and "Received:" in output
        and re.search(r"(?m)^\s*null\s*$", output)
        and any(stone in output for stone in ('data-stone="black"', 'data-stone="white"'))
    ):
        findings.append(GateFinding(
            code="react-board-cell-data-stone-not-updated",
            severity="error",
            stage="runtime",
            message="A board interaction test expected the clicked cell data-stone attribute to update.",
            repair_hint=(
                "Preserve the stable board-cell DOM contract and update the clicked cell element with "
                "`data-stone=\"black\"` or `data-stone=\"white\"` after moves. If board cells are objects "
                "such as `{ stone, isLastMove }`, check occupancy with `cell.stone !== null` rather than "
                "`cell !== null`; otherwise empty object cells will block every move before state updates."
            ),
            source=source,
            paths=paths,
        ))

    if (
        "getCellWithStone" in output
        and "getByRole" in output
        and "data-stone" in output
        and 'aria-label="行' in output
        and "空" in output
        and any(stone in output for stone in ("黑子", "白子", "black", "white"))
    ):
        findings.append(GateFinding(
            code="react-board-cell-accessible-state-not-updated",
            severity="error",
            stage="runtime",
            message="A board test could not find the moved stone by its accessible cell state.",
            repair_hint=(
                "Keep every board cell's rendered stone, `data-stone`, and `aria-label` derived from the "
                "same board state after each move. A cell containing black should expose a stable state "
                "such as `data-stone=\"black\"` and `aria-label=\"行1列1, 黑子\"`; white cells should "
                "likewise announce `白子`, while only truly empty cells announce `空`."
            ),
            source=source,
            paths=paths,
        ))

    if (
        "Board.test.tsx" in output
        and "expected \"spy\" to not be called at all" in output
        and "actually been called" in output
        and "onCellClick" in output
    ):
        board_path = _vitest_frame_path_containing(output, "Board.test")
        findings.append(GateFinding(
            code="react-presentational-board-occupied-click-contract",
            severity="error",
            stage="runtime",
            message=(
                "A presentational Board test expected occupied cells to suppress the click callback."
            ),
            repair_hint=(
                "Keep ownership clear between presentational Board and App/game-state logic. If Board is "
                "only a controlled/presentational grid, test duplicate/occupied move prevention through "
                "App or the game-state hook, not by expecting Board to know the game rules. If Board owns "
                "that contract, explicitly disable occupied cells or guard `onCellClick` when `cell !== null`."
            ),
            source=source,
            paths=[board_path] if board_path else paths,
        ))

    if (
        "useGameState.test" in output
        and "winner" in output
        and "expected null to be 'black'" in output
    ):
        hook_path = _vitest_frame_path_containing(output, "useGameState.test")
        findings.append(GateFinding(
            code="turn-based-board-game-hook-winner-state-not-updated",
            severity="error",
            stage="runtime",
            message="A game-state hook test expected a winner after a five-in-row move sequence but `winner` stayed null.",
            repair_hint=(
                "If this task owns win/game-over behavior, wire win detection into `handleCellClick` or the "
                "shared move reducer: evaluate the board after placing the latest stone, set `winner`, and "
                "ignore later moves while `winner` is set. If the current task is only core turn management "
                "and win detection belongs to a later task, move this assertion to the win-detection tests "
                "instead of requiring `winner` before that behavior exists."
            ),
            source=source,
            paths=[hook_path] if hook_path else paths,
        ))

    if (
        "useGameState.test" in output
        and "handleCellClick" in output
        and (
            "expected 'black' to be 'white'" in output
            or "expected 'white' to be 'black'" in output
            or "expected null to be 'black'" in output
        )
    ):
        hook_path = _vitest_frame_path_containing(output, "useGameState.test")
        findings.append(GateFinding(
            code="react-hook-batched-turn-actions-use-stale-state",
            severity="error",
            stage="runtime",
            message="A hook test appears to call multiple turn-changing actions inside one stale render snapshot.",
            repair_hint=(
                "In renderHook tests for turn-based React state, perform each move in a separate `act` call "
                "and call the latest `result.current.handleCellClick` after React has applied the previous "
                "state update. If the product intentionally supports multiple programmatic moves in one "
                "transaction, implement `handleCellClick` with functional state updates; otherwise prefer "
                "public App click tests for alternating-turn workflows."
            ),
            source=source,
            paths=[hook_path] if hook_path else paths,
        ))

    if (
        "expect(element).toHaveTextContent()" in output
        and "Expected element to have text content:" in output
        and "Received:" in output
        and (
            re.search(r"(?m)^\s*/(?:空\$|黑|白)/\s*$", output)
            or re.search(r"(?m)^\s*(?:空|黑子|白子)\s*$", output)
        )
        and ("●" in output or "Received:\n" in output)
    ):
        findings.append(GateFinding(
            code="react-board-cell-text-accessible-name-mismatch",
            severity="error",
            stage="runtime",
            message="A board-cell test asserted visible text that the rendered cell exposes through aria-label.",
            repair_hint=(
                "Keep board-cell tests aligned with the DOM contract. If the cell uses graphical stones "
                "and exposes state via aria-label, assert the accessible name or stable classes instead "
                "of `toHaveTextContent(/空|黑|白/)`; alternatively render matching visible text if that is "
                "the intended product contract."
            ),
            source=source,
            paths=paths,
        ))

    if "落子历史" in output and "getByText" in output:
        findings.append(GateFinding(
            code="react-status-panel-history-section-missing",
            severity="error",
            stage="runtime",
            message="Status panel tests expected a move-history section, but the rendered text was missing.",
            repair_hint=(
                "Render a stable `落子历史` section when the status panel feature requires recent move "
                "history, or update the test only if the accepted DOM contract intentionally uses a different "
                "visible label."
            ),
            source=source,
            paths=paths,
        ))

    if (
        "AssertionError: expected" in output
        and "to contain" in output
        and any(term in output for term in ("黑子", "白子"))
        and any(term in output for term in ("黑方", "白方"))
    ):
        findings.append(GateFinding(
            code="react-status-panel-label-contract-mismatch",
            severity="error",
            stage="runtime",
            message="Status panel rendered player/result text with terminology that does not match its tests.",
            repair_hint=(
                "Use the same player terminology across visible status text, live-region announcements, "
                "and tests. For this Gomoku UI, do not mix `黑方/白方` with `黑子/白子` unless tests and "
                "product copy are updated together."
            ),
            source=source,
            paths=paths,
        ))

    if (
        (
            any(pattern in output for pattern in (
                "getByText('当前回合:",
                'getByText("当前回合:',
                "getByText(/当前回合:",
            ))
            or (
                "expect(element).toHaveTextContent()" in output
                and "Expected element to have text content:" in output
                and any(term in output for term in ("黑方获胜", "白方获胜", "黑棋胜", "白棋胜"))
                and "Received:" in output
                and "当前回合" in output
                and any(term in output for term in (
                    "核心落子交互",
                    "已存在游戏结束状态",
                    "游戏结束后禁止继续落子",
                    "已经出现胜者，When 用户继续点击棋盘",
                ))
            )
        )
        and any(term in output for term in ("data-testid=\"game-status\"", "当前回合", "核心落子交互", "已存在游戏结束状态"))
    ):
        findings.append(GateFinding(
            code="turn-based-board-game-current-turn-status-contract-drift",
            severity="error",
            stage="runtime",
            message="Turn-based board game current-turn status text drifted from an earlier test contract.",
            repair_hint=(
                "Preserve the existing visible current-turn contract across tasks. If earlier tests assert "
                "`当前回合: 黑子` or `当前回合: 白子`, keep rendering that text for in-progress turns and add "
                "winner/draw text only for ended games, or update the UI and all existing tests consistently "
                "in the same task. In core move/turn tasks, defer tests that construct a five-in-row game-over "
                "sequence; remove them and leave win/game-over coverage to the win-detection task."
            ),
            source=source,
            paths=paths,
        ))

    if (
        "expect(element).toHaveTextContent()" in output
        and "Expected element to have text content:" in output
        and "Received:" in output
        and any(term in output for term in ("黑方胜利", "白方胜利"))
        and any(term in output for term in ("黑方回合", "白方回合"))
    ):
        findings.append(GateFinding(
            code="turn-based-board-game-impossible-public-win-sequence",
            severity="error",
            stage="runtime",
            message="A public UI test expected a win from an impossible same-player move sequence.",
            repair_hint=(
                "Public board clicks alternate players. Do not test a Gomoku win by clicking the target "
                "player's five cells consecutively through the UI. Use a valid 9-click black-win sequence "
                "such as black target cells `(0,0)..(0,4)` on turns 1/3/5/7/9 with white filler moves far "
                "away, or use a pure helper/state setup when testing same-player board geometry directly."
            ),
            source=source,
            paths=paths,
        ))

    if (
        "Unable to find an element with the text:" in output
        and any(pattern in output for pattern in ("/白.*胜|白方.*赢/i", "白方胜利", "白方获胜"))
        and any(term in output for term in ("五子棋", "Gomoku", "胜负判定", "棋子"))
    ):
        findings.append(GateFinding(
            code="turn-based-board-game-invalid-white-win-sequence",
            severity="error",
            stage="runtime",
            message="A Gomoku test expected a white win, but the public move sequence did not produce one.",
            repair_hint=(
                "White wins through the public click API require five white target cells, all played on "
                "turns 2/4/6/8/10. Do not count black's first move as part of the white target line, and "
                "do not stop after only four white stones. Use black filler moves that do not share one row, "
                "one column, or one diagonal, for example black `(10,10)`, `(11,12)`, `(12,14)`, `(13,11)`, "
                "`(14,13)` and white target `(0,5)..(4,5)` for a vertical win."
            ),
            source=source,
            paths=paths,
        ))

    if (
        "expected 'black' to be 'white'" in output
        and (
            "white makes 5-in-a-row" in output
            or "White player wins" in output
            or "win for white" in output
            or "winner).toBe('white')" in output
            or "白方" in output
        )
        and any(term in output for term in ("winner", "获胜", "win/draw", "Gomoku", "五子棋"))
    ):
        findings.append(GateFinding(
            code="turn-based-board-game-invalid-white-win-sequence",
            severity="error",
            stage="runtime",
            message="A Gomoku white-win test accidentally produced an earlier black win.",
            repair_hint=(
                "White wins through the public move/click API require five white target cells on turns "
                "2/4/6/8/10. Black filler moves must not share one row, one column, or one diagonal; do not share one row/column/diagonal, otherwise "
                "black can win on turn 9 before white's fifth move. Use fillers like `(10,10)`, `(11,12)`, "
                "`(12,14)`, `(13,11)`, `(14,13)` with white target `(0,5)..(4,5)`, or test same-color "
                "geometry through a pure board helper instead of the turn-advancing API."
            ),
            source=source,
            paths=paths,
        ))

    if (
        "to be null" in output
        and "data-testid=\"winner-display\"" in output
        and any(term in output for term in ("棋盘已满", "平局", "draw"))
        and any(term in output for term in ("黑方", "白方", "black", "white"))
    ):
        findings.append(GateFinding(
            code="turn-board-game-draw-test-created-accidental-win",
            severity="error",
            stage="runtime",
            message="A full-board draw test produced a winner before the board was filled.",
            repair_hint=(
                "Avoid sequentially filling a Gomoku board through the public click API for draw tests; "
                "naive row-major or alternating patterns usually create five-in-a-row before the board is "
                "full. Test draw detection with a pure draw helper/state setup or a proven no-five board "
                "pattern, and assert the product's visible `平局` contract rather than assuming the "
                "`winner-display` element must be absent."
            ),
            source=source,
            paths=paths,
        ))

    if (
        "Unable to find an element with the text:" in output
        and any(pattern in output for pattern in ("/平.*局|平手/i", "平局"))
        and ("cell occupied winning" in output or "棋盘已满" in output or "draw" in output)
    ):
        findings.append(GateFinding(
            code="turn-board-game-draw-test-created-accidental-win",
            severity="error",
            stage="runtime",
            message="A full-board draw test could not find draw text after public board filling.",
            repair_hint=(
                "Avoid sequentially filling a Gomoku board through the public click API for draw tests; "
                "naive row-major or alternating patterns usually create five-in-a-row before the board is "
                "full. Test draw detection with a pure draw helper/state setup or a proven no-five board "
                "pattern, and assert the product's visible `平局` contract after that deterministic setup."
            ),
            source=source,
            paths=paths,
        ))

    if (
        any(pattern in output for pattern in ("getByText('平局')", 'getByText("平局")'))
        and 'data-winning="true"' in output
        and any(term in output for term in ("棋盘已满", "平局", "draw"))
    ):
        findings.append(GateFinding(
            code="turn-board-game-draw-test-created-accidental-win",
            severity="error",
            stage="runtime",
            message="A full-board draw UI test rendered a winning board before finding draw text.",
            repair_hint=(
                "Do not let a full-board Gomoku draw UI test block the MVP. Omit automated draw tests "
                "for this workflow unless the implementation already exposes a simple pure helper or "
                "deterministic no-five board state; keep win detection coverage to one lightweight "
                "acceptance-level smoke test."
            ),
            source=source,
            paths=paths,
        ))

    if (
        "expected 'white-wins' to be 'draw'" in output
        or "expected 'black-wins' to be 'draw'" in output
        or "expected 'white' to be 'draw'" in output
        or "expected 'black' to be 'draw'" in output
    ) and any(term in output for term in ("棋盘已满", "draw", "getGameStatus", "isBoardFull")):
        findings.append(GateFinding(
            code="turn-board-game-draw-test-created-accidental-win",
            severity="error",
            stage="runtime",
            message="A draw helper test used a full board pattern that still contains five-in-a-row.",
            repair_hint=(
                "A full Gomoku board is not automatically a draw. Use a proven no-five board fixture, "
                "or test draw by setting a full board state known not to contain five contiguous stones "
                "in any row, column, or diagonal before asserting `draw`. Avoid simple checkerboard, "
                "row-major, or repeated stripe patterns unless you have verified the win detector returns no winner."
            ),
            source=source,
            paths=paths,
        ))

    if (
        "expected { Object (winner, positions) } to be null" in output
        and any(term in output for term in ("board is full", "full with no 5-in-a-row", "No winner"))
        and any(term in output for term in ('"winner": "black"', '"winner": "white"'))
    ):
        findings.append(GateFinding(
            code="turn-board-game-draw-test-created-accidental-win",
            severity="error",
            stage="runtime",
            message="A draw helper test expected no winner, but the full-board fixture contains five-in-row.",
            repair_hint=(
                "A full Gomoku board is not automatically a draw. Use a proven no-five board fixture, "
                "or test draw by setting a full board state known not to contain five contiguous stones "
                "in any row, column, or diagonal before asserting `null`/draw. Avoid simple checkerboard, "
                "row-major, or repeated stripe patterns unless you have verified the win detector returns no winner."
            ),
            source=source,
            paths=paths,
        ))

    if (
        "expect(element).toHaveTextContent()" in output
        and "Expected element to have text content:" in output
        and "Received:" in output
        and any(term in output for term in ("黑棋胜", "白棋胜", "黑方获胜", "白方获胜", "平局"))
        and "当前回合" not in output
    ):
        findings.append(GateFinding(
            code="turn-based-board-game-winner-status-mismatch",
            severity="error",
            stage="runtime",
            message="Turn-based board game status text does not match the expected winner or draw state.",
            repair_hint=(
                "Re-check the move sequence and win/draw state update path. Preserve alternating turns, "
                "ensure the fifth stone triggers the intended winner, pass winner/highlight state through "
                "to the board, and avoid test filler moves that create an earlier opponent win."
            ),
            source=source,
            paths=paths,
        ))

    if (
        "expect(element).toHaveTextContent()" in output
        and "Expected element to have text content:" in output
        and "Received:" in output
        and "游戏结束" in output
        and "获胜" in output
        and "当前回合" in output
    ):
        findings.append(GateFinding(
            code="turn-based-board-game-winner-state-not-updated",
            severity="error",
            stage="runtime",
            message="Turn-based board game UI still shows the current turn after a winning move sequence.",
            repair_hint=(
                "Wire win detection into the same state path that handles normal moves. In `placeStone` "
                "or the click handler, evaluate the board after adding the latest stone, call `setWinner(...)` "
                "when five-in-a-row is found, and stop toggling to the next player after a win. Undo should "
                "clear `winner` and restore `currentPlayer` to the undone stone's player."
            ),
            source=source,
            paths=paths,
        ))

    if (
        "Found multiple elements by:" in output
        and "data-testid=\"cell-" in output
        and "render(<App" in output
    ):
        findings.append(GateFinding(
            code="react-testing-library-duplicate-render-without-cleanup",
            severity="error",
            stage="runtime",
            message="A React Testing Library test rendered the app twice and produced duplicate board cells.",
            repair_hint=(
                "Do not call `render(<App />)` multiple times in the same test without cleaning up the first "
                "render. Use a single render, call the returned `unmount()` before rendering again, or use "
                "Testing Library `cleanup()` between independent scenarios so `getByTestId('cell-r-c')` "
                "does not match duplicate boards."
            ),
            source=source,
            paths=paths,
        ))

    if (
        path.endswith("Board.test.tsx")
        and any(
            token in output
            for token in (
                "screen.getByText(/黑方获胜",
                "screen.getByText(/白方获胜",
                "screen.getByText(/平局",
                "Unable to find an element with the text: /黑方获胜",
                "Unable to find an element with the text: /白方获胜",
                "Unable to find an element with the text: /平局",
            )
        )
        and ("<Board" in output or "Board >" in output or "data-testid=\"cell-" in output)
    ):
        findings.append(GateFinding(
            code="react-presentational-board-test-expects-game-state",
            severity="error",
            stage="runtime",
            message="A Board component test expected winner or draw game status from a controlled board render.",
            repair_hint=(
                "Keep component tests at the right level. If `Board` is a presentational/controlled component, "
                "do not render `<Board board={board} onCellClick={() => {}} />` and expect clicks to mutate "
                "state or display `黑方获胜`/`白方获胜`/`平局`. Test win/draw integration through `App` or "
                "`useGameState`, or pass explicit `winner`/`winningCells`/draw props to Board and assert only "
                "the Board display contract."
            ),
            source=source,
            paths=paths,
        ))

    if (
        "Unable to find an element by: [data-testid=\"winner-display\"]" in output
        and "winnerDisplay" in output
        and "white" in output
        and any(term in output for term in ("白方", "white", "胜负判定", "Gomoku", "五子棋"))
    ):
        findings.append(GateFinding(
            code="turn-based-board-game-invalid-white-win-sequence",
            severity="error",
            stage="runtime",
            message="A Gomoku test expected a white winner display, but the public move sequence did not produce one.",
            repair_hint=(
                "White wins through the public click API require five white target cells, all played on "
                "turns 2/4/6/8/10. Do not count black's first move as part of the white target line, and "
                "do not stop after only four white stones. Use black filler moves that do not share one row, "
                "one column, or one diagonal, for example black `(10,10)`, `(11,12)`, `(12,14)`, `(13,11)`, "
                "`(14,13)` and white target `(0,5)..(4,5)` for a vertical win."
            ),
            source=source,
            paths=paths,
        ))

    if (
        "Unable to find an element by: [data-testid=\"draw-display\"]" in output
        and "winner-display" in output
        and any(term in output for term in ("winner: black", "winner: white", "黑方", "白方", "black", "white"))
    ):
        findings.append(GateFinding(
            code="turn-board-game-draw-test-created-accidental-win",
            severity="error",
            stage="runtime",
            message="A full-board draw test expected draw display but rendered a winner instead.",
            repair_hint=(
                "Avoid sequentially filling a Gomoku board through the public click API for draw tests; "
                "naive row-major, snake, or alternating patterns usually create five-in-a-row before the board "
                "is full. Test draw detection with a pure draw helper/state setup or a proven no-five board "
                "pattern, and assert the product's visible `平局`/draw contract after that deterministic setup."
            ),
            source=source,
            paths=paths,
        ))

    if (
        "Test timed out in 5000ms" in output
        and any(term in output for term in ("棋盘已满", "平局", "draw"))
    ):
        findings.append(GateFinding(
            code="turn-board-game-board-fill-test-timeout",
            severity="error",
            stage="runtime",
            message="A board-fill draw test timed out before completing the full board setup.",
            repair_hint=(
                "For the Gomoku MVP workflow, omit or delete the full-board draw UI test instead of "
                "clicking 225 cells or raising timeouts. Keep the automated UI coverage lightweight: one "
                "black horizontal win smoke test plus core interaction tests is enough; draw can be covered "
                "only if there is already a simple deterministic helper/state setup."
            ),
            source=source,
            paths=paths,
        ))

    if (
        "expect(element).toHaveTextContent()" in output
        and "Expected element to have text content:" in output
        and "Received:" in output
        and "胜者:" in output
    ):
        findings.append(GateFinding(
            code="turn-based-board-game-accidental-early-win",
            severity="error",
            stage="runtime",
            message=(
                "Turn-based board game test expected one winner but the rendered winner is the other player."
            ),
            repair_hint=(
                "Re-check the move sequence. For alternating-turn games, use opponent filler moves that do "
                "not block the target line and do not create an earlier win for the opponent. Then verify "
                "the game logic only declares the winner after the intended fifth stone."
            ),
            source=source,
            paths=paths,
        ))

    return findings


def runtime_findings_for_output(output: str, *, source: str) -> list[GateFinding]:
    from code_minions.failure_playbook import failure_hints_for_output

    findings: list[GateFinding] = _typescript_runtime_findings(output, source=source)
    findings.extend(_react_runtime_findings(output, source=source))
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
