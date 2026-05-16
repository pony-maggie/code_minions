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
        elif ts_code == "6133" and "is declared but its value is never read" in lowered:
            code = "typescript-unused-local"
            repair_hint = (
                "Remove the unused local, import, or destructured value. For React `useState`, if only the "
                "state value is read, destructure just `[value] = useState(...)`; keep the setter only when "
                "real UI or game logic calls it."
            )
        elif ts_code == "2588" and "cannot assign to" in lowered and "because it is a constant" in lowered:
            code = "typescript-reassigned-const"
            repair_hint = (
                "A variable declared with `const` is reassigned later. Change that local binding to `let`, "
                "or refactor the reassignment into a new value if immutability is intended."
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
            and "property 'advancetimersbytime' does not exist" in lowered
            and "userevent" in lowered
        ):
            code = "testing-library-user-event-timer-method"
            repair_hint = (
                "`advanceTimersByTime` is a Vitest timer API, not a `userEvent` instance method. "
                "Call `vi.advanceTimersByTime(...)`, usually inside `act(...)`, and remove unused "
                "`userEvent.setup()` variables if the test only fires keyboard/click events directly."
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
    missing_control_match = re.search(
        r"""Unable to find an element by:\s+\[data-testid=["'](?P<testid>[^"']+-button)["']\]""",
        output,
    )
    if missing_control_match:
        testid = missing_control_match.group("testid")
        reset_path = _vitest_frame_path_containing(output, "App") or path
        findings.append(GateFinding(
            code="react-vite-missing-stable-control-testid",
            severity="error",
            stage="runtime",
            message="A React test expected a stable control test id that is missing from the rendered app.",
            repair_hint=(
                f"Preserve existing public test contracts when adding features. Render the control with "
                f"`data-testid=\"{testid}\"` (`{testid}`) if tests already use it, or update all tests and UI together "
                "to a single stable selector. Do not replace an existing interactive app with a placeholder "
                "shell such as `<main>Ready</main>` while implementing later behavior."
            ),
            source=source,
            paths=[reset_path] if reset_path else paths,
        ))

    missing_testid_match = re.search(
        r"""Unable to find an element by:\s+\[data-testid=["'](?P<testid>[^"']+)["']\]""",
        output,
    )
    if (
        missing_testid_match
        and not missing_testid_match.group("testid").endswith("-button")
    ):
        testid = missing_testid_match.group("testid")
        findings.append(GateFinding(
            code="react-vite-missing-stable-testid",
            severity="error",
            stage="runtime",
            message="A React test expected a stable test id that is missing from the rendered app.",
            repair_hint=(
                f"Preserve the stable DOM contract used by tests and prior tasks. Render an element with "
                f"`data-testid=\"{testid}\"` (`{testid}`) when that semantic UI element still exists, "
                "or update tests and implementation together to use a single stable selector."
            ),
            source=source,
            paths=paths,
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

    if "result.current._setState is not a function" in output or "result.current['_setState']" in output:
        state_path = _first_vitest_frame_path(output)
        findings.append(GateFinding(
            code="react-hook-test-private-state-mutator",
            severity="error",
            stage="runtime",
            message="A React hook test tried to call a private or non-existent state mutator.",
            repair_hint=(
                "Do not access private or imagined hook internals such as `result.current['_setState']`. "
                "Drive state through the public hook API, factor deterministic pure helpers for game-rule "
                "logic, or intentionally expose a real testable setter/initializer and update the hook "
                "contract and tests together."
            ),
            source=source,
            paths=[state_path] if state_path else paths,
        ))
        return findings

    spy_missing_match = re.search(
        r"""(?:Error:\s+)?(?P<name>[A-Za-z_$][\w$]*) does not exist[\s\S]{0,300}vi\.spyOn""",
        output,
    )
    if not spy_missing_match:
        spy_missing_match = re.search(
            r"""vi\.spyOn[\s\S]{0,300}(?:Error:\s+)?(?P<name>[A-Za-z_$][\w$]*) does not exist""",
            output,
        )
    if spy_missing_match:
        name = spy_missing_match.group("name")
        spy_path = _first_vitest_frame_path(output)
        findings.append(GateFinding(
            code="react-vitest-spy-on-non-exported-helper",
            severity="error",
            stage="runtime",
            message=f"A Vitest spy targeted `{name}`, but that module export does not exist.",
            repair_hint=(
                "`vi.spyOn(module, 'name')` can only mock a real exported module property. Do not spy "
                "on non-exported implementation helpers; either export the helper deliberately, move it "
                "to a pure helper module, or test through the public UI/hook behavior without that spy."
            ),
            source=source,
            paths=[spy_path] if spy_path else paths,
        ))
        return findings

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
        "Test timed out in 5000ms" in output
        and not any(term in output for term in ("棋盘已满", "平局", "draw"))
    ):
        timeout_path = _first_vitest_frame_path(output)
        findings.append(GateFinding(
            code="react-vite-user-event-fake-timer-timeout",
            severity="error",
            stage="runtime",
            message="A React/Vite interaction test timed out under Vitest.",
            repair_hint=(
                "If this test uses `vi.useFakeTimers()` with Testing Library `userEvent`, create the user "
                "with `userEvent.setup({ advanceTimers: vi.advanceTimersByTime })`, or keep the user "
                "interaction test on real timers. For timer-driven UI assertions, advance fake timers "
                "inside `act(...)` before checking DOM movement or status changes."
            ),
            source=source,
            paths=[timeout_path] if timeout_path else paths,
        ))

    if (
        "Element type is invalid" in output
        and "got: undefined" in output
        and "mixed up default and named imports" in output
    ):
        mismatch_path = _first_vitest_frame_path(output)
        findings.append(GateFinding(
            code="react-component-import-export-mismatch",
            severity="error",
            stage="runtime",
            message="A React component rendered as undefined, likely due to an import/export mismatch.",
            repair_hint=(
                "Align React component imports with the module exports. If tests or callers use a "
                "default import from a module that currently has only a named component export, either "
                "change all callers to named imports or add `export default ComponentName` while keeping "
                "the named export for existing callers."
            ),
            source=source,
            paths=[mismatch_path] if mismatch_path else paths,
        ))

    if (
        "Unable to find an element with the text:" in output
        and "text is broken up by multiple elements" in output
    ):
        text_query_path = _first_vitest_frame_path(output)
        findings.append(GateFinding(
            code="react-testing-library-split-text-query",
            severity="error",
            stage="runtime",
            message="A Testing Library text query failed because the visible text is split across child elements.",
            repair_hint=(
                "Avoid brittle `getByText` regex assertions for labels whose value is rendered in a child "
                "element. Query the accessible container label, use a stable test id with "
                "`toHaveTextContent`, or use a function matcher that checks `element.textContent`."
            ),
            source=source,
            paths=[text_query_path] if text_query_path else paths,
        ))

    if (
        "received value must be a node" in output.lower()
        and ".enter-hint" in output
        and re.search(r"""(?i)(enter|start|开始|button|按钮)""", output)
    ):
        optional_path = _first_vitest_frame_path(output)
        findings.append(GateFinding(
            code="react-optional-or-criteria-overasserted",
            severity="error",
            stage="runtime",
            message="A React test appears to require an optional OR acceptance affordance as mandatory DOM.",
            repair_hint=(
                "Do not turn OR acceptance criteria into AND tests. If the product requirement says a user "
                "can start with a button or an Enter hint, the test should assert that at least one supported "
                "start affordance works, or the implementation should deliberately expose both if that is the "
                "chosen UI contract. Avoid querying a single optional selector and passing null into "
                "jest-dom matchers."
            ),
            source=source,
            paths=[optional_path] if optional_path else paths,
        ))

    if (
        (
            "180度反向" in output
            or "180-degree" in output
            or "opposite direction" in output
            or ("headAfterUp" in output and re.search(r"""(?i)(arrowdown|向下|s键|btn-down)""", output))
        )
        and "expected" in output.lower()
        and re.search(r"""(?i)(arrowleft|arrowdown|向左|向下|for left|leftbutton)""", output)
    ):
        direction_path = _first_vitest_frame_path(output)
        findings.append(GateFinding(
            code="react-grid-invalid-opposite-direction-test",
            severity="error",
            stage="runtime",
            message="A grid movement test appears to expect an illegal opposite-direction turn.",
            repair_hint=(
                "Movement tests for grid apps must respect the initial direction and 180-degree "
                "reversal rule. Do not assert an immediate opposite turn from the current direction. "
                "For example, from an initial right direction, pressing left should be rejected; to test "
                "left movement, first put the entity in a vertical direction through a legal turn sequence "
                "or expose a deterministic initial state helper."
            ),
            source=source,
            paths=[direction_path] if direction_path else paths,
        ))

    if (
        "expected" in output.lower()
        and re.search(r"""(?i)(head|grid|row|col|列|行)""", output)
        and (
            re.search(r"""(?i)expected\s+['"].*(?:row|col|列|行).*['"]\s+to\s+(?:match|contain|be)""", output)
            or re.search(r"""(?i)to\s+(?:match|contain).*['"].*(?:row|col|列|行).*['"]""", output)
            or re.search(r"""(?i)expected.*(?:row|col|列|行).*received""", output)
        )
    ):
        coordinate_path = _first_vitest_frame_path(output)
        findings.append(GateFinding(
            code="react-grid-brittle-absolute-coordinate-test",
            severity="error",
            stage="runtime",
            message="A grid movement test appears to assert brittle absolute head coordinates.",
            repair_hint=(
                "Prefer relative movement assertions for grid games: capture the head cell before the tick, "
                "perform one legal direction change and timer advance, then assert the row/column delta. "
                "Only assert exact coordinates when the test passes a deterministic initial state through a "
                "public initializer that the component actually consumes; keep 0-based/1-based labels explicit."
            ),
            source=source,
            paths=[coordinate_path] if coordinate_path else paths,
        ))

    if (
        "expected" in output
        and re.search(r"""(?i)(current score|score|分数|high score|最高分|length|count)""", output)
        and re.search(r"""(?i)(localStorage|setItem|当前分数|最高分|stored|persist)""", output)
        and re.search(r"""(?i)(received:?\s*['"]?.*0|to contain ['"].*10|to be called with arguments)""", output)
    ):
        fixture_path = _first_vitest_frame_path(output)
        findings.append(GateFinding(
            code="react-deterministic-state-fixture-not-applied",
            severity="error",
            stage="runtime",
            message="A React state-transition test appears to expect fixture-driven state that was not applied.",
            repair_hint=(
                "When a React/Vitest test needs deterministic state, do not create an unused fixture. "
                "Pass the fixture through an explicit component prop or hook initializer, and make the "
                "implementation consume that public contract. If no deterministic initializer exists, "
                "drive the real UI through enough valid timer ticks/interactions before asserting score, "
                "storage, or entity-growth state."
            ),
            source=source,
            paths=[fixture_path] if fixture_path else paths,
        ))

    if "Found multiple elements with the text:" in output:
        ambiguous_path = _first_vitest_frame_path(output)
        findings.append(GateFinding(
            code="react-generated-test-ambiguous-text-query",
            severity="error",
            stage="generated-test-contract",
            message="A generated React test uses a text query broad enough to match multiple elements.",
            repair_hint=(
                "Anchor broad text regexes, scope the query with `within(...)`, or use a semantic test id/role. "
                "For score panels, prefer exact patterns such as `/^分数:\\s*0$/` over `/分数: 0/`, which can "
                "also match high-score text."
            ),
            source=source,
            paths=[ambiguous_path] if ambiguous_path else paths,
        ))

    if (
        re.search(r"""Unable to find an element (?:by|with).*?(?:game-over|游戏结束)""", output, re.DOTALL)
        and "advanceTimersByTime" in output
        and re.search(r"""(?i)(TICK_INTERVAL|\*\s*\d+|game over|撞墙|wall)""", output)
    ):
        timer_path = _first_vitest_frame_path(output)
        findings.append(GateFinding(
            code="react-generated-test-brittle-long-timer-state",
            severity="error",
            stage="generated-test-contract",
            message="A generated React game-state test advances many fake-timer ticks at once and expects complex UI state.",
            repair_hint=(
                "Do not prove multi-tick game-over behavior by one large `vi.advanceTimersByTime(...)` against "
                "component state. Either test the pure collision helper directly, expose a deterministic initial "
                "state near the boundary, or advance one timer tick per act so React effects and refs settle "
                "between ticks."
            ),
            source=source,
            paths=[timer_path] if timer_path else paths,
        ))

    if (
        "TS2322" in output
        and (
            "initialState" in output
            or "initialDirection" in output
        )
        and ("Props" in output or "IntrinsicAttributes" in output)
        and "not assignable to type" in output
    ):
        prop_path = _first_vitest_frame_path(output)
        findings.append(GateFinding(
            code="react-test-fixture-prop-contract-mismatch",
            severity="error",
            stage="runtime",
            message="A React test fixture prop does not match the component's declared props.",
            repair_hint=(
                "Keep deterministic test fixtures aligned with the component's public prop contract. "
                "Either add an `initialState` prop to the props interface and consume it when initializing "
                "state, or add and consume supported granular initializer props consistently in both the "
                "component and hook. Type inline fixtures "
                "with the existing domain type or `satisfies` it so literal unions such as direction/status "
                "do not widen to plain `string`."
            ),
            source=source,
            paths=[prop_path] if prop_path else paths,
        ))

    generic_marker_match = re.search(
        r"""toHaveAttribute\(["'](?P<attr>data-[^"']+)["'],\s*["']true["']\)""",
        output,
    )
    if generic_marker_match and re.search(r"(?m)^\s*null\s*$", output):
        marker_attr = generic_marker_match.group("attr")
        marker_path = _first_vitest_frame_path(output)
        findings.append(GateFinding(
            code="react-grid-entity-marker-missing",
            severity="error",
            stage="runtime",
            message="A grid/entity test expected a stable data marker, but the rendered cell did not expose it.",
            repair_hint=(
                f"Preserve stable grid-cell DOM markers used by tests and accessibility checks. Derive "
                f"`{marker_attr}=\"true\"` from the same state that renders the visual entity, and keep "
                "the marker synchronized when the entity moves or is regenerated."
            ),
            source=source,
            paths=[marker_path] if marker_path else paths,
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
