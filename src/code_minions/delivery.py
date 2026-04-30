from __future__ import annotations

import json
import re
import shlex
from pathlib import Path
from typing import Any

import yaml

from code_minions.stacks import apply_stack_pack_defaults, stack_id_for_delivery

IGNORED_DIRS = {".git", ".devflow", ".pytest_cache", "__pycache__", ".ruff_cache", "node_modules"}

LANGUAGE_BY_SUFFIX = {
    ".go": "go",
    ".js": "javascript",
    ".jsx": "javascript",
    ".py": "python",
    ".rs": "rust",
    ".swift": "swift",
    ".ts": "typescript",
    ".tsx": "typescript",
}

TS_RESOLVABLE_EXTENSIONS = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".json")
TS_IMPORT_RE = re.compile(
    r"""(?:import|export)\s+(?:type\s+)?(?:[^'"]+\s+from\s+)?['"](?P<path>\.{1,2}/[^'"]+)['"]|"""
    r"""import\(\s*['"](?P<dynamic>\.{1,2}/[^'"]+)['"]\s*\)"""
)
DELIVERY_SEVERITIES = {"error", "warning"}
JEST_DOM_MATCHERS = (
    "tohaveattribute",
    "tohaveclass",
    "tohavefocus",
    "tohavetextcontent",
    "tobechecked",
    "tobedisabled",
    "tobeemptydomelement",
    "tobeinthedocument",
    "tobevisible",
)
VITEST_GLOBAL_APIS = ("afterEach", "beforeEach", "describe", "expect", "it", "test", "vi")
VITEST_NAMED_IMPORT_RE = re.compile(
    r"""import\s*\{(?P<names>[^}]+)\}\s*from\s*['"]vitest['"]""",
    re.MULTILINE | re.DOTALL,
)
RECT_MOCK_RE = re.compile(
    r"""(?:spyOn|stubGlobal|defineProperty|mockReturnValue|mockImplementation)[\s\S]{0,240}getBoundingClientRect"""
    r"""|getBoundingClientRect[\s\S]{0,240}(?:mockReturnValue|mockImplementation)""",
    re.MULTILINE,
)
TESTING_LIBRARY_REGEX_QUERY_RE = re.compile(
    r"""\b(?:get|find|query)(?:All)?By(?:Role|LabelText)\s*\([\s\S]{0,320}?"""
    r"""(?:name\s*:\s*)?/(?P<pattern>(?:\\.|[^/\n])*)/[a-z]*""",
    re.MULTILINE,
)
BOARD_COORDINATE_REGEX_RE = re.compile(
    r"""行\s*\d+\s*列\s*\d+|第\s*\d+\s*行\s*第\s*\d+\s*列|"""
    r"""row\s*\d+[\s\S]{0,40}(?:col|column)\s*\d+""",
    re.IGNORECASE,
)
LOW_LEVEL_USER_POINTERDOWN_RE = re.compile(
    r"""\buser(?:Event)?(?:\.setup\(\))?\.pointer\s*\([\s\S]{0,240}\[pointerdown\]""",
    re.MULTILINE,
)
COMPUTED_STYLE_LAYOUT_ASSERTION_RE = re.compile(
    r"""getComputedStyle\s*\([\s\S]{0,320}"""
    r"""expect\s*\([^)]*\.(?:display|justifyContent|alignItems|placeItems|gridTemplateColumns|width|height)[^)]*\)\s*"""
    r"""\.(?:toBe|toEqual|toContain|toMatch)\s*\(""",
    re.MULTILINE,
)
TYPESCRIPT_CSS_AT_IMPORT_RE = re.compile(r"""(?m)^\s*@import\s+['"]""")
POSTCSS_CONFIG_NAMES = (
    "postcss.config.js",
    "postcss.config.cjs",
    "postcss.config.mjs",
    "postcss.config.ts",
)
POSTCSS_PLUGIN_PACKAGES = ("tailwindcss", "autoprefixer", "postcss-preset-env")


def _text_from_prd(structured_prd: dict[str, Any]) -> str:
    parts = []
    for key in ("goal", "constraints", "features", "non_functional"):
        parts.append(str(structured_prd.get(key, "")))
    return "\n".join(parts).lower()


def _looks_like_swift_macos_profile(profile: dict[str, Any], prd_text: str) -> bool:
    text = "\n".join(str(profile.get(key, "")) for key in ("kind", "language", "framework", "build_system"))
    text = f"{prd_text}\n{text}".lower()
    return ("macos" in text or "mac os" in text or "mac app" in text) and (
        "swift" in text or "swiftui" in text or "appkit" in text
    )


def _swift_macos_profile(*, xcodegen: bool = True) -> dict[str, Any]:
    return apply_stack_pack_defaults({
        "stack_id": "swift-xcodegen" if xcodegen else "",
        "kind": "native-macos-app",
        "language": "swift",
        "framework": "swiftui",
        "build_system": "xcodegen" if xcodegen else "xcode",
        "test_command": (
            "xcodegen generate && xcodebuild test -scheme MacCalc"
            if xcodegen
            else "xcodebuild test -scheme <scheme>"
        ),
        "required_files": ["project.yml", "**/*.swift", "**/*App.swift"] if xcodegen else ["**/*.swift", "**/*App.swift"],
        "forbidden_product_languages": ["python", "javascript", "typescript", "go", "rust"],
    })


def _normalized_explicit_profile(profile: dict[str, Any], prd_text: str) -> dict[str, Any]:
    if _looks_like_swift_macos_profile(profile, prd_text):
        normalized = _swift_macos_profile(xcodegen=True)
        for key, value in profile.items():
            if value not in (None, "", []):
                normalized[key] = value
        normalized["kind"] = "native-macos-app"
        normalized["language"] = "swift"
        normalized["framework"] = "swiftui"
        if str(profile.get("build_system", "")).lower() in {"", "xcode 16+", "xcode", "xcodegen"}:
            normalized["build_system"] = "xcodegen"
            normalized["test_command"] = "xcodegen generate && xcodebuild test -scheme MacCalc"
            normalized["required_files"] = ["project.yml", "**/*.swift", "**/*App.swift"]
        normalized["forbidden_product_languages"] = [
            "python",
            "javascript",
            "typescript",
            "go",
            "rust",
        ]
        return apply_stack_pack_defaults(normalized)
    return apply_stack_pack_defaults(profile)


def infer_delivery_profile(structured_prd: dict[str, Any]) -> dict[str, Any]:
    text = _text_from_prd(structured_prd)
    profile = structured_prd.get("delivery_profile")
    if isinstance(profile, dict) and profile:
        return _normalized_explicit_profile(profile, text)

    if ("macos" in text or "mac app" in text or "mac os" in text) and (
        "swift" in text or "swiftui" in text or "appkit" in text
    ):
        build_system = "xcodegen" if "xcodegen" in text or "project.yml" in text else "xcode"
        return _swift_macos_profile(xcodegen=build_system == "xcodegen")

    if "go" in text and ("web service" in text or "http" in text or "api" in text):
        return {
            "stack_id": "go-service",
            "kind": "web-service",
            "language": "go",
            "build_system": "go-mod",
            "test_command": "go test ./...",
            "required_files": ["go.mod", "**/*.go"],
            "forbidden_product_languages": ["python", "javascript", "typescript", "swift"],
        }

    if "python" in text and ("cli" in text or "command line" in text):
        return {
            "stack_id": "python-cli",
            "kind": "cli",
            "language": "python",
            "build_system": "python",
            "test_command": "python -m pytest -q",
            "required_files": ["**/*.py"],
            "forbidden_product_languages": [],
        }

    return {}


def _iter_files(workdir: Path) -> list[Path]:
    files: list[Path] = []
    for path in workdir.rglob("*"):
        rel_parts = path.relative_to(workdir).parts
        if any(part in IGNORED_DIRS for part in rel_parts):
            continue
        if path.is_file():
            files.append(path)
    return files


def _matches_required_file(workdir: Path, pattern: str) -> bool:
    matches = list(workdir.glob(pattern))
    return any(path.is_file() or path.is_dir() for path in matches)


def language_counts(workdir: Path) -> dict[str, int]:
    counts: dict[str, int] = {}
    for path in _iter_files(workdir):
        language = LANGUAGE_BY_SUFFIX.get(path.suffix)
        if language:
            counts[language] = counts.get(language, 0) + 1
    return counts


def execution_profile_for_delivery(profile: dict[str, Any] | None) -> dict[str, Any]:
    if not profile:
        return {}

    stack_id = stack_id_for_delivery(profile)
    text = "\n".join(
        str(profile.get(key, ""))
        for key in ("kind", "language", "framework", "build_system", "test_command")
    ).lower()

    if stack_id == "react-vite" or ("typescript" in text and ("react" in text or "vite" in text or "web-app" in text)):
        test_command = str(profile.get("test_command") or "npm test")
        return {
            "install_command": ["npm", "install", "--no-audit", "--fund=false"],
            "pre_test_command": [
                "npx",
                "tsc",
                "--noEmit",
                "--noUnusedLocals",
                "false",
                "--noUnusedParameters",
                "false",
            ],
            "test_command": shlex.split(test_command),
            "env": {"CI": "true"},
            "ignored_paths": ["node_modules", "dist", "coverage"],
        }

    if stack_id == "swift-xcodegen" or ("swift" in text and "xcodegen" in text):
        command = str(profile.get("test_command", ""))
        scheme_match = re.search(r"-scheme\s+([A-Za-z0-9_.-]+)", command)
        scheme = scheme_match.group(1) if scheme_match else "MacCalc"
        return {
            "install_command": None,
            "test_command": ["xcodebuild", "test", "-scheme", scheme],
            "pre_test_command": ["xcodegen", "generate"],
            "env": {},
            "ignored_paths": ["*.xcodeproj", "DerivedData"],
        }

    return {}


def _is_swift_xcodegen_profile(profile: dict[str, Any]) -> bool:
    if stack_id_for_delivery(profile) == "swift-xcodegen":
        return True
    text = "\n".join(str(profile.get(key, "")) for key in ("kind", "language", "build_system")).lower()
    return "swift" in text and ("xcodegen" in text or "native-macos-app" in text)


def _is_react_vite_profile(profile: dict[str, Any]) -> bool:
    if stack_id_for_delivery(profile) == "react-vite":
        return True
    text = "\n".join(
        str(profile.get(key, ""))
        for key in ("kind", "language", "framework", "build_system", "test_command")
    ).lower()
    return "typescript" in text and "react" in text and "vite" in text


def _gate_strictness(profile: dict[str, Any]) -> str:
    strictness = str(profile.get("gate_strictness") or "balanced").strip().lower()
    if strictness in {"relaxed", "balanced", "strict"}:
        return strictness
    return "balanced"


def _delivery_issue(code: str, message: str, severity: str = "error") -> dict[str, str]:
    if severity not in DELIVERY_SEVERITIES:
        severity = "error"
    return {"code": code, "severity": severity, "message": message}


def _react_vite_hygiene_severity(profile: dict[str, Any]) -> str:
    return "warning" if _gate_strictness(profile) == "relaxed" else "error"


def _swift_test_files_in_product_sources(workdir: Path) -> list[Path]:
    offenders: list[Path] = []
    for path in _iter_files(workdir):
        if path.suffix != ".swift":
            continue
        rel = path.relative_to(workdir)
        parts = tuple(part.lower() for part in rel.parts[:-1])
        if "tests" in parts or "test" in parts:
            continue
        if "src" not in parts and "sources" not in parts:
            continue
        try:
            text = path.read_text(errors="ignore")
        except OSError:
            text = ""
        name = path.name.lower()
        if "import xctest" in text.lower() or "xctestcase" in text.lower() or name.endswith("tests.swift"):
            offenders.append(path)
    return offenders


def _setting_value(settings: Any, key: str) -> Any:
    if not isinstance(settings, dict):
        return None
    if key in settings:
        return settings[key]
    for value in settings.values():
        found = _setting_value(value, key)
        if found is not None:
            return found
    return None


def _truthy_build_setting(value: Any) -> bool:
    return str(value).strip().lower() in {"yes", "true", "1"}


def _target_has_infoplist_config(target: dict[str, Any]) -> bool:
    info = target.get("info")
    if isinstance(info, dict) and info.get("path"):
        return True

    settings = target.get("settings")
    if _truthy_build_setting(_setting_value(settings, "GENERATE_INFOPLIST_FILE")):
        return True
    return bool(_setting_value(settings, "INFOPLIST_FILE"))


def _xcodegen_targets_missing_infoplist_config(workdir: Path) -> list[str]:
    project = workdir / "project.yml"
    if not project.is_file():
        return []
    try:
        data = yaml.safe_load(project.read_text()) or {}
    except yaml.YAMLError:
        return []
    targets = data.get("targets")
    if not isinstance(targets, dict):
        return []

    missing: list[str] = []
    for name, target in targets.items():
        if not isinstance(target, dict):
            continue
        target_type = str(target.get("type", "")).lower()
        if target_type not in {"application", "bundle.unit-test"}:
            continue
        if not _target_has_infoplist_config(target):
            missing.append(str(name))
    return missing


def _react_testing_library_test_files(workdir: Path) -> list[Path]:
    test_files: list[Path] = []
    for path in _js_ts_test_files(workdir):
        try:
            text = path.read_text(errors="ignore")
        except OSError:
            continue
        if "@testing-library/react" in text:
            test_files.append(path)
    return test_files


def _package_json(workdir: Path) -> dict[str, Any]:
    package_json = workdir / "package.json"
    if not package_json.is_file():
        return {}
    try:
        data = json.loads(package_json.read_text(errors="ignore"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _has_package_dependency(workdir: Path, name: str) -> bool:
    data = _package_json(workdir)
    for section in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"):
        deps = data.get(section)
        if isinstance(deps, dict) and name in deps:
            return True
    return False


def _files_importing_jest_dom(workdir: Path) -> list[Path]:
    files: list[Path] = []
    for path in _iter_files(workdir):
        if path.suffix not in {".js", ".jsx", ".ts", ".tsx"}:
            continue
        try:
            text = path.read_text(errors="ignore")
        except OSError:
            continue
        if "@testing-library/jest-dom" in text:
            files.append(path)
    return files


def _files_importing_bare_jest_dom(workdir: Path) -> list[Path]:
    files: list[Path] = []
    bare_import = re.compile(r"""['"]@testing-library/jest-dom['"]""")
    for path in _files_importing_jest_dom(workdir):
        try:
            text = path.read_text(errors="ignore")
        except OSError:
            continue
        if bare_import.search(text):
            files.append(path)
    return files


def _typescript_files_using_css_at_import(workdir: Path) -> list[Path]:
    files: list[Path] = []
    for path in _iter_files(workdir):
        if path.suffix not in {".ts", ".tsx"}:
            continue
        try:
            text = path.read_text(errors="ignore")
        except OSError:
            continue
        if TYPESCRIPT_CSS_AT_IMPORT_RE.search(text):
            files.append(path)
    return files


def _missing_postcss_plugin_dependencies(workdir: Path) -> dict[str, list[Path]]:
    missing: dict[str, list[Path]] = {}
    for name in POSTCSS_CONFIG_NAMES:
        path = workdir / name
        if not path.is_file():
            continue
        try:
            text = path.read_text(errors="ignore")
        except OSError:
            continue
        for package_name in POSTCSS_PLUGIN_PACKAGES:
            if not re.search(rf"""(?<![\w@/-]){re.escape(package_name)}(?![\w@/-])""", text):
                continue
            if not _has_package_dependency(workdir, package_name):
                missing.setdefault(package_name, []).append(path)
    return missing


def _test_files_using_jest_dom_matchers(workdir: Path) -> list[Path]:
    files: list[Path] = []
    for path in _js_ts_test_files(workdir):
        try:
            text = path.read_text(errors="ignore").lower()
        except OSError:
            continue
        if any(matcher in text for matcher in JEST_DOM_MATCHERS):
            files.append(path)
    return files


def _vitest_config_enables_globals(workdir: Path) -> bool:
    config_names = (
        "vite.config.ts",
        "vite.config.js",
        "vite.config.mts",
        "vite.config.mjs",
        "vitest.config.ts",
        "vitest.config.js",
        "vitest.config.mts",
        "vitest.config.mjs",
    )
    for name in config_names:
        path = workdir / name
        if not path.is_file():
            continue
        text = path.read_text(errors="ignore")
        if re.search(r"\bglobals\s*:\s*true\b", text):
            return True
    return False


def _vitest_imported_names(text: str) -> set[str]:
    imported: set[str] = set()
    for match in VITEST_NAMED_IMPORT_RE.finditer(text):
        for raw_name in match.group("names").split(","):
            name = raw_name.strip()
            if not name:
                continue
            imported.add(name.split(" as ", 1)[0].strip())
    return imported


def _uses_vitest_api(text: str, api: str) -> bool:
    if api == "vi":
        return bool(re.search(r"\bvi\s*(?:\.|\()", text))
    return bool(re.search(rf"\b{re.escape(api)}\s*\(", text))


def _vitest_global_api_mismatches(workdir: Path) -> list[tuple[Path, list[str]]]:
    if _vitest_config_enables_globals(workdir):
        return []

    mismatches: list[tuple[Path, list[str]]] = []
    for path in _js_ts_test_files(workdir):
        try:
            text = path.read_text(errors="ignore")
        except OSError:
            continue
        imported = _vitest_imported_names(text)
        missing = [
            api
            for api in VITEST_GLOBAL_APIS
            if _uses_vitest_api(text, api) and api not in imported
        ]
        if missing:
            mismatches.append((path, missing))
    return mismatches


def _layout_dependent_test_files(workdir: Path) -> list[Path]:
    offenders: list[Path] = []
    for path in _js_ts_test_files(workdir):
        try:
            text = path.read_text(errors="ignore")
        except OSError:
            continue
        if "getBoundingClientRect" not in text:
            continue
        if "fireEvent.click" not in text and "userEvent.click" not in text:
            continue
        if "clientX" not in text and "clientY" not in text:
            continue
        if RECT_MOCK_RE.search(text):
            continue
        offenders.append(path)
    return offenders


def _nested_react_vite_project_paths(workdir: Path) -> list[Path]:
    nested_roots: list[Path] = []
    for candidate in ("worktree", "workspace"):
        root = workdir / candidate
        if not root.is_dir():
            continue
        if (
            (root / "src").is_dir()
            and (
                (root / "index.html").is_file()
                or (root / "package.json").is_file()
                or any(root.glob("vite.config.*"))
                or any((root / "src").glob("**/*.tsx"))
            )
        ):
            nested_roots.append(root)
    return nested_roots


def _ambiguous_testing_library_queries(workdir: Path) -> list[tuple[Path, str]]:
    offenders: list[tuple[Path, str]] = []
    for path in _js_ts_test_files(workdir):
        try:
            text = path.read_text(errors="ignore")
        except OSError:
            continue
        for match in TESTING_LIBRARY_REGEX_QUERY_RE.finditer(text):
            pattern = match.group("pattern").strip()
            if not BOARD_COORDINATE_REGEX_RE.search(pattern):
                continue
            if pattern.startswith("^") and pattern.endswith("$"):
                continue
            offenders.append((path, pattern))
    return offenders


def _low_level_user_pointerdown_tests(workdir: Path) -> list[Path]:
    offenders: list[Path] = []
    for path in _js_ts_test_files(workdir):
        try:
            text = path.read_text(errors="ignore")
        except OSError:
            continue
        if LOW_LEVEL_USER_POINTERDOWN_RE.search(text):
            offenders.append(path)
    return offenders


def _computed_style_layout_assertion_tests(workdir: Path) -> list[Path]:
    offenders: list[Path] = []
    for path in _js_ts_test_files(workdir):
        try:
            text = path.read_text(errors="ignore")
        except OSError:
            continue
        if COMPUTED_STYLE_LAYOUT_ASSERTION_RE.search(text):
            offenders.append(path)
    return offenders


def _js_ts_test_files(workdir: Path) -> list[Path]:
    test_files: list[Path] = []
    for path in _iter_files(workdir):
        if path.suffix not in {".js", ".jsx", ".ts", ".tsx"}:
            continue
        lower_name = path.name.lower()
        if ".test." in lower_name or ".spec." in lower_name:
            test_files.append(path)
    return test_files


def _has_jsdom_test_environment(workdir: Path, test_files: list[Path]) -> bool:
    config_names = (
        "vite.config.ts",
        "vite.config.js",
        "vite.config.mts",
        "vite.config.mjs",
        "vitest.config.ts",
        "vitest.config.js",
        "vitest.config.mts",
        "vitest.config.mjs",
    )
    for name in config_names:
        path = workdir / name
        if not path.is_file():
            continue
        text = path.read_text(errors="ignore").lower()
        if "environment" in text and "jsdom" in text:
            return True

    return any("@vitest-environment jsdom" in path.read_text(errors="ignore").lower() for path in test_files)


def _relative_import_resolves(importer: Path, imported: str) -> bool:
    target = (importer.parent / imported).resolve()
    if target.is_file():
        return True
    if target.suffix:
        return False
    if any(target.with_suffix(ext).is_file() for ext in TS_RESOLVABLE_EXTENSIONS):
        return True
    if target.is_dir():
        return any((target / f"index{ext}").is_file() for ext in TS_RESOLVABLE_EXTENSIONS)
    return False


def _unresolved_relative_imports(workdir: Path) -> list[tuple[Path, str]]:
    unresolved: list[tuple[Path, str]] = []
    for path in _iter_files(workdir):
        if path.suffix not in {".js", ".jsx", ".ts", ".tsx"}:
            continue
        try:
            text = path.read_text(errors="ignore")
        except OSError:
            continue
        for match in TS_IMPORT_RE.finditer(text):
            imported = match.group("path") or match.group("dynamic")
            if imported and not _relative_import_resolves(path, imported):
                unresolved.append((path, imported))
    return unresolved


def validate_delivery_profile(workdir: Path, profile: dict[str, Any] | None) -> list[dict[str, str]]:
    if not profile:
        return []

    issues: list[dict[str, str]] = []
    for pattern in profile.get("required_files") or []:
        if isinstance(pattern, str) and not _matches_required_file(workdir, pattern):
            issues.append(_delivery_issue(
                "missing-required-file",
                f"Delivery profile requires `{pattern}`, but it was not found.",
            ))

    forbidden = {str(lang).lower() for lang in profile.get("forbidden_product_languages") or []}
    counts = language_counts(workdir)
    for language in sorted(forbidden):
        if counts.get(language, 0) > 0:
            issues.append(_delivery_issue(
                "forbidden-product-language",
                (
                    f"Delivery profile forbids {language} product files, "
                    f"but {counts[language]} {language} file(s) were found."
                ),
            ))

    if _is_swift_xcodegen_profile(profile):
        for path in _swift_test_files_in_product_sources(workdir):
            rel = path.relative_to(workdir).as_posix()
            issues.append(_delivery_issue(
                "test-file-in-product-sources",
                (
                    f"Swift test file `{rel}` is inside product sources. Move it under "
                    "`tests/` or delete it from the app source directory so XcodeGen "
                    "does not compile XCTest into the application target."
                ),
            ))
        missing_infoplist = _xcodegen_targets_missing_infoplist_config(workdir)
        if missing_infoplist:
            issues.append(_delivery_issue(
                "missing-infoplist-generation",
                (
                    "XcodeGen application and unit-test targets must generate or provide an Info.plist. "
                    f"Missing Info.plist configuration for target(s): {', '.join(missing_infoplist)}. "
                    "Add `GENERATE_INFOPLIST_FILE: YES` in each target's settings or provide "
                    "`INFOPLIST_FILE`/`info.path`."
                ),
            ))

    if _is_react_vite_profile(profile):
        hygiene_severity = _react_vite_hygiene_severity(profile)
        nested_projects = _nested_react_vite_project_paths(workdir)
        if nested_projects:
            files = ", ".join(path.relative_to(workdir).as_posix() for path in nested_projects)
            issues.append(_delivery_issue(
                "nested-worktree-project",
                (
                    f"React/Vite project files were created inside nested directorie(s): {files}. "
                    "The current git worktree root is already the project root; move app files and "
                    "tests to root-level `index.html`, `package.json`, and `src/`, then delete the "
                    "nested shadow project so later tasks modify the same app."
                ),
            ))
        ambiguous_queries = _ambiguous_testing_library_queries(workdir)
        if ambiguous_queries:
            examples = []
            for path, pattern in ambiguous_queries[:3]:
                examples.append(f"{path.relative_to(workdir).as_posix()} uses /{pattern}/")
            issues.append(_delivery_issue(
                "ambiguous-testing-library-query",
                (
                    "Generated React Testing Library tests use broad regex queries for board coordinates, "
                    f"which can match multiple cells: {'; '.join(examples)}. Use exact accessible names "
                    "such as `{ name: /^行1列1, 空$/ }`, query by stable test id, or use `getAllBy...` "
                    "only when intentionally asserting multiple elements."
                ),
            ))
        pointerdown_tests = _low_level_user_pointerdown_tests(workdir)
        if pointerdown_tests:
            files = ", ".join(path.relative_to(workdir).as_posix() for path in pointerdown_tests[:3])
            issues.append(_delivery_issue(
                "low-level-pointerdown-test",
                (
                    f"{files} uses `user.pointer(... '[pointerdown]')` as a touch support test. "
                    "In jsdom, low-level pointer sequences can diverge from browser activation behavior. "
                    "Prefer `await user.click(cell)` for mouse/touch activation acceptance, or use "
                    "`fireEvent.pointerDown(cell)` only when intentionally testing an explicit pointerDown contract."
                ),
            ))
        test_files = _js_ts_test_files(workdir)
        if not test_files:
            issues.append(_delivery_issue(
                "missing-test-file",
                (
                    "React/Vite delivery must include at least one real Vitest test file matching "
                    "`*.test.ts`, `*.test.tsx`, `*.spec.ts`, or `*.spec.tsx`. Add tests for the "
                    "generated behavior instead of relying on a no-test Vitest run."
                ),
                hygiene_severity,
            ))
        react_tests = _react_testing_library_test_files(workdir)
        if react_tests and not _has_jsdom_test_environment(workdir, react_tests):
            files = ", ".join(path.relative_to(workdir).as_posix() for path in react_tests[:3])
            issues.append(_delivery_issue(
                "missing-jsdom-test-environment",
                (
                    "React Testing Library tests require a browser-like Vitest environment, "
                    f"but no jsdom test environment was found for {files}. Configure "
                    "`test: { environment: 'jsdom' }` in `vite.config.ts` or `vitest.config.ts`, "
                    "and keep `jsdom` in devDependencies."
                ),
                hygiene_severity,
            ))
        bare_jest_dom_files = _files_importing_bare_jest_dom(workdir)
        if bare_jest_dom_files:
            files = ", ".join(path.relative_to(workdir).as_posix() for path in bare_jest_dom_files[:3])
            issues.append(_delivery_issue(
                "bare-jest-dom-import",
                (
                    f"{files} imports the bare `@testing-library/jest-dom` entrypoint. "
                    "Vitest projects should import `@testing-library/jest-dom/vitest` from the setup file "
                    "so matcher types and the Vitest expect instance are wired correctly."
                ),
            ))
        css_at_import_files = _typescript_files_using_css_at_import(workdir)
        if css_at_import_files:
            files = ", ".join(path.relative_to(workdir).as_posix() for path in css_at_import_files[:3])
            issues.append(_delivery_issue(
                "invalid-typescript-at-import",
                (
                    f"{files} uses CSS-style `@import` syntax inside a TypeScript file. "
                    "Use standard ES module syntax instead, for example "
                    "`import '@testing-library/jest-dom/vitest';` in Vitest setup files."
                ),
            ))
        missing_postcss_plugins = _missing_postcss_plugin_dependencies(workdir)
        if missing_postcss_plugins:
            packages = ", ".join(sorted(missing_postcss_plugins))
            files = sorted({
                path.relative_to(workdir).as_posix()
                for paths in missing_postcss_plugins.values()
                for path in paths
            })
            issues.append(_delivery_issue(
                "missing-postcss-plugin-dependency",
                (
                    f"PostCSS config file(s) {', '.join(files)} reference plugin package(s) "
                    f"{packages}, but package.json does not declare them. Add the missing package(s) "
                    "to devDependencies, or remove the PostCSS/Tailwind config and use plain CSS."
                ),
            ))
        jest_dom_matcher_files = _test_files_using_jest_dom_matchers(workdir)
        if jest_dom_matcher_files and not _has_package_dependency(workdir, "@testing-library/jest-dom"):
            files = ", ".join(path.relative_to(workdir).as_posix() for path in jest_dom_matcher_files[:3])
            issues.append(_delivery_issue(
                "missing-jest-dom-dependency",
                (
                    f"{files} uses Testing Library jest-dom matchers such as `toHaveTextContent`, "
                    "but `@testing-library/jest-dom` is not declared in package.json. Add it to "
                    "devDependencies and import `@testing-library/jest-dom/vitest` from the Vitest setup file, "
                    "or use built-in assertions instead."
                ),
            ))
        vitest_global_mismatches = _vitest_global_api_mismatches(workdir)
        if vitest_global_mismatches:
            examples = []
            for path, missing in vitest_global_mismatches[:3]:
                examples.append(f"{path.relative_to(workdir).as_posix()} uses {', '.join(missing)}")
            issues.append(_delivery_issue(
                "vitest-global-api-mismatch",
                (
                    "Vitest test APIs are used as globals, but Vitest globals are not enabled. "
                    f"{'; '.join(examples)}. Either import the used APIs from `vitest` in each "
                    "test file or set `test: { globals: true }` in the Vitest/Vite config and "
                    "include matching Vitest types."
                ),
            ))
        layout_dependent_tests = _layout_dependent_test_files(workdir)
        if layout_dependent_tests:
            files = ", ".join(path.relative_to(workdir).as_posix() for path in layout_dependent_tests[:3])
            issues.append(_delivery_issue(
                "jsdom-layout-dependent-test",
                (
                    f"{files} derives click coordinates from `getBoundingClientRect()` without mocking it. "
                    "jsdom does not compute real layout, so this often produces zero-sized boxes and false "
                    "interaction failures. Prefer semantic click targets such as cell buttons/test ids, or mock "
                    "`getBoundingClientRect` with non-zero width/height before firing coordinate-based clicks."
                ),
            ))
        computed_style_tests = _computed_style_layout_assertion_tests(workdir)
        if computed_style_tests:
            files = ", ".join(path.relative_to(workdir).as_posix() for path in computed_style_tests[:3])
            issues.append(_delivery_issue(
                "jsdom-computed-style-layout-test",
                (
                    f"{files} asserts flex/grid/responsive layout through `window.getComputedStyle()`. "
                    "Vitest/jsdom does not reliably apply external CSS import styles for layout assertions. "
                    "Assert semantic structure, classes, ARIA labels, and stable element presence in unit tests; "
                    "leave visual centering/responsive layout checks to browser/e2e verification."
                ),
            ))
        unresolved = _unresolved_relative_imports(workdir)
        for importer, imported in unresolved[:5]:
            issues.append(_delivery_issue(
                "unresolved-relative-import",
                (
                    f"`{importer.relative_to(workdir).as_posix()}` imports `{imported}`, "
                    "but no matching relative source file exists. Create the referenced file, "
                    "update the import, or remove the orphan test/module before running Vitest."
                ),
            ))

    return issues
