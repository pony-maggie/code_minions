from __future__ import annotations

from pathlib import Path

import code_minions
from code_minions.engine.skill import load_skill


def test_implement_with_tdd_allows_multiple_self_heal_rounds() -> None:
    root = Path(code_minions.__file__).resolve().parent / "builtin" / "skills" / "implement-with-tdd"

    skill = load_skill(root)

    assert skill.meta.policies["self_heal_max_rounds"] >= 3
