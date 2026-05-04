"""Load llm provider config from devflow.yaml and env vars."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


class LLMConfigError(Exception):
    pass


@dataclass(frozen=True)
class ProviderConfig:
    name: str
    model: str
    api_key: str
    api_base: str | None = None


@dataclass(frozen=True)
class LLMConfig:
    default: str
    providers: dict[str, ProviderConfig]


def load_llm_config(devflow_yaml_path: Path) -> LLMConfig:
    if not devflow_yaml_path.exists():
        raise LLMConfigError(f"devflow.yaml not found: {devflow_yaml_path}")
    data: dict[str, Any] = yaml.safe_load(devflow_yaml_path.read_text()) or {}
    llm = data.get("llm") or {}
    default = llm.get("default")
    providers_raw = llm.get("providers") or {}
    if not default or default not in providers_raw:
        raise LLMConfigError("devflow.yaml: llm.default must reference a configured provider")

    providers: dict[str, ProviderConfig] = {}
    for name, p in providers_raw.items():
        env_name = p.get("api_key_env")
        if not env_name:
            raise LLMConfigError(f"provider {name!r}: api_key_env is required")
        key = os.environ.get(env_name, "").strip()
        if name == default and not key:
            raise LLMConfigError(f"env var {env_name} (for provider {name!r}) is empty or unset")
        api_base = p.get("api_base")
        if api_base is not None and not isinstance(api_base, str):
            raise LLMConfigError(f"provider {name!r}: api_base must be a string")
        model = p.get("model", "")
        if not isinstance(model, str):
            raise LLMConfigError(f"provider {name!r}: model must be a string")
        providers[name] = ProviderConfig(
            name=name,
            model=model.strip(),
            api_key=key,
            api_base=api_base.strip() if api_base else None,
        )

    return LLMConfig(default=default, providers=providers)
