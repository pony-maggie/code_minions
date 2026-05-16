from __future__ import annotations

import re
from pathlib import Path

import code_minions
from code_minions.engine.skill import load_skill


def test_runtime_code_does_not_embed_project_specific_domain_rules() -> None:
    root = Path(code_minions.__file__).resolve().parent
    forbidden = (
        "snake",
        "gomoku",
        "五子棋",
        "五连",
        "minicalc",
        "calculate/add",
        "黑方",
        "白方",
        "悔棋",
        "星位",
        "black-wins",
        "white-wins",
        "placeStone(",
        "legal_alternating_win_sequence",
        "winner_for_alternating_coordinate_moves",
        "turn-based board game",
        "opponent filler moves",
    )
    offenders: list[str] = []

    for path in root.rglob("*"):
        if not path.is_file() or path.suffix not in {".py", ".md", ".yaml", ".yml"}:
            continue
        rel = path.relative_to(root).as_posix()
        text = path.read_text(errors="ignore").lower()
        for token in forbidden:
            if token.lower() in text:
                offenders.append(f"{rel}: {token}")

    assert offenders == []


def test_runtime_code_does_not_encode_generated_app_status_string_logic() -> None:
    root = Path(code_minions.__file__).resolve().parent / "builtin" / "skills" / "implement-with-tdd"
    patterns = (
        re.compile(r"""\bstatus\s*(?:={2,3}|!={1,2})\s*['"][a-z][a-z0-9_-]*['"]"""),
        re.compile(r"""\bsetStatus\(\s*['"][a-z][a-z0-9_-]*['"]\s*\)"""),
    )
    offenders: list[str] = []

    for path in root.rglob("*"):
        if not path.is_file() or path.suffix not in {".py", ".md", ".yaml", ".yml"}:
            continue
        rel = path.relative_to(root).as_posix()
        text = path.read_text(errors="ignore")
        for pattern in patterns:
            for match in pattern.finditer(text):
                offenders.append(f"{rel}: {match.group(0)}")

    assert offenders == []


def test_implement_with_tdd_allows_multiple_self_heal_rounds() -> None:
    root = Path(code_minions.__file__).resolve().parent / "builtin" / "skills" / "implement-with-tdd"

    skill = load_skill(root)

    assert skill.meta.policies["self_heal_max_rounds"] >= 3
    assert skill.meta.policies["reviewer_max_rounds"] >= 2
