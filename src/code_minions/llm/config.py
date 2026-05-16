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
    roles: dict[str, str]


def load_llm_config(devflow_yaml_path: Path) -> LLMConfig:
    if not devflow_yaml_path.exists():
        raise LLMConfigError(f"devflow.yaml not found: {devflow_yaml_path}")
    data: dict[str, Any] = yaml.safe_load(devflow_yaml_path.read_text()) or {}
    llm = data.get("llm") or {}
    default = llm.get("default")
    providers_raw = llm.get("providers") or {}
    roles_raw = llm.get("roles") or {}
    if not default or default not in providers_raw:
        raise LLMConfigError("devflow.yaml: llm.default must reference a configured provider")
    if not isinstance(roles_raw, dict):
        raise LLMConfigError("devflow.yaml: llm.roles must be a mapping when present")

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

    roles: dict[str, str] = {}
    for role, provider_name in roles_raw.items():
        role_name = str(role).strip()
        provider_key = str(provider_name).strip()
        if provider_key not in providers:
            raise LLMConfigError(
                f"devflow.yaml: llm.roles.{role_name} references unknown provider {provider_key!r}"
            )
        if not providers[provider_key].api_key:
            raise LLMConfigError(
                f"env var for llm.roles.{role_name} provider {provider_key!r} is empty or unset"
            )
        roles[role_name] = provider_key

    return LLMConfig(default=default, providers=providers, roles=roles)
