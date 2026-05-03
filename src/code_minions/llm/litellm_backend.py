"""LiteLLM-backed adapter. Thin translation layer between our internal
Message/Tool/Response types and litellm's OpenAI-style unified API.
LiteLLM handles provider routing, retries, and rate limits internally.
"""
from __future__ import annotations

import http.client
import json
import os
import time
import urllib.error
import urllib.request
from typing import Any

from code_minions.llm.types import (
    Message,
    Response,
    Tool,
    ToolCall,
    Usage,
)

MINIMAX_DEFAULT_API_BASE = "https://api.minimaxi.com/v1"
DEFAULT_LLM_REQUEST_TIMEOUT_SECONDS = 180


def _request_timeout_seconds() -> int:
    raw = os.environ.get("CODE_MINIONS_LLM_TIMEOUT_SECONDS")
    if not raw:
        return DEFAULT_LLM_REQUEST_TIMEOUT_SECONDS
    try:
        timeout = int(raw)
    except ValueError:
        return DEFAULT_LLM_REQUEST_TIMEOUT_SECONDS
    return max(1, timeout)


def _completion(**kwargs: Any) -> Any:
    from litellm import completion  # type: ignore
    return completion(**kwargs)


TRANSIENT_ERROR_MARKERS = (
    "APIConnectionError",
    "InternalServerError",
    "ServiceUnavailableError",
    "Timeout",
    "Connection error",
    "UNEXPECTED_EOF_WHILE_READING",
    "EOF occurred in violation of protocol",
    "RemoteDisconnected",
    "Remote end closed connection",
    "SSL",
)

NON_RETRYABLE_ERROR_MARKERS = (
    "BadRequestError",
    "AuthenticationError",
    "PermissionDeniedError",
    "NotFoundError",
    "unsupported value",
    "invalid_request_error",
    "invalid api key",
)


def _is_transient_llm_error(exc: Exception) -> bool:
    name = type(exc).__name__
    text = f"{name}: {exc}"
    if any(marker in text for marker in NON_RETRYABLE_ERROR_MARKERS):
        return False
    return any(marker in text for marker in TRANSIENT_ERROR_MARKERS)


def _completion_with_retries(kwargs: dict[str, Any], *, max_attempts: int = 3) -> Any:
    last_exc: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return _completion(**kwargs)
        except Exception as e:
            last_exc = e
            if attempt == max_attempts or not _is_transient_llm_error(e):
                raise
            time.sleep(0.5 * attempt)
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("LLM completion failed before any attempt")


def _is_transient_http_error(exc: urllib.error.HTTPError) -> bool:
    return exc.code == 429 or exc.code == 529 or 500 <= exc.code <= 599


def _urlopen_with_retries(
    req: urllib.request.Request,
    *,
    timeout: int | None = None,
    max_attempts: int = 3,
) -> Any:
    request_timeout = timeout if timeout is not None else _request_timeout_seconds()
    last_exc: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return urllib.request.urlopen(req, timeout=request_timeout)
        except urllib.error.HTTPError as e:
            last_exc = e
            if attempt == max_attempts or not _is_transient_http_error(e):
                raise
            time.sleep(0.5 * attempt)
        except urllib.error.URLError as e:
            last_exc = e
            if attempt == max_attempts or not _is_transient_llm_error(e):
                raise
            time.sleep(0.5 * attempt)
        except http.client.RemoteDisconnected as e:
            last_exc = e
            if attempt == max_attempts or not _is_transient_llm_error(e):
                raise
            time.sleep(0.5 * attempt)
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("MiniMax request failed before any attempt")


def _openai_model_uses_default_temperature(provider: str, model: str) -> bool:
    return provider == "openai" and model.startswith("gpt-5")


def _openai_model_uses_reasoning_defaults(provider: str, model: str) -> bool:
    return provider == "openai" and model.startswith("gpt-5")


def _openai_default_reasoning_effort(model: str) -> str:
    model_name = model.split("/")[-1]
    if model_name.startswith(("gpt-5.1", "gpt-5.2", "gpt-5.4", "gpt-5.5")):
        return "none"
    return "low"


def _finish_reason(choice: Any, tool_calls: list[ToolCall]) -> str:
    if tool_calls:
        return "tool_use"
    raw_reason = getattr(choice, "finish_reason", None)
    if raw_reason == "length":
        return "max_tokens"
    return raw_reason or "end_turn"


class LiteLLMBackend:
    """Single backend covering any provider supported by litellm.

    `provider` is the LiteLLM provider prefix (e.g. "anthropic", "openai",
    "gemini", "ollama", "bedrock"). `default_model` is the provider-local
    model name; we join them as "<provider>/<model>" when calling litellm.
    """

    name = "litellm"

    def __init__(
        self,
        provider: str,
        default_model: str,
        api_key: str,
        extra_env: dict[str, str] | None = None,
        api_base: str | None = None,
    ):
        import os
        self._provider = provider
        self._default_model = default_model
        self._api_key = api_key
        self._api_base = api_base
        # LiteLLM reads keys from env vars named per-provider. We set them here
        # so the user only has to configure our single api_key.
        env_key_map = {
            "anthropic": "ANTHROPIC_API_KEY",
            "openai": "OPENAI_API_KEY",
            "gemini": "GEMINI_API_KEY",
            "deepseek": "DEEPSEEK_API_KEY",
            "minimax": "MINIMAX_API_KEY",
        }
        if provider in env_key_map and api_key:
            os.environ.setdefault(env_key_map[provider], api_key)
        for k, v in (extra_env or {}).items():
            os.environ.setdefault(k, v)

    def supports_tool_use(self) -> bool:
        return True

    def chat(
        self,
        messages: list[Message],
        tools: list[Tool] | None = None,
        model: str | None = None,
        temperature: float = 0.2,
        max_tokens: int = 4096,
    ) -> Response:
        sdk_msgs = [self._to_sdk_message(m) for m in messages]
        if self._provider == "minimax":
            return self._minimax_chat(
                sdk_msgs=sdk_msgs,
                tools=tools,
                model=model or self._default_model,
                temperature=temperature,
                max_tokens=max_tokens,
            )
        selected_model = model or self._default_model
        full_model = f"{self._provider}/{selected_model}"
        kwargs: dict[str, Any] = {
            "model": full_model,
            "messages": sdk_msgs,
            "max_tokens": max_tokens,
            "api_key": self._api_key,
            "timeout": _request_timeout_seconds(),
        }
        if not _openai_model_uses_default_temperature(self._provider, selected_model):
            kwargs["temperature"] = temperature
        if _openai_model_uses_reasoning_defaults(self._provider, selected_model):
            kwargs["reasoning_effort"] = _openai_default_reasoning_effort(selected_model)
        if self._api_base:
            kwargs["api_base"] = self._api_base
        if tools:
            kwargs["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.input_schema,
                    },
                }
                for t in tools
            ]
        raw = _completion_with_retries(kwargs)
        return self._from_sdk_response(raw)

    def _minimax_chat(
        self,
        *,
        sdk_msgs: list[dict[str, Any]],
        tools: list[Tool] | None,
        model: str,
        temperature: float,
        max_tokens: int,
    ) -> Response:
        base = (self._api_base or MINIMAX_DEFAULT_API_BASE).rstrip("/")
        payload: dict[str, Any] = {
            "model": model,
            "messages": sdk_msgs,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.input_schema,
                    },
                }
                for t in tools
            ]

        req = urllib.request.Request(
            f"{base}/chat/completions",
            data=json.dumps(payload).encode(),
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with _urlopen_with_retries(req) as response:
                raw = json.loads(response.read().decode())
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")
            raise RuntimeError(f"MiniMax request failed: HTTP {e.code}: {body}") from e
        except urllib.error.URLError as e:
            raise RuntimeError(f"MiniMax connection failed: {e.reason}") from e
        except http.client.RemoteDisconnected as e:
            raise RuntimeError(f"MiniMax connection failed: {e}") from e

        base_resp = raw.get("base_resp") or {}
        if base_resp.get("status_code", 0) not in (0, None):
            raise RuntimeError(
                "MiniMax request failed: "
                f"{base_resp.get('status_msg') or json.dumps(base_resp, ensure_ascii=False)}"
            )
        return self._from_minimax_response(raw, model)

    @staticmethod
    def _to_sdk_message(m: Message) -> dict[str, Any]:
        if m.role == "tool":
            return {"role": "tool", "tool_call_id": m.tool_call_id, "content": m.content}
        if m.role == "assistant" and m.tool_calls:
            return {
                "role": "assistant",
                "content": m.content or None,
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)},
                    }
                    for tc in m.tool_calls
                ],
            }
        return {"role": m.role, "content": m.content}

    @staticmethod
    def _from_sdk_response(raw: Any) -> Response:
        choice = raw.choices[0]
        msg = choice.message
        tool_calls: list[ToolCall] = []
        for tc in (getattr(msg, "tool_calls", None) or []):
            try:
                args = json.loads(tc.function.arguments)
            except json.JSONDecodeError:
                args = {}
            tool_calls.append(ToolCall(id=tc.id, name=tc.function.name, arguments=args))
        stop = _finish_reason(choice, tool_calls)
        return Response(
            message=Message(role="assistant", content=msg.content or "", tool_calls=tool_calls),
            usage=Usage(
                input_tokens=raw.usage.prompt_tokens,
                output_tokens=raw.usage.completion_tokens,
            ),
            model=raw.model,
            stop_reason=stop,
        )

    @staticmethod
    def _from_minimax_response(raw: dict[str, Any], fallback_model: str) -> Response:
        choice = (raw.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        tool_calls: list[ToolCall] = []
        for tc in msg.get("tool_calls") or []:
            function = tc.get("function") or {}
            try:
                args = json.loads(function.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            tool_calls.append(ToolCall(
                id=tc.get("id", ""),
                name=function.get("name", ""),
                arguments=args,
            ))
        usage = raw.get("usage") or {}
        stop = "tool_use" if tool_calls else (
            "max_tokens" if choice.get("finish_reason") == "length" else choice.get("finish_reason") or "end_turn"
        )
        return Response(
            message=Message(role="assistant", content=msg.get("content") or "", tool_calls=tool_calls),
            usage=Usage(
                input_tokens=int(usage.get("prompt_tokens") or 0),
                output_tokens=int(usage.get("completion_tokens") or 0),
            ),
            model=raw.get("model") or fallback_model,
            stop_reason=stop,
        )
