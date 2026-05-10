from __future__ import annotations

import json
import os
import re
import shlex
import tomllib
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
REACT_VITE_STYLE_IMPORT_SUFFIXES = (".css",)
TS_IMPORT_RE = re.compile(
    r"""(?:import|export)\s+(?:type\s+)?(?:[^'"]+\s+from\s+)?['"](?P<path>\.{1,2}/[^'"]+)['"]|"""
    r"""import\(\s*['"](?P<dynamic>\.{1,2}/[^'"]+)['"]\s*\)|"""
    r"""require\(\s*['"](?P<require>\.{1,2}/[^'"]+)['"]\s*\)"""
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
ABSOLUTE_STONE_CLASS_RE = re.compile(
    r"""\.stone\s*\{[^}]*\bposition\s*:\s*absolute\b""",
    re.IGNORECASE | re.MULTILINE | re.DOTALL,
)
TYPESCRIPT_CSS_AT_IMPORT_RE = re.compile(r"""(?m)^\s*@import\s+['"]""")
PYTHON_SRC_MODULE_IMPORT_RE = re.compile(r"""(?m)^\s*(?:from\s+src(?:\.|\s+import)|import\s+src\.)""")
PYTHON_FASTAPI_APP_RE = re.compile(r"""(?m)^\s*app\s*=\s*FastAPI\s*\(""")
PYTHON_FASTAPI_FORM_RE = re.compile(r"""\bForm\s*\(""")
HTML_SCRIPT_TAG_RE = re.compile(r"""<\s*script\b""", re.IGNORECASE)
JINJA_TEMPLATE_MARKER_RE = re.compile(r"""({{.*?}}|{%.*?%})""", re.DOTALL)
FASTAPI_ROUTE_DECORATOR_RE = re.compile(
    r"""@(?:app|router)\.(?P<method>get|post|put|patch|delete)\(\s*['"](?P<path>/[^'"]*)['"]""",
    re.IGNORECASE,
)
PYTHON_TEST_CLIENT_ROUTE_RE = re.compile(
    r"""\.(?P<method>get|post|put|patch|delete)\(\s*['"](?P<path>/[^'"]*)['"]""",
    re.IGNORECASE,
)
PYTHON_TEST_APP_IMPORT_RE = re.compile(
    r"""(?m)^\s*from\s+(?P<package>[A-Za-z_]\w*)\.app\s+import\s+app\b"""
)
POSTCSS_CONFIG_NAMES = (
    "postcss.config.js",
    "postcss.config.cjs",
    "postcss.config.mjs",
    "postcss.config.ts",
)
POSTCSS_PLUGIN_PACKAGES = ("tailwindcss", "autoprefixer", "postcss-preset-env")
FASTAPI_BUILTIN_GET_ROUTES = {"/docs", "/docs/oauth2-redirect", "/openapi.json", "/redoc"}


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

    if "python" in text and (
        "fastapi" in text
        or "web service" in text
        or "web api" in text
        or "http api" in text
    ):
        return apply_stack_pack_defaults({
            "stack_id": "python-web",
            "kind": "web-service",
            "language": "python",
            "framework": "fastapi" if "fastapi" in text else "python-web",
            "build_system": "python",
            "test_command": "python -m pytest -q",
            "required_files": ["pyproject.toml", "src", "tests"],
            "forbidden_product_languages": ["javascript", "typescript", "swift", "go"],
        })

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
            "pre_test_command": ["npm", "run", "build"],
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

    if stack_id == "python-web" or (
        "python" in text
        and (
            "fastapi" in text
            or "web-service" in text
            or "web service" in text
            or "web api" in text
            or "http" in text
        )
    ):
        test_command = str(profile.get("test_command") or "python -m pytest -q")
        return {
            "install_command": None,
            "test_command": shlex.split(test_command),
            "env": {"PYTHONPATH": "{workdir}{pathsep}{workdir}/src"},
            "ignored_paths": ["__pycache__", ".pytest_cache"],
        }

    if stack_id == "python-cli" or ("python" in text and ("cli" in text or "command line" in text)):
        test_command = str(profile.get("test_command") or "python -m pytest -q")
        return {
            "install_command": None,
            "test_command": shlex.split(test_command),
            "env": {"PYTHONPATH": "{workdir}{pathsep}{workdir}/src"},
            "ignored_paths": ["__pycache__", ".pytest_cache"],
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


def _is_python_web_profile(profile: dict[str, Any]) -> bool:
    if stack_id_for_delivery(profile) == "python-web":
        return True
    text = "\n".join(
        str(profile.get(key, ""))
        for key in ("kind", "language", "framework", "build_system", "test_command")
    ).lower()
    return "python" in text and (
        "fastapi" in text
        or "web-service" in text
        or "web service" in text
        or "web api" in text
        or "http api" in text
    )


def _gate_strictness(profile: dict[str, Any]) -> str:
    strictness = str(profile.get("gate_strictness") or "balanced").strip().lower()
    if strictness in {"relaxed", "balanced", "strict"}:
        return strictness
    return "balanced"


def _delivery_issue(
    code: str,
    message: str,
    severity: str = "error",
    *,
    repair_hint: str = "",
    paths: list[str] | None = None,
) -> dict[str, Any]:
    if severity not in DELIVERY_SEVERITIES:
        severity = "error"
    issue: dict[str, Any] = {"code": code, "severity": severity, "message": message}
    if repair_hint:
        issue["repair_hint"] = repair_hint
    if paths:
        issue["paths"] = paths
    return issue


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


def _pyproject_has_pytest_pythonpath_src(workdir: Path) -> bool:
    pyproject = workdir / "pyproject.toml"
    if not pyproject.is_file():
        return False
    try:
        data = tomllib.loads(pyproject.read_text(errors="ignore"))
    except tomllib.TOMLDecodeError:
        return False
    options = (
        data.get("tool", {})
        .get("pytest", {})
        .get("ini_options", {})
    )
    if not isinstance(options, dict):
        return False
    pythonpath = options.get("pythonpath")
    if isinstance(pythonpath, str):
        return pythonpath == "src"
    if isinstance(pythonpath, list):
        return any(str(path) == "src" for path in pythonpath)
    return False


def _pyproject_project_name(workdir: Path) -> str:
    pyproject = workdir / "pyproject.toml"
    if not pyproject.is_file():
        return ""
    try:
        data = tomllib.loads(pyproject.read_text(errors="ignore"))
    except tomllib.TOMLDecodeError:
        return ""
    project = data.get("project", {})
    if not isinstance(project, dict):
        return ""
    return str(project.get("name") or "").strip()


def _pyproject_project_dependencies(workdir: Path) -> list[str]:
    pyproject = workdir / "pyproject.toml"
    if not pyproject.is_file():
        return []
    try:
        data = tomllib.loads(pyproject.read_text(errors="ignore"))
    except tomllib.TOMLDecodeError:
        return []
    project = data.get("project", {})
    if not isinstance(project, dict):
        return []
    dependencies = project.get("dependencies")
    if not isinstance(dependencies, list):
        return []
    return [str(dependency) for dependency in dependencies]


def _dependency_name(dependency: str) -> str:
    match = re.match(r"\s*([A-Za-z0-9_.-]+)", dependency)
    if not match:
        return ""
    return match.group(1).replace("_", "-").lower()


def _python_web_declares_form_dependency(workdir: Path) -> bool:
    dependencies = _pyproject_project_dependencies(workdir)
    names = {_dependency_name(dependency) for dependency in dependencies}
    if "python-multipart" in names:
        return True
    normalized = [dependency.replace("_", "-").lower() for dependency in dependencies]
    return any(dependency.startswith("fastapi[standard]") or dependency.startswith("fastapi[all]") for dependency in normalized)


def _python_web_canonical_package(workdir: Path) -> str:
    project_name = _pyproject_project_name(workdir)
    if not project_name:
        return ""
    package_name = re.sub(r"\W+", "_", project_name).strip("_").lower()
    if not package_name:
        return ""
    if (workdir / "src" / package_name / "app.py").is_file():
        return package_name
    return ""


def _python_web_form_usage_files(workdir: Path) -> list[Path]:
    src_dir = workdir / "src"
    if not src_dir.is_dir():
        return []
    paths: list[Path] = []
    for path in src_dir.rglob("*.py"):
        if not path.is_file():
            continue
        try:
            text = path.read_text(errors="ignore")
        except OSError:
            continue
        if PYTHON_FASTAPI_FORM_RE.search(text):
            paths.append(path)
    return paths


def _python_web_inline_script_files(workdir: Path) -> list[Path]:
    src_dir = workdir / "src"
    if not src_dir.is_dir():
        return []
    paths: list[Path] = []
    for path in src_dir.rglob("*.py"):
        if not path.is_file():
            continue
        try:
            text = path.read_text(errors="ignore")
        except OSError:
            continue
        if HTML_SCRIPT_TAG_RE.search(text):
            paths.append(path)
    return paths


def _python_web_unrendered_template_marker_files(workdir: Path) -> list[Path]:
    src_dir = workdir / "src"
    if not src_dir.is_dir():
        return []
    paths: list[Path] = []
    for path in src_dir.rglob("*.py"):
        if not path.is_file():
            continue
        try:
            text = path.read_text(errors="ignore")
        except OSError:
            continue
        if (
            JINJA_TEMPLATE_MARKER_RE.search(text)
            and "Jinja2Templates" not in text
            and "TemplateResponse" not in text
        ):
            paths.append(path)
    return paths


def _python_web_src_module_import_tests(workdir: Path) -> list[Path]:
    tests_dir = workdir / "tests"
    if not tests_dir.is_dir():
        return []
    offenders: list[Path] = []
    for path in tests_dir.rglob("*.py"):
        if not path.is_file():
            continue
        try:
            text = path.read_text(errors="ignore")
        except OSError:
            continue
        if PYTHON_SRC_MODULE_IMPORT_RE.search(text):
            offenders.append(path)
    return offenders


def _python_web_fastapi_app_modules(workdir: Path) -> list[Path]:
    src_dir = workdir / "src"
    if not src_dir.is_dir():
        return []
    modules: list[Path] = []
    for path in src_dir.glob("*/app.py"):
        if not path.is_file():
            continue
        try:
            text = path.read_text(errors="ignore")
        except OSError:
            continue
        if "FastAPI" in text and PYTHON_FASTAPI_APP_RE.search(text):
            modules.append(path)
    return modules


def _python_web_route_decorators(workdir: Path) -> set[tuple[str, str]]:
    src_dir = workdir / "src"
    if not src_dir.is_dir():
        return set()
    routes: set[tuple[str, str]] = set()
    for path in src_dir.rglob("*.py"):
        if not path.is_file():
            continue
        try:
            text = path.read_text(errors="ignore")
        except OSError:
            continue
        for match in FASTAPI_ROUTE_DECORATOR_RE.finditer(text):
            routes.add((match.group("method").lower(), match.group("path")))
    return routes


def _python_web_missing_tested_routes(workdir: Path) -> list[tuple[Path, str, str]]:
    tests_dir = workdir / "tests"
    if not tests_dir.is_dir():
        return []
    declared_routes = _python_web_route_decorators(workdir)
    if not declared_routes:
        return []

    missing: list[tuple[Path, str, str]] = []
    seen: set[tuple[Path, str, str]] = set()
    for path in tests_dir.rglob("*.py"):
        if not path.is_file():
            continue
        try:
            text = path.read_text(errors="ignore")
        except OSError:
            continue
        for match in PYTHON_TEST_CLIENT_ROUTE_RE.finditer(text):
            method = match.group("method").lower()
            route_path = match.group("path")
            key = (path, method, route_path)
            if method == "get" and route_path in FASTAPI_BUILTIN_GET_ROUTES:
                continue
            if (method, route_path) not in declared_routes and key not in seen:
                missing.append(key)
                seen.add(key)
    return missing


def _python_web_test_app_imports(workdir: Path) -> dict[str, list[Path]]:
    tests_dir = workdir / "tests"
    if not tests_dir.is_dir():
        return {}
    imports: dict[str, list[Path]] = {}
    for path in tests_dir.rglob("*.py"):
        if not path.is_file():
            continue
        try:
            text = path.read_text(errors="ignore")
        except OSError:
            continue
        for match in PYTHON_TEST_APP_IMPORT_RE.finditer(text):
            imports.setdefault(match.group("package"), []).append(path)
    return imports


def _python_web_imported_app_modules_missing_app(workdir: Path) -> list[Path]:
    missing: list[Path] = []
    for package_name in _python_web_test_app_imports(workdir):
        module = workdir / "src" / package_name / "app.py"
        if not module.is_file():
            missing.append(module)
            continue
        try:
            text = module.read_text(errors="ignore")
        except OSError:
            missing.append(module)
            continue
        if "FastAPI" not in text or not PYTHON_FASTAPI_APP_RE.search(text):
            missing.append(module)
    return missing


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


def _has_vitest_globals_types(workdir: Path) -> bool:
    for path in _iter_files(workdir):
        if path.suffix not in {".json", ".ts", ".tsx", ".d.ts"}:
            continue
        if "vitest/globals" in path.read_text(errors="ignore"):
            return True
    return False


def _vitest_global_type_gaps(workdir: Path) -> list[tuple[Path, list[str]]]:
    if not _vitest_config_enables_globals(workdir) or _has_vitest_globals_types(workdir):
        return []

    gaps: list[tuple[Path, list[str]]] = []
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
            gaps.append((path, missing))
    return gaps


def _missing_tsconfig_references(workdir: Path) -> list[tuple[Path, str, Path]]:
    missing: list[tuple[Path, str, Path]] = []
    for path in _iter_files(workdir):
        if not path.name.startswith("tsconfig") or path.suffix != ".json":
            continue
        try:
            data = json.loads(path.read_text(errors="ignore"))
        except json.JSONDecodeError:
            continue
        references = data.get("references")
        if not isinstance(references, list):
            continue
        for reference in references:
            if not isinstance(reference, dict) or not isinstance(reference.get("path"), str):
                continue
            raw_reference = reference["path"]
            target = (path.parent / raw_reference).resolve()
            if target.is_file() or (target.is_dir() and (target / "tsconfig.json").is_file()):
                continue
            expected = target if target.suffix else target / "tsconfig.json"
            missing.append((path, raw_reference, expected))
    return missing


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


def _duplicate_types_module_paths(workdir: Path) -> list[Path]:
    module_files = [workdir / "src" / "types.ts", workdir / "src" / "types.tsx"]
    index_files = [
        workdir / "src" / "types" / "index.ts",
        workdir / "src" / "types" / "index.tsx",
    ]
    existing_modules = [path for path in module_files if path.is_file()]
    existing_indexes = [path for path in index_files if path.is_file()]
    if not existing_modules or not existing_indexes:
        return []
    return [existing_modules[0], existing_indexes[0]]


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


def _react_vite_absolute_stone_cell_files(workdir: Path) -> list[Path]:
    has_absolute_stone_css = False
    for path in _iter_files(workdir):
        if path.suffix != ".css":
            continue
        try:
            text = path.read_text(errors="ignore")
        except OSError:
            continue
        if ABSOLUTE_STONE_CLASS_RE.search(text):
            has_absolute_stone_css = True
            break
    if not has_absolute_stone_css:
        return []

    offenders: list[Path] = []
    for path in _iter_files(workdir):
        if path.suffix not in {".jsx", ".tsx"}:
            continue
        try:
            text = path.read_text(errors="ignore")
        except OSError:
            continue
        stone_class_vars = {
            match.group("name")
            for match in re.finditer(
                r"""\b(?P<name>[A-Za-z_$][\w$]*)\s*=\s*[^;\n]*`stone\b""",
                text,
            )
        }
        bad_button_class = False
        for button_match in re.finditer(r"""<button\b(?P<tag>[\s\S]*?)>""", text):
            tag = button_match.group("tag")
            class_match = re.search(
                r"""className\s*=\s*(?:"(?P<double>[^"]*)"|'(?P<single>[^']*)'|`(?P<template>[^`]*)`|\{(?P<braced>[\s\S]*?)\})""",
                tag,
            )
            if not class_match:
                continue
            class_expr = next((value for value in class_match.groups() if value is not None), "")
            if "cell" not in class_expr:
                continue
            if re.search(r"""\bstone\b""", class_expr) or any(name in class_expr for name in stone_class_vars):
                bad_button_class = True
                break
        if bad_button_class:
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
    if target.suffix in TS_RESOLVABLE_EXTENSIONS:
        return False
    if any((target.parent / f"{target.name}{ext}").is_file() for ext in TS_RESOLVABLE_EXTENSIONS):
        return True
    if target.is_dir():
        return any((target / f"index{ext}").is_file() for ext in TS_RESOLVABLE_EXTENSIONS)
    return False


def _import_module_stem(module_name: str) -> str:
    for extension in TS_RESOLVABLE_EXTENSIONS:
        if module_name.endswith(extension):
            return module_name[: -len(extension)]
    return module_name


def _candidate_relative_import_targets(workdir: Path, importer: Path, imported: str) -> list[Path]:
    module_name = Path(imported).name
    if not module_name:
        return []

    module_stem = _import_module_stem(module_name)
    candidates: list[Path] = []
    for path in _iter_files(workdir):
        if path == importer or path.suffix not in TS_RESOLVABLE_EXTENSIONS:
            continue
        if path.stem == module_stem or (path.stem == "index" and path.parent.name == module_stem):
            candidates.append(path)

    def sort_key(path: Path) -> tuple[int, int, str]:
        try:
            common = len(set(importer.relative_to(workdir).parts) & set(path.relative_to(workdir).parts))
        except ValueError:
            common = 0
        return (-common, len(path.parts), path.as_posix())

    return sorted(candidates, key=sort_key)


def _relative_import_specifier(importer: Path, target: Path) -> str:
    module_target = target.parent if target.stem == "index" else target.with_suffix("")
    rel = os.path.relpath(module_target, start=importer.parent)
    specifier = Path(rel).as_posix()
    if not specifier.startswith("."):
        specifier = f"./{specifier}"
    return specifier


def _relative_import_repair(
    workdir: Path,
    importer: Path,
    imported: str,
) -> tuple[str, list[str]]:
    importer_rel = importer.relative_to(workdir).as_posix()
    candidates = _candidate_relative_import_targets(workdir, importer, imported)
    if not candidates:
        return "", [importer_rel]

    target = candidates[0]
    target_rel = target.relative_to(workdir).as_posix()
    specifier = _relative_import_specifier(importer, target)
    return (
        f"Existing likely target `{target_rel}` is available. From `{importer_rel}`, "
        f"import `{specifier}` instead of `{imported}`, or move/create the module at the referenced path.",
        [importer_rel, target_rel],
    )


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
            imported = match.group("path") or match.group("dynamic") or match.group("require")
            if imported and not _relative_import_resolves(path, imported):
                unresolved.append((path, imported))
    return unresolved


def repair_unique_unresolved_relative_imports(workdir: Path) -> list[str]:
    """Repair low-risk unresolved TS/JS relative imports."""
    changed: list[str] = []
    workdir_resolved = workdir.resolve()
    for path in _iter_files(workdir):
        if path.suffix not in {".js", ".jsx", ".ts", ".tsx"}:
            continue
        try:
            text = path.read_text(errors="ignore")
        except OSError:
            continue

        replacements: list[tuple[int, int, str]] = []
        for match in TS_IMPORT_RE.finditer(text):
            if match.group("path"):
                group = "path"
            elif match.group("dynamic"):
                group = "dynamic"
            else:
                group = "require"
            imported = match.group(group)
            if not imported or _relative_import_resolves(path, imported):
                continue
            explicit_target = (path.parent / imported).resolve()
            if any(imported.endswith(suffix) for suffix in REACT_VITE_STYLE_IMPORT_SUFFIXES):
                try:
                    explicit_target.relative_to(workdir_resolved)
                except ValueError:
                    continue
                explicit_target.parent.mkdir(parents=True, exist_ok=True)
                explicit_target.write_text("/* Generated to satisfy an explicit style import. */\n")
                changed.append(explicit_target.relative_to(workdir).as_posix())
                continue
            candidates = _candidate_relative_import_targets(workdir, path, imported)
            if len(candidates) != 1:
                continue
            replacements.append((
                match.start(group),
                match.end(group),
                _relative_import_specifier(path, candidates[0]),
            ))
        if not replacements:
            continue

        updated = text
        for start, end, specifier in reversed(replacements):
            updated = updated[:start] + specifier + updated[end:]
        if updated == text:
            continue
        path.write_text(updated)
        changed.append(path.relative_to(workdir).as_posix())
    return changed


def validate_delivery_profile(workdir: Path, profile: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not profile:
        return []

    issues: list[dict[str, Any]] = []
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

    if _is_python_web_profile(profile):
        if (
            (workdir / "src").is_dir()
            and (workdir / "pyproject.toml").is_file()
            and not _pyproject_has_pytest_pythonpath_src(workdir)
        ):
            issues.append(_delivery_issue(
                "python-web-missing-pytest-pythonpath",
                (
                    "Python web projects using a `src/` layout must configure pytest to import "
                    "from `src`. Add `pythonpath = ['src']` under `[tool.pytest.ini_options]` "
                    "in `pyproject.toml` so `python -m pytest -q` works without relying on "
                    "workflow-injected PYTHONPATH."
                ),
                repair_hint=(
                    "Update `pyproject.toml` with `[tool.pytest.ini_options]`, "
                    "`pythonpath = ['src']`, and `testpaths = ['tests']`."
                ),
                paths=["pyproject.toml"],
            ))
        src_module_import_tests = _python_web_src_module_import_tests(workdir)
        if src_module_import_tests:
            paths = [path.relative_to(workdir).as_posix() for path in src_module_import_tests]
            issues.append(_delivery_issue(
                "python-web-src-module-import",
                (
                    "Python web tests import application code through the synthetic `src` module. "
                    "For src-layout packages, tests should import the FastAPI app from the package, "
                    "for example `from minicalc_api.app import app`, not `from src.main import app`."
                ),
                repair_hint=(
                    "Move the ASGI app into `src/<package>/app.py`, update tests to import "
                    "from `<package>.app`, and keep `pyproject.toml` pytest `pythonpath = ['src']`."
                ),
                paths=paths,
            ))
        form_usage_files = _python_web_form_usage_files(workdir)
        if form_usage_files and not _python_web_declares_form_dependency(workdir):
            paths = [path.relative_to(workdir).as_posix() for path in form_usage_files]
            issues.append(_delivery_issue(
                "python-web-missing-python-multipart",
                (
                    "FastAPI form routes use `Form(...)`, but `pyproject.toml` does not declare "
                    "`python-multipart` as a runtime dependency. Fresh installs can fail while "
                    "importing the ASGI app."
                ),
                repair_hint=(
                    "Add `python-multipart>=0.0.9` to `[project].dependencies`, or avoid "
                    "`Form(...)` and parse form bodies without FastAPI's multipart dependency."
                ),
                paths=paths + ["pyproject.toml"],
            ))
        if "javascript" in forbidden:
            inline_script_files = _python_web_inline_script_files(workdir)
            if inline_script_files:
                paths = [path.relative_to(workdir).as_posix() for path in inline_script_files]
                issues.append(_delivery_issue(
                    "python-web-forbidden-inline-javascript",
                    (
                        "Delivery profile forbids JavaScript product code, but Python source "
                        "contains inline `<script>` HTML. Use server-rendered forms and normal "
                        "HTTP form posts instead of a JavaScript interaction layer."
                    ),
                    repair_hint=(
                        "Remove `<script>` blocks from rendered HTML, set form `method='post'` "
                        "and `action='/calculate'`, and return the updated full HTML page from "
                        "the FastAPI form handler."
                    ),
                    paths=paths,
                ))
        unrendered_template_marker_files = _python_web_unrendered_template_marker_files(workdir)
        if unrendered_template_marker_files:
            paths = [path.relative_to(workdir).as_posix() for path in unrendered_template_marker_files]
            issues.append(_delivery_issue(
                "python-web-unrendered-template-markers",
                (
                    "Python source contains Jinja-style template markers (`{{ ... }}` or `{% ... %}`) "
                    "but does not use FastAPI/Jinja template rendering. These markers can leak into "
                    "the browser as raw page text."
                ),
                repair_hint=(
                    "Either render HTML with `Jinja2Templates`/`TemplateResponse`, or remove Jinja "
                    "markers and build the server-rendered HTML string directly before returning it."
                ),
                paths=paths,
            ))
        fastapi_app_modules = _python_web_fastapi_app_modules(workdir)
        if len(fastapi_app_modules) > 1:
            paths = [path.relative_to(workdir).as_posix() for path in fastapi_app_modules]
            issues.append(_delivery_issue(
                "python-web-multiple-app-modules",
                (
                    "Python web projects must keep one canonical FastAPI app module across tasks. "
                    f"Multiple modules export `app = FastAPI(...)`: {', '.join(paths)}. "
                    "Merge routes and shared state into one package app module so health, calculation, "
                    "and history tests exercise the same ASGI application."
                ),
                repair_hint=(
                    "Choose the package named by the PRD or existing tests, move all routes into its "
                    "`app.py`, update tests to import that package, and delete the shadow app module."
                ),
                paths=paths,
            ))
        missing_tested_routes = _python_web_missing_tested_routes(workdir)
        if missing_tested_routes:
            examples = []
            paths = set()
            for path, method, route_path in missing_tested_routes[:5]:
                rel = path.relative_to(workdir).as_posix()
                paths.add(rel)
                examples.append(f"{rel} calls {method.upper()} {route_path}")
            issues.append(_delivery_issue(
                "python-web-tested-route-missing",
                (
                    "Python web tests call routes that are not declared by any FastAPI `@app` or "
                    f"`@router` decorator: {'; '.join(examples)}. Preserve existing endpoint paths "
                    "when adding later features; do not rename a route just to satisfy a new test."
                ),
                repair_hint=(
                    "Add or restore matching FastAPI route decorators in the canonical app module, "
                    "or update all tests and PRD-derived expectations consistently when a route rename "
                    "is intentional."
                ),
                paths=sorted(paths),
            ))
        test_app_imports = _python_web_test_app_imports(workdir)
        canonical_package = _python_web_canonical_package(workdir)
        if canonical_package and test_app_imports:
            noncanonical_paths = sorted({
                path.relative_to(workdir).as_posix()
                for package_name, import_paths in test_app_imports.items()
                if package_name != canonical_package
                for path in import_paths
            })
            if noncanonical_paths:
                issues.append(_delivery_issue(
                    "python-web-noncanonical-test-app-import",
                    (
                        f"`pyproject.toml` names canonical package `{canonical_package}`, but Python web "
                        "tests import the FastAPI app from another package. Keep all endpoint tests on "
                        f"`from {canonical_package}.app import app` so every feature exercises the same service."
                    ),
                    repair_hint=(
                        f"Update tests to import `app` from `{canonical_package}.app`, move any routes "
                        f"from shadow packages into `src/{canonical_package}/app.py`, and remove the shadow app."
                    ),
                    paths=noncanonical_paths,
                ))
        if len(test_app_imports) > 1:
            examples = []
            paths = set()
            for package_name, import_paths in sorted(test_app_imports.items()):
                rels = [path.relative_to(workdir).as_posix() for path in import_paths[:3]]
                paths.update(rels)
                examples.append(f"{package_name}.app from {', '.join(rels)}")
            issues.append(_delivery_issue(
                "python-web-multiple-test-app-imports",
                (
                    "Python web tests import multiple FastAPI app packages: "
                    f"{'; '.join(examples)}. All endpoint tests should exercise the same canonical "
                    "ASGI app so cross-feature behavior is tested on one service."
                ),
                repair_hint=(
                    "Pick the package named by the PRD or first scaffold, update all tests to import "
                    "`app` from that package, and remove shadow app packages."
                ),
                paths=sorted(paths),
            ))
        missing_imported_apps = _python_web_imported_app_modules_missing_app(workdir)
        if missing_imported_apps:
            paths = [path.relative_to(workdir).as_posix() for path in missing_imported_apps]
            issues.append(_delivery_issue(
                "python-web-missing-imported-app",
                (
                    "Python web tests import `app` from module(s) that do not export "
                    f"`app = FastAPI(...)`: {', '.join(paths)}. Do not empty or replace the canonical "
                    "app module while adding later endpoints."
                ),
                repair_hint=(
                    "Restore the canonical `src/<package>/app.py` FastAPI `app` object and add new "
                    "routes to it instead of moving the service to another package."
                ),
                paths=paths,
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
        duplicate_types = _duplicate_types_module_paths(workdir)
        if duplicate_types:
            paths = [path.relative_to(workdir).as_posix() for path in duplicate_types]
            issues.append(_delivery_issue(
                "duplicate-types-module",
                (
                    f"React/Vite project has duplicate shared type module entries: {', '.join(paths)}. "
                    "Keep a single canonical shared type module, usually `src/types.ts`; extend that file "
                    "or update all imports consistently instead of adding `src/types/index.ts` that shadows "
                    "`../types` resolution."
                ),
                repair_hint=(
                    "Merge the duplicate type declarations into one module and update imports so every "
                    "caller references the same exported symbols."
                ),
                paths=paths,
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
        vitest_global_type_gaps = _vitest_global_type_gaps(workdir)
        if vitest_global_type_gaps:
            examples = []
            for path, missing in vitest_global_type_gaps[:3]:
                examples.append(f"{path.relative_to(workdir).as_posix()} uses {', '.join(missing)}")
            issues.append(_delivery_issue(
                "vitest-global-types-missing",
                (
                    "Vitest globals are enabled at runtime, but TypeScript global types are not configured. "
                    f"{'; '.join(examples)}. Either import the used APIs from `vitest` in each test file, "
                    "or add `vitest/globals` to `compilerOptions.types` in tsconfig.json."
                ),
            ))
        missing_tsconfig_references = _missing_tsconfig_references(workdir)
        for config_path, reference, expected in missing_tsconfig_references[:5]:
            config_rel = config_path.relative_to(workdir).as_posix()
            expected_rel = expected.relative_to(workdir).as_posix()
            issues.append(_delivery_issue(
                "missing-tsconfig-reference",
                (
                    f"`{config_rel}` references `{reference}`, but `{expected_rel}` does not exist. "
                    "Create the referenced tsconfig file, update the project reference, or remove the "
                    "stale reference before running TypeScript."
                ),
                repair_hint=(
                    f"Add `{expected_rel}` with the intended TypeScript config, or remove the "
                    f"`{reference}` entry from `{config_rel}` if it is unused."
                ),
                paths=[config_rel, expected_rel],
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
        stone_cell_files = _react_vite_absolute_stone_cell_files(workdir)
        if stone_cell_files:
            files = ", ".join(path.relative_to(workdir).as_posix() for path in stone_cell_files[:3])
            issues.append(_delivery_issue(
                "react-vite-stone-class-on-cell",
                (
                    f"{files} applies a `stone` state class to clickable board cells while CSS defines "
                    "`.stone { position: absolute; ... }`. That makes the cell button itself absolute "
                    "positioned in the browser, so a placed stone can escape the grid and cover the page. "
                    "Keep the cell in normal grid flow and render the stone as a child element, or scope "
                    "absolute positioning to `.cell .stone`."
                ),
                repair_hint=(
                    "Do not put `stone` on the same button as `cell`. Use a nested `<span className=\"stone ...\">` "
                    "inside the cell, or change CSS to target `.cell .stone` only."
                ),
                paths=[path.relative_to(workdir).as_posix() for path in stone_cell_files],
            ))
        unresolved = _unresolved_relative_imports(workdir)
        for importer, imported in unresolved[:5]:
            repair_hint, paths = _relative_import_repair(workdir, importer, imported)
            issues.append(_delivery_issue(
                "unresolved-relative-import",
                (
                    f"`{importer.relative_to(workdir).as_posix()}` imports `{imported}`, "
                    "but no matching relative source file exists. Create the referenced file, "
                    "update the import, or remove the orphan test/module before running Vitest."
                ),
                repair_hint=repair_hint,
                paths=paths,
            ))

    return issues
