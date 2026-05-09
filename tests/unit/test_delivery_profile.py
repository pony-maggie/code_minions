from __future__ import annotations

from code_minions.delivery import (
    execution_profile_for_delivery,
    infer_delivery_profile,
    repair_unique_unresolved_relative_imports,
    validate_delivery_profile,
)


def test_infers_swift_xcodegen_profile_from_macos_prd() -> None:
    profile = infer_delivery_profile({
        "goal": "Build a native macOS calculator app",
        "constraints": ["Swift 6 + SwiftUI", "Xcode 16+", "XcodeGen project.yml"],
        "features": [],
        "non_functional": {},
    })

    assert profile["kind"] == "native-macos-app"
    assert profile["language"] == "swift"
    assert profile["build_system"] == "xcodegen"
    assert "python" in profile["forbidden_product_languages"]
    assert "**/*.swift" in profile["required_files"]


def test_explicit_go_profile_keeps_declared_fields_and_adds_stack_id() -> None:
    structured_prd = {
        "goal": "Build a Go web service",
        "delivery_profile": {
            "kind": "web-service",
            "language": "go",
            "build_system": "go-mod",
            "test_command": "go test ./...",
            "required_files": ["go.mod", "**/*.go"],
            "forbidden_product_languages": ["python"],
        },
    }

    profile = infer_delivery_profile(structured_prd)

    for key, value in structured_prd["delivery_profile"].items():
        assert profile[key] == value
    assert profile["stack_id"] == "go-service"


def test_explicit_react_vite_profile_is_normalized_with_stack_id() -> None:
    profile = infer_delivery_profile({
        "goal": "Build a browser Gomoku game",
        "delivery_profile": {
            "kind": "web-app",
            "language": "typescript",
            "framework": "react",
            "build_system": "vite",
        },
    })

    assert profile["stack_id"] == "react-vite"
    assert profile["test_command"] == "npm test"
    assert profile["required_files"] == ["package.json", "index.html", "src"]


def test_partial_swift_profile_is_normalized_to_enforceable_contract() -> None:
    profile = infer_delivery_profile({
        "goal": "Build a native macOS desktop application",
        "delivery_profile": {
            "kind": "native macOS desktop application",
            "language": "Swift 6",
            "framework": "SwiftUI",
            "build_system": "Xcode 16+",
            "test_command": None,
            "required_files": None,
            "forbidden_product_languages": None,
        },
    })

    assert profile["kind"] == "native-macos-app"
    assert profile["language"] == "swift"
    assert profile["build_system"] == "xcodegen"
    assert profile["test_command"] == "xcodegen generate && xcodebuild test -scheme MacCalc"
    assert profile["required_files"] == ["project.yml", "**/*.swift", "**/*App.swift"]
    assert "python" in profile["forbidden_product_languages"]
    assert profile["stack_id"] == "swift-xcodegen"


def test_validate_rejects_forbidden_language_and_missing_required_files(tmp_path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "calculator.py").write_text("def add(a, b): return a + b\n")
    profile = {
        "kind": "native-macos-app",
        "language": "swift",
        "required_files": ["project.yml", "**/*.swift"],
        "forbidden_product_languages": ["python"],
    }

    issues = validate_delivery_profile(tmp_path, profile)

    codes = {issue["code"] for issue in issues}
    assert "missing-required-file" in codes
    assert "forbidden-product-language" in codes


def test_validate_rejects_swift_tests_inside_product_sources(tmp_path) -> None:
    (tmp_path / "project.yml").write_text("name: MacCalc\n")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "MacCalcApp.swift").write_text("import SwiftUI\n@main struct MacCalcApp {}\n")
    (tmp_path / "src" / "CalculatorEngine.swift").write_text("struct CalculatorEngine {}\n")
    (tmp_path / "src" / "CalculatorEngineTests.swift").write_text(
        "import XCTest\n@testable import MacCalc\nfinal class CalculatorEngineTests: XCTestCase {}\n"
    )
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "CalculatorEngineTests.swift").write_text(
        "import XCTest\n@testable import MacCalc\nfinal class CalculatorEngineTests: XCTestCase {}\n"
    )
    profile = {
        "kind": "native-macos-app",
        "language": "swift",
        "build_system": "xcodegen",
        "required_files": ["project.yml", "**/*.swift", "**/*App.swift"],
        "forbidden_product_languages": ["python", "javascript", "typescript", "go"],
    }

    issues = validate_delivery_profile(tmp_path, profile)

    assert any(issue["code"] == "test-file-in-product-sources" for issue in issues)


def test_validate_rejects_xcodegen_test_target_without_infoplist_generation(tmp_path) -> None:
    (tmp_path / "project.yml").write_text(
        "name: MacCalc\n"
        "targets:\n"
        "  MacCalc:\n"
        "    type: application\n"
        "    platform: macOS\n"
        "    sources: [Sources/MacCalc]\n"
        "    settings:\n"
        "      GENERATE_INFOPLIST_FILE: YES\n"
        "  MacCalcTests:\n"
        "    type: bundle.unit-test\n"
        "    platform: macOS\n"
        "    sources: [Tests/MacCalcTests]\n"
        "    dependencies:\n"
        "      - target: MacCalc\n"
    )
    profile = {
        "kind": "native-macos-app",
        "language": "swift",
        "build_system": "xcodegen",
    }

    issues = validate_delivery_profile(tmp_path, profile)

    assert any(issue["code"] == "missing-infoplist-generation" for issue in issues)


def test_validate_accepts_xcodegen_test_target_with_infoplist_generation(tmp_path) -> None:
    (tmp_path / "project.yml").write_text(
        "name: MacCalc\n"
        "targets:\n"
        "  MacCalc:\n"
        "    type: application\n"
        "    platform: macOS\n"
        "    sources: [Sources/MacCalc]\n"
        "    settings:\n"
        "      GENERATE_INFOPLIST_FILE: YES\n"
        "  MacCalcTests:\n"
        "    type: bundle.unit-test\n"
        "    platform: macOS\n"
        "    sources: [Tests/MacCalcTests]\n"
        "    settings:\n"
        "      GENERATE_INFOPLIST_FILE: YES\n"
        "    dependencies:\n"
        "      - target: MacCalc\n"
    )
    profile = {
        "kind": "native-macos-app",
        "language": "swift",
        "build_system": "xcodegen",
    }

    issues = validate_delivery_profile(tmp_path, profile)

    assert not any(issue["code"] == "missing-infoplist-generation" for issue in issues)


def test_validate_accepts_go_web_service_profile(tmp_path) -> None:
    (tmp_path / "go.mod").write_text("module example.com/service\n")
    (tmp_path / "cmd" / "server").mkdir(parents=True)
    (tmp_path / "cmd" / "server" / "main.go").write_text("package main\nfunc main() {}\n")
    profile = {
        "kind": "web-service",
        "language": "go",
        "required_files": ["go.mod", "cmd/server/main.go", "**/*.go"],
        "forbidden_product_languages": ["python"],
    }

    assert validate_delivery_profile(tmp_path, profile) == []


def test_validate_rejects_react_vite_project_without_test_files(tmp_path) -> None:
    (tmp_path / "package.json").write_text('{"scripts":{"test":"vitest run"}}\n')
    (tmp_path / "vite.config.ts").write_text(
        "import { defineConfig } from 'vitest/config'\n"
        "export default defineConfig({ test: { environment: 'jsdom' } })\n"
    )
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "App.tsx").write_text("export default function App() { return <div /> }\n")
    profile = {
        "kind": "web-app",
        "language": "typescript",
        "framework": "react",
        "build_system": "vite",
    }

    issues = validate_delivery_profile(tmp_path, profile)

    assert any(issue["code"] == "missing-test-file" and issue["severity"] == "error" for issue in issues)


def test_validate_downgrades_react_vite_hygiene_checks_when_relaxed(tmp_path) -> None:
    (tmp_path / "package.json").write_text('{"scripts":{"test":"vitest run"}}\n')
    (tmp_path / "vite.config.ts").write_text(
        "import { defineConfig } from 'vite'\n"
        "export default defineConfig({})\n"
    )
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "App.tsx").write_text("export default function App() { return <div /> }\n")
    profile = {
        "kind": "web-app",
        "language": "typescript",
        "framework": "react",
        "build_system": "vite",
        "gate_strictness": "relaxed",
    }

    issues = validate_delivery_profile(tmp_path, profile)

    assert any(issue["code"] == "missing-test-file" and issue["severity"] == "warning" for issue in issues)


def test_validate_rejects_react_testing_library_tests_without_jsdom_environment(tmp_path) -> None:
    (tmp_path / "package.json").write_text('{"scripts":{"test":"vitest run"},"devDependencies":{"jsdom":"latest"}}\n')
    (tmp_path / "vite.config.ts").write_text(
        "import { defineConfig } from 'vite'\n"
        "import react from '@vitejs/plugin-react'\n"
        "export default defineConfig({ plugins: [react()] })\n"
    )
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "App.test.tsx").write_text(
        "import { render } from '@testing-library/react'\n"
        "import App from './App'\n"
        "test('renders', () => render(<App />))\n"
    )
    profile = {
        "kind": "web-app",
        "language": "typescript",
        "framework": "react",
        "build_system": "vite",
    }

    issues = validate_delivery_profile(tmp_path, profile)

    assert any(issue["code"] == "missing-jsdom-test-environment" for issue in issues)
    assert any(issue["code"] == "missing-jsdom-test-environment" and issue["severity"] == "error" for issue in issues)


def test_validate_accepts_react_testing_library_tests_with_jsdom_environment(tmp_path) -> None:
    (tmp_path / "package.json").write_text('{"scripts":{"test":"vitest run"},"devDependencies":{"jsdom":"latest"}}\n')
    (tmp_path / "vite.config.ts").write_text(
        "import { defineConfig } from 'vitest/config'\n"
        "import react from '@vitejs/plugin-react'\n"
        "export default defineConfig({ plugins: [react()], test: { environment: 'jsdom' } })\n"
    )
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "App.test.tsx").write_text(
        "import { render } from '@testing-library/react'\n"
        "import App from './App'\n"
        "test('renders', () => render(<App />))\n"
    )
    profile = {
        "kind": "web-app",
        "language": "typescript",
        "framework": "react",
        "build_system": "vite",
    }

    issues = validate_delivery_profile(tmp_path, profile)

    assert not any(issue["code"] == "missing-jsdom-test-environment" for issue in issues)


def test_validate_rejects_bare_jest_dom_import_in_vitest_setup(tmp_path) -> None:
    (tmp_path / "package.json").write_text(
        '{"scripts":{"test":"vitest run"},"devDependencies":{"@testing-library/jest-dom":"latest","jsdom":"latest"}}\n'
    )
    (tmp_path / "vite.config.ts").write_text(
        "import { defineConfig } from 'vitest/config'\n"
        "export default defineConfig({ test: { environment: 'jsdom', setupFiles: ['./src/setupTests.ts'] } })\n"
    )
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "setupTests.ts").write_text("import '@testing-library/jest-dom'\n")
    (tmp_path / "src" / "App.test.tsx").write_text(
        "import { render, screen } from '@testing-library/react'\n"
        "import App from './App'\n"
        "test('renders', () => { render(<App />); expect(screen.getByText('Hi')).toHaveTextContent('Hi') })\n"
    )
    (tmp_path / "src" / "App.tsx").write_text("export default function App() { return <div>Hi</div> }\n")
    profile = {
        "kind": "web-app",
        "language": "typescript",
        "framework": "react",
        "build_system": "vite",
    }

    issues = validate_delivery_profile(tmp_path, profile)

    assert any(issue["code"] == "bare-jest-dom-import" and issue["severity"] == "error" for issue in issues)


def test_validate_rejects_css_at_import_in_typescript_setup_files(tmp_path) -> None:
    (tmp_path / "package.json").write_text(
        '{"scripts":{"test":"vitest run"},"devDependencies":{"@testing-library/jest-dom":"latest","jsdom":"latest"}}\n'
    )
    (tmp_path / "vite.config.ts").write_text(
        "import { defineConfig } from 'vitest/config'\n"
        "export default defineConfig({ test: { environment: 'jsdom', setupFiles: ['./src/setupTests.ts'] } })\n"
    )
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "setupTests.ts").write_text("@import '@testing-library/jest-dom/vitest';\n")
    (tmp_path / "src" / "App.test.tsx").write_text(
        "import { expect, test } from 'vitest'\n"
        "test('uses matcher', () => expect(document.body).toBeInTheDocument())\n"
    )
    profile = {
        "kind": "web-app",
        "language": "typescript",
        "framework": "react",
        "build_system": "vite",
    }

    issues = validate_delivery_profile(tmp_path, profile)

    assert any(issue["code"] == "invalid-typescript-at-import" and issue["severity"] == "error" for issue in issues)


def test_validate_rejects_missing_postcss_plugin_dependencies(tmp_path) -> None:
    (tmp_path / "package.json").write_text(
        '{"scripts":{"test":"vitest run"},"dependencies":{"react":"latest","react-dom":"latest"},"devDependencies":{"vite":"latest","vitest":"latest"}}\n'
    )
    (tmp_path / "postcss.config.js").write_text(
        "export default {\n"
        "  plugins: {\n"
        "    tailwindcss: {},\n"
        "    autoprefixer: {},\n"
        "  },\n"
        "}\n"
    )
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "App.test.tsx").write_text("import { test } from 'vitest'\ntest('ok', () => {})\n")
    profile = {"stack_id": "react-vite", "gate_strictness": "relaxed"}

    issues = validate_delivery_profile(tmp_path, profile)

    assert any(
        issue["code"] == "missing-postcss-plugin-dependency"
        and issue["severity"] == "error"
        and "tailwindcss" in issue["message"]
        and "autoprefixer" in issue["message"]
        for issue in issues
    )


def test_validate_rejects_jest_dom_matchers_without_declared_dependency(tmp_path) -> None:
    (tmp_path / "package.json").write_text(
        '{"scripts":{"test":"vitest run"},"devDependencies":{"jsdom":"latest"}}\n'
    )
    (tmp_path / "vite.config.ts").write_text(
        "import { defineConfig } from 'vitest/config'\n"
        "export default defineConfig({ test: { environment: 'jsdom', setupFiles: ['./src/setupTests.ts'] } })\n"
    )
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "setupTests.ts").write_text("import '@testing-library/jest-dom/vitest'\n")
    (tmp_path / "src" / "App.test.tsx").write_text(
        "import { render, screen } from '@testing-library/react'\n"
        "import App from './App'\n"
        "test('renders', () => { render(<App />); expect(screen.getByText('Hi')).toHaveTextContent('Hi') })\n"
    )
    (tmp_path / "src" / "App.tsx").write_text("export default function App() { return <div>Hi</div> }\n")
    profile = {
        "kind": "web-app",
        "language": "typescript",
        "framework": "react",
        "build_system": "vite",
    }

    issues = validate_delivery_profile(tmp_path, profile)

    assert any(issue["code"] == "missing-jest-dom-dependency" and issue["severity"] == "error" for issue in issues)


def test_validate_accepts_jest_dom_vitest_setup_with_dependency(tmp_path) -> None:
    (tmp_path / "package.json").write_text(
        '{"scripts":{"test":"vitest run"},"devDependencies":{"@testing-library/jest-dom":"latest","jsdom":"latest"}}\n'
    )
    (tmp_path / "vite.config.ts").write_text(
        "import { defineConfig } from 'vitest/config'\n"
        "export default defineConfig({ test: { environment: 'jsdom', setupFiles: ['./src/setupTests.ts'] } })\n"
    )
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "setupTests.ts").write_text("import '@testing-library/jest-dom/vitest'\n")
    (tmp_path / "src" / "App.test.tsx").write_text(
        "import { render, screen } from '@testing-library/react'\n"
        "import App from './App'\n"
        "test('renders', () => { render(<App />); expect(screen.getByText('Hi')).toHaveTextContent('Hi') })\n"
    )
    (tmp_path / "src" / "App.tsx").write_text("export default function App() { return <div>Hi</div> }\n")
    profile = {
        "kind": "web-app",
        "language": "typescript",
        "framework": "react",
        "build_system": "vite",
    }

    issues = validate_delivery_profile(tmp_path, profile)

    assert not any(issue["code"] in {"bare-jest-dom-import", "missing-jest-dom-dependency"} for issue in issues)


def test_validate_rejects_vitest_globals_used_without_imports_or_globals_config(tmp_path) -> None:
    (tmp_path / "package.json").write_text(
        '{"scripts":{"test":"vitest run"},"devDependencies":{"jsdom":"latest","vitest":"latest"}}\n'
    )
    (tmp_path / "vite.config.ts").write_text(
        "import { defineConfig } from 'vitest/config'\n"
        "export default defineConfig({ test: { environment: 'jsdom' } })\n"
    )
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "App.test.tsx").write_text(
        "import { render } from '@testing-library/react'\n"
        "import App from './App'\n"
        "describe('App', () => { it('renders', () => { render(<App />); expect(true).toBe(true) }) })\n"
    )
    (tmp_path / "src" / "App.tsx").write_text("export default function App() { return <div /> }\n")
    profile = {
        "kind": "web-app",
        "language": "typescript",
        "framework": "react",
        "build_system": "vite",
        "gate_strictness": "relaxed",
    }

    issues = validate_delivery_profile(tmp_path, profile)

    assert any(issue["code"] == "vitest-global-api-mismatch" and issue["severity"] == "error" for issue in issues)


def test_validate_accepts_vitest_globals_used_with_globals_config(tmp_path) -> None:
    (tmp_path / "package.json").write_text('{"scripts":{"test":"vitest run"}}\n')
    (tmp_path / "vite.config.ts").write_text(
        "import { defineConfig } from 'vitest/config'\n"
        "export default defineConfig({ test: { environment: 'jsdom', globals: true } })\n"
    )
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "App.test.tsx").write_text(
        "describe('App', () => { it('renders', () => { expect(true).toBe(true) }) })\n"
    )
    profile = {
        "kind": "web-app",
        "language": "typescript",
        "framework": "react",
        "build_system": "vite",
    }

    issues = validate_delivery_profile(tmp_path, profile)

    assert not any(issue["code"] == "vitest-global-api-mismatch" for issue in issues)


def test_validate_rejects_vitest_globals_without_types_config(tmp_path) -> None:
    (tmp_path / "package.json").write_text('{"scripts":{"test":"vitest run"}}\n')
    (tmp_path / "vite.config.ts").write_text(
        "import { defineConfig } from 'vitest/config'\n"
        "export default defineConfig({ test: { environment: 'jsdom', globals: true } })\n"
    )
    (tmp_path / "tsconfig.json").write_text(
        '{"compilerOptions":{"types":["node"]},"include":["src"]}\n'
    )
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "App.test.tsx").write_text(
        "import { describe, expect, it } from 'vitest'\n"
        "const onClick = vi.fn()\n"
        "describe('App', () => { it('renders', () => { expect(onClick).toBeDefined() }) })\n"
    )
    profile = {"stack_id": "react-vite", "gate_strictness": "relaxed"}

    issues = validate_delivery_profile(tmp_path, profile)

    issue = next(issue for issue in issues if issue["code"] == "vitest-global-types-missing")
    assert issue["severity"] == "error"
    assert "src/App.test.tsx uses vi" in issue["message"]
    assert "vitest/globals" in issue["message"]


def test_validate_accepts_vitest_globals_with_types_config(tmp_path) -> None:
    (tmp_path / "package.json").write_text('{"scripts":{"test":"vitest run"}}\n')
    (tmp_path / "vite.config.ts").write_text(
        "import { defineConfig } from 'vitest/config'\n"
        "export default defineConfig({ test: { environment: 'jsdom', globals: true } })\n"
    )
    (tmp_path / "tsconfig.json").write_text(
        '{"compilerOptions":{"types":["vitest/globals"]},"include":["src"]}\n'
    )
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "App.test.tsx").write_text(
        "const onClick = vi.fn()\n"
        "describe('App', () => { it('renders', () => { expect(onClick).toBeDefined() }) })\n"
    )
    profile = {"stack_id": "react-vite"}

    issues = validate_delivery_profile(tmp_path, profile)

    assert not any(issue["code"] == "vitest-global-types-missing" for issue in issues)


def test_validate_accepts_vitest_globals_when_imported_from_vitest(tmp_path) -> None:
    (tmp_path / "package.json").write_text('{"scripts":{"test":"vitest run"}}\n')
    (tmp_path / "vite.config.ts").write_text(
        "import { defineConfig } from 'vitest/config'\n"
        "export default defineConfig({ test: { environment: 'jsdom' } })\n"
    )
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "App.test.tsx").write_text(
        "import { describe, expect, it } from 'vitest'\n"
        "describe('App', () => { it('renders', () => { expect(true).toBe(true) }) })\n"
    )
    profile = {
        "kind": "web-app",
        "language": "typescript",
        "framework": "react",
        "build_system": "vite",
    }

    issues = validate_delivery_profile(tmp_path, profile)

    assert not any(issue["code"] == "vitest-global-api-mismatch" for issue in issues)


def test_validate_rejects_missing_tsconfig_project_reference(tmp_path) -> None:
    (tmp_path / "package.json").write_text('{"scripts":{"test":"vitest run"}}\n')
    (tmp_path / "vite.config.ts").write_text(
        "import { defineConfig } from 'vitest/config'\n"
        "export default defineConfig({ test: { environment: 'jsdom' } })\n"
    )
    (tmp_path / "tsconfig.json").write_text(
        '{"include":["src"],"references":[{"path":"./tsconfig.node.json"}]}\n'
    )
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "App.tsx").write_text("export default function App() { return <div /> }\n")
    (tmp_path / "src" / "App.test.tsx").write_text(
        "import { describe, expect, it } from 'vitest'\n"
        "describe('App', () => { it('renders', () => { expect(true).toBe(true) }) })\n"
    )
    profile = {"stack_id": "react-vite"}

    issues = validate_delivery_profile(tmp_path, profile)

    issue = next(issue for issue in issues if issue["code"] == "missing-tsconfig-reference")
    assert issue["severity"] == "error"
    assert "`tsconfig.json` references `./tsconfig.node.json`" in issue["message"]
    assert issue["paths"] == ["tsconfig.json", "tsconfig.node.json"]


def test_validate_accepts_existing_tsconfig_project_reference(tmp_path) -> None:
    (tmp_path / "package.json").write_text('{"scripts":{"test":"vitest run"}}\n')
    (tmp_path / "vite.config.ts").write_text(
        "import { defineConfig } from 'vitest/config'\n"
        "export default defineConfig({ test: { environment: 'jsdom' } })\n"
    )
    (tmp_path / "tsconfig.json").write_text(
        '{"include":["src"],"references":[{"path":"./tsconfig.node.json"}]}\n'
    )
    (tmp_path / "tsconfig.node.json").write_text('{"include":["vite.config.ts"]}\n')
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "App.tsx").write_text("export default function App() { return <div /> }\n")
    (tmp_path / "src" / "App.test.tsx").write_text(
        "import { describe, expect, it } from 'vitest'\n"
        "describe('App', () => { it('renders', () => { expect(true).toBe(true) }) })\n"
    )
    profile = {"stack_id": "react-vite"}

    issues = validate_delivery_profile(tmp_path, profile)

    assert not any(issue["code"] == "missing-tsconfig-reference" for issue in issues)


def test_validate_rejects_react_vite_test_importing_missing_relative_module(tmp_path) -> None:
    (tmp_path / "package.json").write_text('{"scripts":{"test":"vitest run"}}\n')
    (tmp_path / "vitest.config.ts").write_text(
        "import { defineConfig } from 'vitest/config'\n"
        "export default defineConfig({ test: { environment: 'jsdom' } })\n"
    )
    (tmp_path / "src" / "components").mkdir(parents=True)
    (tmp_path / "src" / "components" / "Board.test.tsx").write_text(
        "import { render } from '@testing-library/react'\n"
        "import Board from './Board'\n"
        "test('renders', () => render(<Board />))\n"
    )
    profile = {
        "kind": "web-app",
        "language": "typescript",
        "framework": "react",
        "build_system": "vite",
    }

    issues = validate_delivery_profile(tmp_path, profile)

    assert any(issue["code"] == "unresolved-relative-import" for issue in issues)


def test_repair_creates_missing_react_vite_css_import(tmp_path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "App.tsx").write_text("import './App.css'\nexport default function App() { return <div /> }\n")

    changed = repair_unique_unresolved_relative_imports(tmp_path)

    assert changed == ["src/App.css"]
    assert (tmp_path / "src" / "App.css").is_file()
    assert not any(
        issue["code"] == "unresolved-relative-import"
        for issue in validate_delivery_profile(tmp_path, {"stack_id": "react-vite"})
    )


def test_validate_suggests_existing_relative_import_target(tmp_path) -> None:
    (tmp_path / "package.json").write_text('{"scripts":{"test":"vitest run"}}\n')
    (tmp_path / "vitest.config.ts").write_text(
        "import { defineConfig } from 'vitest/config'\n"
        "export default defineConfig({ test: { environment: 'jsdom' } })\n"
    )
    (tmp_path / "src" / "hooks").mkdir(parents=True)
    (tmp_path / "src" / "hooks" / "useGameState.ts").write_text(
        "import { Board } from './types'\n"
    )
    (tmp_path / "src" / "types.ts").write_text("export type Board = unknown\n")
    (tmp_path / "src" / "App.test.tsx").write_text(
        "import { describe, expect, it } from 'vitest'\n"
        "describe('App', () => { it('renders', () => { expect(true).toBe(true) }) })\n"
    )
    profile = {"stack_id": "react-vite"}

    issues = validate_delivery_profile(tmp_path, profile)

    issue = next(issue for issue in issues if issue["code"] == "unresolved-relative-import")
    assert issue["paths"] == ["src/hooks/useGameState.ts", "src/types.ts"]
    assert "likely target `src/types.ts`" in issue["repair_hint"]
    assert "import `../types`" in issue["repair_hint"]


def test_validate_suggests_existing_relative_require_target(tmp_path) -> None:
    (tmp_path / "package.json").write_text('{"scripts":{"test":"vitest run"}}\n')
    (tmp_path / "vitest.config.ts").write_text(
        "import { defineConfig } from 'vitest/config'\n"
        "export default defineConfig({ test: { environment: 'jsdom' } })\n"
    )
    (tmp_path / "src" / "hooks").mkdir(parents=True)
    (tmp_path / "src" / "hooks" / "useGameState.ts").write_text("export const checkDraw = () => false\n")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "GameState.test.tsx").write_text(
        "import { describe, expect, it } from 'vitest'\n"
        "describe('draw', () => {\n"
        "  it('checks draw', () => {\n"
        "    const { checkDraw } = require('../hooks/useGameState')\n"
        "    expect(checkDraw()).toBe(false)\n"
        "  })\n"
        "})\n"
    )
    profile = {"stack_id": "react-vite"}

    issues = validate_delivery_profile(tmp_path, profile)

    issue = next(issue for issue in issues if issue["code"] == "unresolved-relative-import")
    assert issue["paths"] == ["tests/GameState.test.tsx", "src/hooks/useGameState.ts"]
    assert "import `../src/hooks/useGameState`" in issue["repair_hint"]


def test_validate_react_vite_checks_can_be_selected_by_stack_id(tmp_path) -> None:
    (tmp_path / "package.json").write_text('{"scripts":{"test":"vitest run"}}\n')
    (tmp_path / "vite.config.ts").write_text(
        "import { defineConfig } from 'vite'\n"
        "export default defineConfig({})\n"
    )
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "App.tsx").write_text("export default function App() { return <div /> }\n")
    profile = {
        "stack_id": "react-vite",
        "gate_strictness": "relaxed",
    }

    issues = validate_delivery_profile(tmp_path, profile)

    assert any(issue["code"] == "missing-test-file" for issue in issues)


def test_validate_rejects_duplicate_react_vite_types_module_sources(tmp_path) -> None:
    (tmp_path / "package.json").write_text('{"scripts":{"test":"vitest run"}}\n')
    (tmp_path / "vite.config.ts").write_text(
        "import { defineConfig } from 'vitest/config'\n"
        "export default defineConfig({ test: { environment: 'jsdom' } })\n"
    )
    (tmp_path / "src" / "types").mkdir(parents=True)
    (tmp_path / "src" / "types.ts").write_text("export interface CellState {}\n")
    (tmp_path / "src" / "types" / "index.ts").write_text("export interface Cell {}\n")
    (tmp_path / "src" / "App.test.tsx").write_text(
        "import { describe, expect, it } from 'vitest'\n"
        "describe('App', () => { it('renders', () => { expect(true).toBe(true) }) })\n"
    )
    profile = {"stack_id": "react-vite", "gate_strictness": "relaxed"}

    issues = validate_delivery_profile(tmp_path, profile)

    issue = next(issue for issue in issues if issue["code"] == "duplicate-types-module")
    assert issue["severity"] == "error"
    assert issue["paths"] == ["src/types.ts", "src/types/index.ts"]
    assert "single canonical shared type module" in issue["message"]


def test_validate_rejects_nested_react_vite_worktree_project(tmp_path) -> None:
    (tmp_path / "package.json").write_text('{"scripts":{"test":"vitest run"}}\n')
    (tmp_path / "vite.config.ts").write_text(
        "import { defineConfig } from 'vitest/config'\n"
        "export default defineConfig({ test: { environment: 'jsdom' } })\n"
    )
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "App.test.tsx").write_text(
        "import { describe, expect, it } from 'vitest'\n"
        "describe('App', () => { it('renders', () => { expect(true).toBe(true) }) })\n"
    )
    (tmp_path / "src" / "App.tsx").write_text("export default function App() { return <div /> }\n")
    (tmp_path / "worktree" / "src").mkdir(parents=True)
    (tmp_path / "worktree" / "index.html").write_text("<div id=\"root\"></div>\n")
    (tmp_path / "worktree" / "src" / "App.tsx").write_text(
        "export default function ShadowApp() { return <div /> }\n"
    )
    profile = {
        "stack_id": "react-vite",
        "gate_strictness": "relaxed",
    }

    issues = validate_delivery_profile(tmp_path, profile)

    assert any(issue["code"] == "nested-worktree-project" and issue["severity"] == "error" for issue in issues)


def test_validate_rejects_ambiguous_board_coordinate_testing_library_queries(tmp_path) -> None:
    (tmp_path / "package.json").write_text('{"scripts":{"test":"vitest run"}}\n')
    (tmp_path / "vite.config.ts").write_text(
        "import { defineConfig } from 'vitest/config'\n"
        "export default defineConfig({ test: { environment: 'jsdom' } })\n"
    )
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "App.test.tsx").write_text(
        "import { describe, expect, it } from 'vitest'\n"
        "import { screen } from '@testing-library/react'\n"
        "describe('App', () => {\n"
        "  it('clicks a board cell', () => {\n"
        "    screen.getByRole('button', { name: /行1列1.*空/i })\n"
        "    expect(true).toBe(true)\n"
        "  })\n"
        "})\n"
    )
    profile = {"stack_id": "react-vite", "gate_strictness": "relaxed"}

    issues = validate_delivery_profile(tmp_path, profile)

    assert any(
        issue["code"] == "ambiguous-testing-library-query" and issue["severity"] == "error"
        for issue in issues
    )


def test_validate_accepts_exact_board_coordinate_testing_library_queries(tmp_path) -> None:
    (tmp_path / "package.json").write_text('{"scripts":{"test":"vitest run"}}\n')
    (tmp_path / "vite.config.ts").write_text(
        "import { defineConfig } from 'vitest/config'\n"
        "export default defineConfig({ test: { environment: 'jsdom' } })\n"
    )
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "App.test.tsx").write_text(
        "import { describe, expect, it } from 'vitest'\n"
        "import { screen } from '@testing-library/react'\n"
        "describe('App', () => {\n"
        "  it('clicks a board cell', () => {\n"
        "    screen.getByRole('button', { name: /^行1列1, 空$/i })\n"
        "    expect(true).toBe(true)\n"
        "  })\n"
        "})\n"
    )
    profile = {"stack_id": "react-vite", "gate_strictness": "relaxed"}

    issues = validate_delivery_profile(tmp_path, profile)

    assert not any(issue["code"] == "ambiguous-testing-library-query" for issue in issues)


def test_validate_rejects_low_level_user_pointerdown_touch_tests(tmp_path) -> None:
    (tmp_path / "package.json").write_text('{"scripts":{"test":"vitest run"}}\n')
    (tmp_path / "vite.config.ts").write_text(
        "import { defineConfig } from 'vitest/config'\n"
        "export default defineConfig({ test: { environment: 'jsdom' } })\n"
    )
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "Board.test.tsx").write_text(
        "import { describe, expect, it } from 'vitest'\n"
        "import userEvent from '@testing-library/user-event'\n"
        "describe('Board', () => {\n"
        "  it('supports touch', async () => {\n"
        "    await userEvent.setup().pointer({ target: cell, keys: '[pointerdown]' })\n"
        "    expect(document.querySelector('[data-testid=\"stone-0-0\"]')).toBeInTheDocument()\n"
        "  })\n"
        "})\n"
    )
    profile = {"stack_id": "react-vite", "gate_strictness": "relaxed"}

    issues = validate_delivery_profile(tmp_path, profile)

    assert any(issue["code"] == "low-level-pointerdown-test" and issue["severity"] == "error" for issue in issues)


def test_validate_rejects_layout_dependent_react_vite_tests_without_rect_mock(tmp_path) -> None:
    (tmp_path / "package.json").write_text('{"scripts":{"test":"vitest run"}}\n')
    (tmp_path / "vite.config.ts").write_text(
        "import { defineConfig } from 'vitest/config'\n"
        "export default defineConfig({ test: { environment: 'jsdom' } })\n"
    )
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "App.test.tsx").write_text(
        "import { fireEvent } from '@testing-library/react'\n"
        "test('clicks board', () => {\n"
        "  const rect = board.getBoundingClientRect()\n"
        "  fireEvent.click(board, { clientX: rect.left + rect.width / 2, clientY: rect.top + 1 })\n"
        "})\n"
    )
    profile = {
        "stack_id": "react-vite",
        "gate_strictness": "relaxed",
    }

    issues = validate_delivery_profile(tmp_path, profile)

    assert any(issue["code"] == "jsdom-layout-dependent-test" and issue["severity"] == "error" for issue in issues)


def test_validate_accepts_layout_dependent_react_vite_tests_with_rect_mock(tmp_path) -> None:
    (tmp_path / "package.json").write_text('{"scripts":{"test":"vitest run"}}\n')
    (tmp_path / "vite.config.ts").write_text(
        "import { defineConfig } from 'vitest/config'\n"
        "export default defineConfig({ test: { environment: 'jsdom' } })\n"
    )
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "App.test.tsx").write_text(
        "import { fireEvent } from '@testing-library/react'\n"
        "test('clicks board', () => {\n"
        "  vi.spyOn(board, 'getBoundingClientRect').mockReturnValue({ left: 0, top: 0, width: 300, height: 300 })\n"
        "  const rect = board.getBoundingClientRect()\n"
        "  fireEvent.click(board, { clientX: rect.left + rect.width / 2, clientY: rect.top + 1 })\n"
        "})\n"
    )
    profile = {"stack_id": "react-vite"}

    issues = validate_delivery_profile(tmp_path, profile)

    assert not any(issue["code"] == "jsdom-layout-dependent-test" for issue in issues)


def test_validate_rejects_computed_style_layout_assertions_in_jsdom_tests(tmp_path) -> None:
    (tmp_path / "package.json").write_text('{"scripts":{"test":"vitest run"}}\n')
    (tmp_path / "vite.config.ts").write_text(
        "import { defineConfig } from 'vitest/config'\n"
        "export default defineConfig({ test: { environment: 'jsdom' } })\n"
    )
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "Board.test.tsx").write_text(
        "test('centers the board', () => {\n"
        "  const styles = window.getComputedStyle(board.parentElement!)\n"
        "  expect(styles.display).toBe('flex')\n"
        "  expect(styles.justifyContent).toBe('center')\n"
        "})\n"
    )
    profile = {"stack_id": "react-vite", "gate_strictness": "relaxed"}

    issues = validate_delivery_profile(tmp_path, profile)

    assert any(
        issue["code"] == "jsdom-computed-style-layout-test" and issue["severity"] == "error"
        for issue in issues
    )


def test_validate_accepts_react_vite_test_importing_existing_relative_module(tmp_path) -> None:
    (tmp_path / "package.json").write_text('{"scripts":{"test":"vitest run"}}\n')
    (tmp_path / "vitest.config.ts").write_text(
        "import { defineConfig } from 'vitest/config'\n"
        "export default defineConfig({ test: { environment: 'jsdom' } })\n"
    )
    (tmp_path / "src" / "components").mkdir(parents=True)
    (tmp_path / "src" / "components" / "Board.test.tsx").write_text(
        "import { render } from '@testing-library/react'\n"
        "import Board from './Board'\n"
        "test('renders', () => render(<Board />))\n"
    )
    (tmp_path / "src" / "components" / "Board.tsx").write_text(
        "export default function Board() { return <div /> }\n"
    )
    profile = {
        "kind": "web-app",
        "language": "typescript",
        "framework": "react",
        "build_system": "vite",
    }

    issues = validate_delivery_profile(tmp_path, profile)

    assert not any(issue["code"] == "unresolved-relative-import" for issue in issues)


def test_validate_accepts_relative_import_with_dotted_module_name(tmp_path) -> None:
    (tmp_path / "package.json").write_text('{"scripts":{"test":"vitest run"}}\n')
    (tmp_path / "vite.config.ts").write_text(
        "import { defineConfig } from 'vitest/config'\n"
        "export default defineConfig({ test: { environment: 'jsdom' } })\n"
    )
    (tmp_path / "src" / "components").mkdir(parents=True)
    (tmp_path / "src" / "components" / "Board.test.tsx").write_text(
        "import { makeBoard } from './Board.test.utils'\n"
        "test('renders', () => expect(makeBoard()).toEqual([]))\n"
    )
    (tmp_path / "src" / "components" / "Board.test.utils.ts").write_text(
        "export const makeBoard = () => []\n"
    )
    profile = {"stack_id": "react-vite"}

    issues = validate_delivery_profile(tmp_path, profile)

    assert not any(issue["code"] == "unresolved-relative-import" for issue in issues)


def test_execution_profile_for_react_vite_installs_and_runs_ci_tests() -> None:
    profile = {
        "kind": "web-app",
        "language": "typescript",
        "framework": "react",
        "build_system": "vite",
        "test_command": "npm test",
    }

    execution = execution_profile_for_delivery(profile)

    assert execution == {
        "install_command": ["npm", "install", "--no-audit", "--fund=false"],
        "pre_test_command": ["npm", "run", "build"],
        "test_command": ["npm", "test"],
        "env": {"CI": "true"},
        "ignored_paths": ["node_modules", "dist", "coverage"],
    }


def test_execution_profile_can_be_selected_by_stack_id() -> None:
    execution = execution_profile_for_delivery({
        "stack_id": "react-vite",
        "test_command": "npm run test:unit",
    })

    assert execution["install_command"] == ["npm", "install", "--no-audit", "--fund=false"]
    assert execution["pre_test_command"] == ["npm", "run", "build"]
    assert execution["test_command"] == ["npm", "run", "test:unit"]
    assert execution["env"] == {"CI": "true"}


def test_react_vite_delivery_rejects_absolute_stone_class_on_cell(tmp_path: Path) -> None:
    (tmp_path / "src" / "components").mkdir(parents=True)
    (tmp_path / "src" / "components" / "Board.tsx").write_text(
        "export function Board({ cell }) {\n"
        "  const stoneClass = cell ? `stone ${cell}` : ''\n"
        "  return <button className={`cell ${stoneClass}`} />\n"
        "}\n"
    )
    (tmp_path / "src" / "styles.css").write_text(
        ".cell { position: relative; }\n"
        ".stone { position: absolute; width: 80%; height: 80%; border-radius: 50%; }\n"
    )
    (tmp_path / "package.json").write_text(
        '{"scripts": {"test": "vitest run", "build": "vite build"}}\n'
    )
    (tmp_path / "index.html").write_text('<div id="root"></div>\n')

    issues = validate_delivery_profile(tmp_path, {"stack_id": "react-vite"})

    assert any(issue["code"] == "react-vite-stone-class-on-cell" for issue in issues)


def test_react_vite_delivery_allows_nested_absolute_stone_span(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir(parents=True)
    (tmp_path / "src" / "Board.tsx").write_text(
        "export function Board({ stone }) {\n"
        "  return (\n"
        "    <button className=\"cell\" data-stone={stone}>\n"
        "      {stone && <span className={`stone ${stone}`} />}\n"
        "    </button>\n"
        "  )\n"
        "}\n"
    )
    (tmp_path / "src" / "styles.css").write_text(
        ".cell { position: relative; }\n"
        ".stone { position: absolute; inset: 10%; border-radius: 50%; }\n"
    )
    (tmp_path / "package.json").write_text(
        '{"scripts": {"test": "vitest run", "build": "vite build"}}\n'
    )
    (tmp_path / "index.html").write_text('<div id="root"></div>\n')

    issues = validate_delivery_profile(tmp_path, {"stack_id": "react-vite"})

    assert not any(issue["code"] == "react-vite-stone-class-on-cell" for issue in issues)


def test_execution_profile_for_swift_xcodegen_uses_generate_then_xcodebuild() -> None:
    profile = {
        "kind": "native-macos-app",
        "language": "swift",
        "build_system": "xcodegen",
        "test_command": "xcodegen generate && xcodebuild test -scheme MacCalc",
    }

    execution = execution_profile_for_delivery(profile)

    assert execution == {
        "install_command": None,
        "test_command": ["xcodebuild", "test", "-scheme", "MacCalc"],
        "pre_test_command": ["xcodegen", "generate"],
        "env": {},
        "ignored_paths": ["*.xcodeproj", "DerivedData"],
    }


def test_execution_profile_for_python_cli_adds_src_to_pythonpath() -> None:
    execution = execution_profile_for_delivery({
        "stack_id": "python-cli",
        "kind": "cli",
        "language": "python",
        "test_command": "python -m pytest -q",
    })

    assert execution["install_command"] is None
    assert execution["test_command"] == ["python", "-m", "pytest", "-q"]
    assert "{workdir}/src" in execution["env"]["PYTHONPATH"]
