from __future__ import annotations

from code_minions.failure_playbook import (
    failure_findings_for_output,
    failure_hints_for_output,
    failure_matches_for_output,
)


def test_playbook_hints_for_missing_vitest_binary() -> None:
    hints = failure_hints_for_output("sh: vitest: command not found")

    assert hints == [
        "Node test runner `vitest` is missing at runtime. Ensure npm dependencies are installed before tests run, and keep `vitest` in devDependencies if package.json uses it.",
    ]


def test_playbook_matches_are_structured_for_self_heal() -> None:
    matches = failure_matches_for_output("sh: vitest: command not found")

    assert matches == [
        {
            "name": "vitest-command-not-found",
            "category": "functional",
            "severity": "major",
            "fix_hint": "Node test runner `vitest` is missing at runtime. Ensure npm dependencies are installed before tests run, and keep `vitest` in devDependencies if package.json uses it.",
            "auto_fixable": False,
            "deterministic_fix": None,
        }
    ]


def test_playbook_hints_for_missing_jest_dom_import() -> None:
    hints = failure_hints_for_output(
        'Error: Failed to resolve import "@testing-library/jest-dom" from "src/setupTests.ts". Does the file exist?'
    )

    assert hints == [
        "The test setup imports `@testing-library/jest-dom` but package.json does not provide it. Add it to devDependencies or remove the setup import if no jest-dom matchers are used.",
    ]


def test_playbook_hints_for_missing_jest_dom_vitest_types() -> None:
    hints = failure_hints_for_output(
        "error TS2339: Property 'toHaveTextContent' does not exist on type 'Assertion<HTMLElement>'."
    )

    assert hints == [
        "Testing Library jest-dom matchers such as `toHaveTextContent` need Vitest type augmentation. Add `@testing-library/jest-dom` to devDependencies and import `@testing-library/jest-dom/vitest` from the Vitest setup file, or replace the custom matcher with a built-in assertion such as `expect(element.textContent).toContain(...)`.",
    ]


def test_playbook_hints_for_bare_jest_dom_import_in_vitest() -> None:
    hints = failure_hints_for_output(
        "ReferenceError: expect is not defined\n"
        "Object.<anonymous> node_modules/@testing-library/jest-dom/dist/extend-expect.js:6:1"
    )

    assert hints == [
        "The bare `@testing-library/jest-dom` setup import expects a Jest-style global `expect`. In Vitest, import `@testing-library/jest-dom/vitest` from the setup file, or enable Vitest globals deliberately and include the matching types.",
    ]


def test_playbook_hints_for_css_at_import_in_typescript_setup() -> None:
    hints = failure_hints_for_output("src/setupTests.ts(1,2): error TS1109: Expression expected.")

    assert hints == [
        "TypeScript reported `TS1109: Expression expected`, commonly caused by CSS-style `@import` in a `.ts` setup file. Use ES module syntax instead, for example `import '@testing-library/jest-dom/vitest';`.",
    ]


def test_ts1109_outside_setup_file_does_not_emit_setup_css_import_hint() -> None:
    hints = failure_hints_for_output(
        "src/hooks/useGameState.test.ts(17,6): error TS1109: Expression expected."
    )

    assert hints == []


def test_playbook_hints_for_vitest_no_test_files_found() -> None:
    hints = failure_hints_for_output("No test files found, exiting with code 1")

    assert hints == [
        "Vitest did not find any test files. Add at least one real `*.test.ts`, `*.test.tsx`, `*.spec.ts`, or `*.spec.tsx` file under `src/` that exercises the delivered behavior.",
    ]


def test_playbook_hints_for_vite_missing_relative_module_import() -> None:
    hints = failure_hints_for_output(
        'Error: Failed to resolve import "./Board" from "src/components/Board.test.tsx". Does the file exist?'
    )

    assert hints == [
        "A test imports a relative module that does not exist. Create the referenced source file, update the import to the file that was actually generated, or delete the orphan test so every test import resolves before running Vitest.",
    ]


def test_playbook_hints_for_swift_xctest_inside_app_sources() -> None:
    hints = failure_hints_for_output(
        "test-file-in-product-sources: Swift test file `src/CalculatorEngineTests.swift` is inside product sources."
    )

    assert hints == [
        "A Swift XCTest file is inside product sources. Move the test under `tests/` or delete the duplicate from `src/` so the app target does not compile XCTest.",
    ]


def test_playbook_hints_for_testing_library_duplicate_dom() -> None:
    hints = failure_hints_for_output(
        "TestingLibraryElementError: Found multiple elements by: [data-testid=\"cell-7-7\"]"
    )

    assert hints == [
        "React Testing Library found duplicate elements. Ensure each test cleans up rendered DOM, for example import `cleanup` and call `afterEach(cleanup)`, or configure Vitest globals/setupFiles correctly.",
    ]


def test_playbook_hints_for_low_level_pointerdown_touch_tests() -> None:
    hints = failure_hints_for_output(
        "low-level-pointerdown-test: src/components/Board.test.tsx uses "
        "`user.pointer(... '[pointerdown]')` as a touch support test."
    )

    assert hints == [
        "A React/Vite test uses low-level `user.pointer(... '[pointerdown]')` to prove touch support. In jsdom this can diverge from browser activation behavior; prefer `await user.click(cell)` for mouse/touch activation acceptance, or use `fireEvent.pointerDown(cell)` only when testing an explicit pointerDown contract.",
    ]


def test_playbook_hints_for_testing_library_duplicate_role_query() -> None:
    hints = failure_hints_for_output(
        "TestingLibraryElementError: Found multiple elements with the role \"button\" and name `/Row 1, Column 1/i`"
    )

    assert hints == [
        "React Testing Library found multiple elements for a role query. If the query targets board coordinates, use an exact accessible name or anchored regex such as `{ name: /^行1列1, 空$/ }`, or query a stable test id. Also ensure each test cleans up rendered DOM between cases.",
    ]


def test_playbook_hints_for_vitest_missing_jsdom_environment() -> None:
    hints = failure_hints_for_output(
        "ReferenceError: document is not defined\n"
        "ReferenceError: window is not defined\n"
        "at node_modules/@testing-library/react/dist/pure.js"
    )

    assert hints == [
        "React/Vite component tests are running in Vitest's Node environment. Configure Vitest with `test: { environment: 'jsdom' }` in `vite.config.ts` or `vitest.config.ts`, keep `jsdom` in devDependencies, and use cleanup between Testing Library tests.",
    ]


def test_playbook_hints_for_missing_postcss_plugin_dependency() -> None:
    hints = failure_hints_for_output(
        "Failed to load PostCSS config: Loading PostCSS Plugin failed: Cannot find module 'tailwindcss'"
    )

    assert hints == [
        "A PostCSS config references a plugin package that package.json does not install. Add the missing plugin such as `tailwindcss`/`autoprefixer` to devDependencies, or remove the PostCSS/Tailwind config and use plain CSS for the React/Vite MVP.",
    ]


def test_playbook_hints_for_npm_unpublished_dependency_version() -> None:
    hints = failure_hints_for_output(
        "npm error code ETARGET\n"
        "npm error notarget No matching version found for @testing-library/user-event@^16.0.1.\n"
        "npm error notarget In most cases you or one of your dependencies are requesting a package version that doesn't exist."
    )

    assert hints == [
        "npm could not install because package.json requests a dependency version that is not published. Do not invent future package versions; replace the dependency range with a published npm version, or remove the dependency if the generated code does not need it.",
    ]


def test_playbook_hints_for_jest_global_used_in_vitest() -> None:
    hints = failure_hints_for_output("ReferenceError: jest is not defined")

    assert hints == [
        "Vitest does not provide the Jest `jest` global. Import `vi` from `vitest` and use `vi.fn()`, `vi.spyOn()`, and other `vi.*` helpers instead of `jest.*`.",
    ]


def test_playbook_hints_for_vitest_test_api_globals_without_config() -> None:
    hints = failure_hints_for_output("ReferenceError: describe is not defined")

    assert hints == [
        "Vitest does not expose test APIs as globals unless configured. Either import `describe`, `it`/`test`, `expect`, `beforeEach`/`afterEach`, and `vi` from `vitest` in each test file, or set `test: { globals: true }` and include matching Vitest types.",
    ]


def test_playbook_hints_for_jsdom_layout_dependent_click_tests() -> None:
    hints = failure_hints_for_output(
        "AssertionError: expected +0 to be 1\n"
        "const rect = boardContainer.getBoundingClientRect()\n"
        "fireEvent.click(boardContainer, { clientX: rect.left + rect.width / 2 })\n"
        "document.querySelectorAll('circle[fill=\"#1a1a1a\"]')"
    )

    assert hints == [
        "jsdom does not calculate real element layout, so `getBoundingClientRect()` returns zero-sized boxes unless mocked. Prefer semantic click targets such as board-cell buttons/test ids, or mock `getBoundingClientRect` with non-zero width/height before firing coordinate-based clicks.",
    ]


def test_playbook_hints_for_jsdom_computed_style_layout_assertions() -> None:
    hints = failure_hints_for_output(
        "AssertionError: expected 'block' to be 'flex'\n"
        "const styles = window.getComputedStyle(board!.parentElement!)\n"
        "expect(styles.display).toBe('flex')"
    )

    assert hints == [
        "jsdom does not reliably apply external CSS module/import styles for layout assertions. Avoid `getComputedStyle()` tests for flex/grid centering or responsive layout; assert semantic structure/classes instead, or move layout verification to browser/e2e visual checks.",
    ]


def test_playbook_hints_for_vitest_user_event_fake_timer_timeout() -> None:
    hints = failure_hints_for_output(
        "FAIL  tests/movement.test.tsx > Keyboard controls > arrow up changes direction\n"
        "Error: Test timed out in 5000ms.\n"
        "If this is a long-running test, pass a timeout value as the last argument or configure it globally with \"testTimeout\"."
    )

    assert hints == [
        "Vitest interaction tests timed out. If the test uses `vi.useFakeTimers()` with `@testing-library/user-event`, create the user with `userEvent.setup({ advanceTimers: vi.advanceTimersByTime })`, or switch that interaction test back to real timers. When asserting timer-driven UI movement, explicitly advance the fake clock inside `act(...)` before checking DOM state.",
    ]


def test_playbook_hints_for_unapplied_react_state_fixture() -> None:
    hints = failure_hints_for_output(
        "AssertionError: expected '当前分数: 0' to contain '当前分数: 10'\n"
        "Expected: \"当前分数: 10\"\n"
        "Received: \"当前分数: 0\""
    )

    assert hints == [
        "A state-transition test expected score changes, but the rendered component stayed at the default state. If the test creates an initial-state fixture, pass it into the component or hook and ensure the implementation consumes that public initializer; otherwise drive enough real timer ticks and interactions to reach the asserted state.",
    ]


def test_playbook_does_not_match_npm_status_and_generic_expected_output_as_coordinates() -> None:
    hints = failure_hints_for_output(
        "up to date in 221ms\n"
        "Expected element to have text content:\n"
        "  Ready\n"
        "Received:\n"
        "  Loading\n"
        "row header rendered\n"
        "color: red\n"
    )

    assert hints == []


def test_playbook_hints_for_react_component_import_export_mismatch() -> None:
    hints = failure_hints_for_output(
        "Element type is invalid: expected a string (for built-in components) or a class/function "
        "(for composite components) but got: undefined. You likely forgot to export your component "
        "from the file it's defined in, or you might have mixed up default and named imports."
    )

    assert hints == [
        "A React component rendered as `undefined`, usually because tests or callers default-import a module that only has a named export, or vice versa. Keep the import/export contract consistent; for `src/App.tsx`, exporting both `export function App(...)` and `export default App` is acceptable when existing callers use both forms.",
    ]


def test_playbook_hints_for_hook_test_private_state_mutator() -> None:
    hints = failure_hints_for_output(
        "TypeError: result.current._setState is not a function\n"
        " ❯ tests/useGame.test.ts:42:35\n"
        "result.current['_setState']({"
    )

    assert hints == [
        "React hook tests should not access private or imagined hook internals such as `result.current['_setState']`. Drive state through the public hook API, factor deterministic pure helpers, or intentionally expose a real testable setter/initializer and update the hook contract and tests together.",
    ]


def test_playbook_hints_for_spy_on_non_exported_helper() -> None:
    hints = failure_hints_for_output(
        "Error: generateFood does not exist\n"
        " ❯ tests/App.test.tsx:28:35\n"
        "const mockGenerateFood = vi.spyOn(gameModule, 'generateFood')"
    )

    assert hints == [
        "`vi.spyOn(module, 'name')` can only mock a real exported module property. Do not spy on non-exported implementation helpers; either export the helper deliberately, move it to a pure helper module, or test through the public UI/hook behavior without that spy.",
    ]


def test_playbook_hints_for_testing_library_split_text_query() -> None:
    hints = failure_hints_for_output(
        "Unable to find an element with the text: /Score:\\s*0/. "
        "This could be because the text is broken up by multiple elements."
    )

    assert hints == [
        "Testing Library text queries can fail when label and value are split across child elements such as `<span>Score:</span><strong>0</strong>`. Prefer an accessible label/query, a stable test id plus `toHaveTextContent`, or a function matcher that checks `element.textContent`.",
    ]


def test_playbook_hints_for_typescript_state_enum_literal_mismatch() -> None:
    hints = failure_hints_for_output(
        "src/App.tsx(37,22): error TS2345: Argument of type '\"black\"' "
        "is not assignable to parameter of type 'SetStateAction<Stone>'."
    )

    assert hints == [
        "A TypeScript state value uses an incompatible string literal or enum value. Preserve the existing exported player type contract across tasks; if the state is typed as an enum, use enum members such as `Stone.Black`, and if the app already uses a `'black' | 'white'` union, do not replace it with an incompatible enum.",
    ]


def test_playbook_hints_for_xcodegen_missing_infoplist_generation() -> None:
    hints = failure_hints_for_output(
        "Cannot code sign because the target does not have an Info.plist file and one is not being generated automatically."
    )

    assert hints == [
        "An XcodeGen target is missing Info.plist configuration. Add `GENERATE_INFOPLIST_FILE: YES` or an explicit `INFOPLIST_FILE`/`info.path` for every application and unit-test target, especially the test bundle target.",
    ]


def test_playbook_hints_for_fastapi_dict_response_model() -> None:
    hints = failure_hints_for_output(
        "AttributeError: 'dict' object has no attribute '__module__'\n"
        "@app.post('/items', response_model={'result': float})"
    )

    assert hints
    assert "FastAPI `response_model`" in hints[0]
    assert "Pydantic model" in hints[0]


def test_failure_findings_for_output_preserves_structured_runtime_hint() -> None:
    findings = failure_findings_for_output(
        "ReferenceError: describe is not defined",
        source="react-vite",
    )

    assert findings[0]["stage"] == "runtime"
    assert findings[0]["severity"] == "error"
    assert findings[0]["source"] == "react-vite"
    assert "Vitest" in findings[0]["repair_hint"]
