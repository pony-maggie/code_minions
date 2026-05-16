from __future__ import annotations

from pathlib import Path

import pytest

from code_minions.llm.config import LLMConfigError, load_llm_config


def _write(p: Path, s: str) -> Path:
    p.write_text(s)
    return p


def test_load_valid_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", " sk-test\n")
    f = _write(tmp_path / "devflow.yaml", """
llm:
  default: anthropic
  providers:
    anthropic:
      model: " claude-sonnet-4-6 "
      api_key_env: ANTHROPIC_API_KEY
""")
    cfg = load_llm_config(f)
    assert cfg.default == "anthropic"
    assert cfg.providers["anthropic"].model == "claude-sonnet-4-6"
    assert cfg.providers["anthropic"].api_key == "sk-test"
    assert cfg.providers["anthropic"].api_base is None


def test_load_provider_api_base(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MINIMAX_API_KEY", "mini-test")
    f = _write(tmp_path / "devflow.yaml", """
llm:
  default: minimax
  providers:
    minimax:
      model: MiniMax-M2.7
      api_key_env: MINIMAX_API_KEY
      api_base: https://api.minimaxi.com/v1
""")
    cfg = load_llm_config(f)
    assert cfg.providers["minimax"].api_base == "https://api.minimaxi.com/v1"


def test_missing_env_var_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    f = _write(tmp_path / "devflow.yaml", """
llm:
  default: anthropic
  providers:
    anthropic:
      api_key_env: ANTHROPIC_API_KEY
""")
    with pytest.raises(LLMConfigError, match="ANTHROPIC_API_KEY"):
        load_llm_config(f)


def test_missing_non_default_env_var_is_allowed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-test")
    f = _write(tmp_path / "devflow.yaml", """
llm:
  default: gemini
  providers:
    anthropic:
      model: claude-sonnet-4-6
      api_key_env: ANTHROPIC_API_KEY
    gemini:
      model: gemini-3.1-pro-preview
      api_key_env: GEMINI_API_KEY
""")
    cfg = load_llm_config(f)
    assert cfg.default == "gemini"
    assert cfg.providers["gemini"].api_key == "gemini-test"
    assert cfg.providers["anthropic"].api_key == ""


def test_load_role_provider_mapping(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "openai-test")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-test")
    f = _write(tmp_path / "devflow.yaml", """
llm:
  default: openai
  roles:
    implementer: openai
    reviewer: anthropic
  providers:
    openai:
      model: gpt-5.5
      api_key_env: OPENAI_API_KEY
    anthropic:
      model: claude-sonnet-4-6
      api_key_env: ANTHROPIC_API_KEY
""")

    cfg = load_llm_config(f)

    assert cfg.roles == {"implementer": "openai", "reviewer": "anthropic"}


def test_role_mapping_to_unknown_provider_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "openai-test")
    f = _write(tmp_path / "devflow.yaml", """
llm:
  default: openai
  roles:
    reviewer: anthropic
  providers:
    openai:
      model: gpt-5.5
      api_key_env: OPENAI_API_KEY
""")

    with pytest.raises(LLMConfigError, match="llm.roles.reviewer"):
        load_llm_config(f)


def test_default_points_to_missing_provider_fails(tmp_path: Path) -> None:
    f = _write(tmp_path / "devflow.yaml", """
llm:
  default: nope
  providers:
    anthropic:
      api_key_env: ANTHROPIC_API_KEY
""")
    with pytest.raises(LLMConfigError, match="llm.default"):
        load_llm_config(f)
