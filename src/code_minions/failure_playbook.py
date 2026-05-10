from typing import Any

PLAYBOOK: tuple[tuple[tuple[str, ...], str], ...] = (
    (
        ("vitest: command not found",),
        "Node test runner `vitest` is missing at runtime. Ensure npm dependencies are installed before tests run, "
        "and keep `vitest` in devDependencies if package.json uses it.",
    ),
    (
        ('Failed to resolve import "@testing-library/jest-dom"',),
        "The test setup imports `@testing-library/jest-dom` but package.json does not provide it. "
        "Add it to devDependencies or remove the setup import if no jest-dom matchers are used.",
    ),
    (
        ("does not exist on type 'assertion", "tohavetextcontent"),
        "Testing Library jest-dom matchers such as `toHaveTextContent` need Vitest type augmentation. "
        "Add `@testing-library/jest-dom` to devDependencies and import `@testing-library/jest-dom/vitest` "
        "from the Vitest setup file, or replace the custom matcher with a built-in assertion such as "
        "`expect(element.textContent).toContain(...)`.",
    ),
    (
        ("referenceerror: expect is not defined", "@testing-library/jest-dom"),
        "The bare `@testing-library/jest-dom` setup import expects a Jest-style global `expect`. "
        "In Vitest, import `@testing-library/jest-dom/vitest` from the setup file, or enable Vitest "
        "globals deliberately and include the matching types.",
    ),
    (
        ("setuptests.ts", "ts1109", "expression expected"),
        "TypeScript reported `TS1109: Expression expected`, commonly caused by CSS-style `@import` "
        "in a `.ts` setup file. Use ES module syntax instead, for example "
        "`import '@testing-library/jest-dom/vitest';`.",
    ),
    (
        ("no test files found",),
        "Vitest did not find any test files. Add at least one real `*.test.ts`, `*.test.tsx`, "
        "`*.spec.ts`, or `*.spec.tsx` file under `src/` that exercises the delivered behavior.",
    ),
    (
        ('failed to resolve import "./', "does the file exist?"),
        "A test imports a relative module that does not exist. Create the referenced source file, "
        "update the import to the file that was actually generated, or delete the orphan test so "
        "every test import resolves before running Vitest.",
    ),
    (
        ('failed to resolve import "../', "does the file exist?"),
        "A test imports a relative module that does not exist. Create the referenced source file, "
        "update the import to the file that was actually generated, or delete the orphan test so "
        "every test import resolves before running Vitest.",
    ),
    (
        ("test-file-in-product-sources",),
        "A Swift XCTest file is inside product sources. Move the test under `tests/` or delete the duplicate "
        "from `src/` so the app target does not compile XCTest.",
    ),
    (
        ("found multiple elements", "data-testid"),
        "React Testing Library found duplicate elements. Ensure each test cleans up rendered DOM, for example "
        "import `cleanup` and call `afterEach(cleanup)`, or configure Vitest globals/setupFiles correctly.",
    ),
    (
        ("found multiple elements", "role"),
        "React Testing Library found multiple elements for a role query. If the query targets board coordinates, "
        "use an exact accessible name or anchored regex such as `{ name: /^行1列1, 空$/ }`, or query a stable "
        "test id. Also ensure each test cleans up rendered DOM between cases.",
    ),
    (
        ("referenceerror: document is not defined",),
        "React/Vite component tests are running in Vitest's Node environment. Configure Vitest with "
        "`test: { environment: 'jsdom' }` in `vite.config.ts` or `vitest.config.ts`, keep `jsdom` "
        "in devDependencies, and use cleanup between Testing Library tests.",
    ),
    (
        ("failed to load postcss config", "cannot find module"),
        "A PostCSS config references a plugin package that package.json does not install. Add the "
        "missing plugin such as `tailwindcss`/`autoprefixer` to devDependencies, or remove the "
        "PostCSS/Tailwind config and use plain CSS for the React/Vite MVP.",
    ),
    (
        ("npm error code etarget", "no matching version found for"),
        "npm could not install because package.json requests a dependency version that is not published. "
        "Do not invent future package versions; replace the dependency range with a published npm version, "
        "or remove the dependency if the generated code does not need it.",
    ),
    (
        ("referenceerror: window is not defined",),
        "React/Vite component tests are running in Vitest's Node environment. Configure Vitest with "
        "`test: { environment: 'jsdom' }` in `vite.config.ts` or `vitest.config.ts`, keep `jsdom` "
        "in devDependencies, and use cleanup between Testing Library tests.",
    ),
    (
        ("referenceerror: jest is not defined",),
        "Vitest does not provide the Jest `jest` global. Import `vi` from `vitest` and use "
        "`vi.fn()`, `vi.spyOn()`, and other `vi.*` helpers instead of `jest.*`.",
    ),
    (
        ("referenceerror: describe is not defined",),
        "Vitest does not expose test APIs as globals unless configured. Either import `describe`, "
        "`it`/`test`, `expect`, `beforeEach`/`afterEach`, and `vi` from `vitest` in each test file, "
        "or set `test: { globals: true }` and include matching Vitest types.",
    ),
    (
        ("getboundingclientrect", "clientx"),
        "jsdom does not calculate real element layout, so `getBoundingClientRect()` returns zero-sized "
        "boxes unless mocked. Prefer semantic click targets such as board-cell buttons/test ids, or mock "
        "`getBoundingClientRect` with non-zero width/height before firing coordinate-based clicks.",
    ),
    (
        ("getcomputedstyle", "expected 'block' to be 'flex'"),
        "jsdom does not reliably apply external CSS module/import styles for layout assertions. Avoid "
        "`getComputedStyle()` tests for flex/grid centering or responsive layout; assert semantic "
        "structure/classes instead, or move layout verification to browser/e2e visual checks.",
    ),
    (
        ("low-level-pointerdown-test",),
        "A React/Vite test uses low-level `user.pointer(... '[pointerdown]')` to prove touch support. "
        "In jsdom this can diverge from browser activation behavior; prefer `await user.click(cell)` "
        "for mouse/touch activation acceptance, or use `fireEvent.pointerDown(cell)` only when testing "
        "an explicit pointerDown contract.",
    ),
    (
        ("ts2345", "setstateaction"),
        "A TypeScript state value uses an incompatible string literal or enum value. Preserve the existing "
        "exported player type contract across tasks; if the state is typed as an enum, use enum members "
        "such as `Stone.Black`, and if the app already uses a `'black' | 'white'` union, do not replace "
        "it with an incompatible enum.",
    ),
    (
        ("cannot code sign because the target does not have an info.plist file",),
        "An XcodeGen target is missing Info.plist configuration. Add `GENERATE_INFOPLIST_FILE: YES` "
        "or an explicit `INFOPLIST_FILE`/`info.path` for every application and unit-test target, "
        "especially the test bundle target.",
    ),
    (
        ("attributeerror: 'dict' object has no attribute '__module__'", "response_model"),
        "FastAPI `response_model` must be a Pydantic model class or valid Python type, not a dict "
        "literal. Define a Pydantic model such as `class AddResponse(BaseModel): result: float`, use "
        "`response_model=AddResponse`, or omit `response_model` for simple dict responses.",
    ),
)


def failure_hints_for_output(output: str) -> list[str]:
    normalized = output.lower()
    hints: list[str] = []
    for needles, hint in PLAYBOOK:
        if all(needle.lower() in normalized for needle in needles) and hint not in hints:
            hints.append(hint)
    return hints


def failure_findings_for_output(output: str, *, source: str = "") -> list[dict[str, Any]]:
    from code_minions.gates import findings_to_dicts, runtime_findings_for_output

    return findings_to_dicts(runtime_findings_for_output(output, source=source))
