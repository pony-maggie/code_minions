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
_VITEST_FRAME_PATH_RE = re.compile(r"❯\s+(?P<path>[^\s:]+\.(?:ts|tsx)):\d+:\d+")
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

        if ts_code == "2304" and "cannot find name" in lowered:
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
        elif ts_code == "7006" and "implicitly has an 'any' type" in message:
            if path not in implicit_any_paths:
                implicit_any_paths.append(path)
            continue
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


def _react_runtime_findings(output: str, *, source: str) -> list[GateFinding]:
    findings: list[GateFinding] = []
    path = _first_vitest_frame_path(output)
    paths = [path] if path else []

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
        "expect(element).toHaveTextContent()" in output
        and "Expected element to have text content:" in output
        and "Received:" in output
        and re.search(r"(?m)^\s*/(?:空\$|黑|白)/\s*$", output)
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
                "do not stop after only four white stones. Use black filler moves far from the white line, "
                "for example black `(14,0)..(14,4)` and white target `(0,1)..(4,1)` for a vertical win."
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
        "expect(element).toHaveTextContent()" in output
        and "Expected element to have text content:" in output
        and "Received:" in output
        and any(term in output for term in ("黑棋胜", "白棋胜", "平局"))
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
        "Test timed out in 5000ms" in output
        and any(term in output for term in ("棋盘已满", "平局", "draw"))
    ):
        findings.append(GateFinding(
            code="turn-board-game-board-fill-test-timeout",
            severity="error",
            stage="runtime",
            message="A board-fill draw test timed out before completing the full board setup.",
            repair_hint=(
                "Avoid 225 slow `userEvent.click` calls just to create a full-board draw state. "
                "Test draw detection with a pure helper/state setup, use faster `fireEvent.click` "
                "with a carefully generated no-win pattern, or raise the timeout only after proving "
                "the test is not stuck in an accidental win/game-over loop."
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
