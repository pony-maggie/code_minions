"""Tests for LiteLLMBackend. We patch litellm.completion with MagicMock."""
from __future__ import annotations

import http.client
import json
import os
import urllib.error
from io import BytesIO
from unittest.mock import MagicMock, patch

from code_minions.llm.litellm_backend import LiteLLMBackend
from code_minions.llm.types import Message, Tool


def _fake_litellm_response(
    text: str = "",
    tool_calls: list[dict] | None = None,
    model: str = "claude-sonnet-4-6",
    finish_reason: str | None = None,
):
    """Build a MagicMock that looks like a litellm ModelResponse."""
    choice = MagicMock()
    msg = MagicMock()
    msg.content = text or None
    msg.tool_calls = []
    if tool_calls:
        for tc in tool_calls:
            tcm = MagicMock()
            tcm.id = tc["id"]
            tcm.function = MagicMock()
            tcm.function.name = tc["name"]
            tcm.function.arguments = json.dumps(tc["arguments"])
            msg.tool_calls.append(tcm)
    choice.message = msg
    choice.finish_reason = finish_reason or ("tool_calls" if tool_calls else "stop")
    resp = MagicMock()
    resp.choices = [choice]
    resp.usage.prompt_tokens = 10
    resp.usage.completion_tokens = 20
    resp.model = model
    return resp


def test_chat_text_only(monkeypatch):
    with patch("code_minions.llm.litellm_backend._completion") as mock_completion:
        mock_completion.return_value = _fake_litellm_response(text="hello world")
        be = LiteLLMBackend(provider="anthropic", default_model="claude-sonnet-4-6", api_key="sk-x")
        resp = be.chat([Message(role="user", content="hi")])
        assert resp.message.content == "hello world"
        assert resp.usage.input_tokens == 10
        # Verify we pass the fully-qualified model to litellm
        call_model = mock_completion.call_args.kwargs["model"]
        assert call_model == "anthropic/claude-sonnet-4-6"
        assert mock_completion.call_args.kwargs["api_key"] == "sk-x"


def test_chat_with_tool_use():
    with patch("code_minions.llm.litellm_backend._completion") as mock_completion:
        mock_completion.return_value = _fake_litellm_response(
            tool_calls=[{"id": "t1", "name": "read_file", "arguments": {"path": "x.txt"}}]
        )
        be = LiteLLMBackend(provider="anthropic", default_model="claude-sonnet-4-6", api_key="sk-x")
        tools = [Tool(name="read_file", description="read", input_schema={"type": "object"})]
        resp = be.chat([Message(role="user", content="read x.txt")], tools=tools)
        assert len(resp.message.tool_calls) == 1
        assert resp.message.tool_calls[0].arguments == {"path": "x.txt"}
        assert resp.stop_reason == "tool_use"
        # Verify tools were forwarded in OpenAI function-calling shape
        sent_tools = mock_completion.call_args.kwargs["tools"]
        assert sent_tools[0]["type"] == "function"
        assert sent_tools[0]["function"]["name"] == "read_file"


def test_round_trip_tool_result_sent_correctly():
    """After a tool_use + tool result, LiteLLMBackend forwards them to litellm in the expected shape."""
    from code_minions.llm.types import ToolCall
    with patch("code_minions.llm.litellm_backend._completion") as mock_completion:
        mock_completion.return_value = _fake_litellm_response(text="done")
        be = LiteLLMBackend(provider="openai", default_model="gpt-5", api_key="sk-x")
        messages = [
            Message(role="user", content="do it"),
            Message(role="assistant", tool_calls=[ToolCall(id="t1", name="f", arguments={})]),
            Message(role="tool", tool_call_id="t1", content="result"),
        ]
        be.chat(messages)
        sent = mock_completion.call_args.kwargs["messages"]
        # Last message should be a tool result
        assert sent[-1]["role"] == "tool"
        assert sent[-1]["tool_call_id"] == "t1"
        assert sent[-1]["content"] == "result"


def test_unknown_provider_still_works_if_litellm_supports_it():
    """LiteLLM supports 100+ providers; we shouldn't hardcode a whitelist."""
    with patch("code_minions.llm.litellm_backend._completion") as mock_completion:
        mock_completion.return_value = _fake_litellm_response(text="hi")
        be = LiteLLMBackend(provider="ollama", default_model="llama3", api_key="")
        be.chat([Message(role="user", content="hi")])
        assert mock_completion.call_args.kwargs["model"] == "ollama/llama3"


def test_openai_gpt5_omits_non_default_temperature():
    with patch("code_minions.llm.litellm_backend._completion") as mock_completion:
        mock_completion.return_value = _fake_litellm_response(text="hi", model="gpt-5.5")
        be = LiteLLMBackend(provider="openai", default_model="gpt-5.5", api_key="sk-x")
        be.chat([Message(role="user", content="hi")], temperature=0.1)

        kwargs = mock_completion.call_args.kwargs
        assert kwargs["model"] == "openai/gpt-5.5"
        assert "temperature" not in kwargs


def test_openai_gpt55_uses_no_reasoning_by_default():
    with patch("code_minions.llm.litellm_backend._completion") as mock_completion:
        mock_completion.return_value = _fake_litellm_response(text="hi", model="gpt-5.5")
        be = LiteLLMBackend(provider="openai", default_model="gpt-5.5", api_key="sk-x")
        be.chat([Message(role="user", content="hi")])

        kwargs = mock_completion.call_args.kwargs
        assert kwargs["reasoning_effort"] == "none"


def test_openai_legacy_gpt5_uses_low_reasoning_by_default():
    with patch("code_minions.llm.litellm_backend._completion") as mock_completion:
        mock_completion.return_value = _fake_litellm_response(text="hi", model="gpt-5")
        be = LiteLLMBackend(provider="openai", default_model="gpt-5", api_key="sk-x")
        be.chat([Message(role="user", content="hi")])

        kwargs = mock_completion.call_args.kwargs
        assert kwargs["reasoning_effort"] == "low"


def test_sdk_response_preserves_length_finish_reason():
    raw = _fake_litellm_response(text="", model="gpt-5.5", finish_reason="length")

    resp = LiteLLMBackend._from_sdk_response(raw)

    assert resp.stop_reason == "max_tokens"


def test_chat_retries_transient_ssl_failure():
    with (
        patch("code_minions.llm.litellm_backend._completion") as mock_completion,
        patch("code_minions.llm.litellm_backend.time.sleep") as mock_sleep,
    ):
        mock_completion.side_effect = [
            RuntimeError("[SSL: UNEXPECTED_EOF_WHILE_READING] EOF occurred in violation of protocol"),
            _fake_litellm_response(text="ok"),
        ]
        be = LiteLLMBackend(provider="openai", default_model="gpt-5.5", api_key="sk-x")

        resp = be.chat([Message(role="user", content="hi")])

        assert resp.message.content == "ok"
        assert mock_completion.call_count == 2
        mock_sleep.assert_called_once()


def test_chat_does_not_retry_bad_request_errors():
    with patch("code_minions.llm.litellm_backend._completion") as mock_completion:
        mock_completion.side_effect = RuntimeError("BadRequestError: unsupported value")
        be = LiteLLMBackend(provider="openai", default_model="gpt-5.5", api_key="sk-x")

        try:
            be.chat([Message(role="user", content="hi")])
        except RuntimeError as e:
            assert "BadRequestError" in str(e)
        else:
            raise AssertionError("expected RuntimeError")

        assert mock_completion.call_count == 1


def test_minimax_api_key_is_exported_for_litellm(monkeypatch):
    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    LiteLLMBackend(provider="minimax", default_model="MiniMax-M2.7", api_key="mini-x")
    assert os.environ["MINIMAX_API_KEY"] == "mini-x"


def test_minimax_uses_openai_compatible_endpoint_by_default():
    class FakeHTTPResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self):
            return json.dumps({
                "choices": [{"message": {"content": "hi"}}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 4},
                "model": "MiniMax-M2.7",
            }).encode()

    with (
        patch("code_minions.llm.litellm_backend._completion") as mock_completion,
        patch("urllib.request.urlopen", return_value=FakeHTTPResponse()) as mock_urlopen,
    ):
        be = LiteLLMBackend(provider="minimax", default_model="MiniMax-M2.7", api_key="mini-x")
        resp = be.chat([Message(role="user", content="hi")])

        assert resp.message.content == "hi"
        mock_completion.assert_not_called()
        req = mock_urlopen.call_args.args[0]
        assert req.full_url == "https://api.minimaxi.com/v1/chat/completions"
        assert req.headers["Authorization"] == "Bearer mini-x"
        assert req.headers["Content-type"] == "application/json"
        body = json.loads(req.data.decode())
        assert body == {
            "model": "MiniMax-M2.7",
            "messages": [{"role": "user", "content": "hi"}],
            "temperature": 0.2,
            "max_tokens": 4096,
        }


def test_minimax_api_base_can_be_overridden():
    class FakeHTTPResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self):
            return BytesIO(
                json.dumps({
                    "choices": [{"message": {"content": "hi"}}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 2},
                    "model": "MiniMax-M2.7",
                }).encode()
            ).read()

    with patch("urllib.request.urlopen", return_value=FakeHTTPResponse()) as mock_urlopen:
        be = LiteLLMBackend(
            provider="minimax",
            default_model="MiniMax-M2.7",
            api_key="mini-x",
            api_base="https://api.minimax.io/v1",
        )
        be.chat([Message(role="user", content="hi")])

        req = mock_urlopen.call_args.args[0]
        assert req.full_url == "https://api.minimax.io/v1/chat/completions"


def test_minimax_retries_transient_ssl_failure():
    class FakeHTTPResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self):
            return json.dumps({
                "choices": [{"message": {"content": "hi"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 2},
                "model": "MiniMax-M2.7",
            }).encode()

    with (
        patch("urllib.request.urlopen") as mock_urlopen,
        patch("code_minions.llm.litellm_backend.time.sleep") as mock_sleep,
    ):
        mock_urlopen.side_effect = [
            urllib.error.URLError(
                "[SSL: UNEXPECTED_EOF_WHILE_READING] EOF occurred in violation of protocol"
            ),
            FakeHTTPResponse(),
        ]
        be = LiteLLMBackend(provider="minimax", default_model="MiniMax-M2.7", api_key="mini-x")

        resp = be.chat([Message(role="user", content="hi")])

        assert resp.message.content == "hi"
        assert mock_urlopen.call_count == 2
        mock_sleep.assert_called_once()


def test_minimax_does_not_retry_http_auth_failure():
    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_urlopen.side_effect = urllib.error.HTTPError(
            url="https://api.minimaxi.com/v1/chat/completions",
            code=401,
            msg="Unauthorized",
            hdrs=None,
            fp=BytesIO(b'{"error":"invalid api key"}'),
        )
        be = LiteLLMBackend(provider="minimax", default_model="MiniMax-M2.7", api_key="bad")

        try:
            be.chat([Message(role="user", content="hi")])
        except RuntimeError as e:
            assert "HTTP 401" in str(e)
        else:
            raise AssertionError("expected RuntimeError")

        assert mock_urlopen.call_count == 1


def test_minimax_retries_http_overloaded_failure():
    class FakeHTTPResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self):
            return json.dumps({
                "choices": [{"message": {"content": "hi"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 2},
                "model": "MiniMax-M2.7",
            }).encode()

    with (
        patch("urllib.request.urlopen") as mock_urlopen,
        patch("code_minions.llm.litellm_backend.time.sleep") as mock_sleep,
    ):
        mock_urlopen.side_effect = [
            urllib.error.HTTPError(
                url="https://api.minimaxi.com/v1/chat/completions",
                code=529,
                msg="overloaded",
                hdrs=None,
                fp=BytesIO(
                    b'{"error":{"type":"overloaded_error","message":"busy","http_code":"529"}}'
                ),
            ),
            FakeHTTPResponse(),
        ]
        be = LiteLLMBackend(provider="minimax", default_model="MiniMax-M2.7", api_key="mini-x")

        resp = be.chat([Message(role="user", content="hi")])

        assert resp.message.content == "hi"
        assert mock_urlopen.call_count == 2
        mock_sleep.assert_called_once()


def test_minimax_retries_remote_disconnected_failure():
    class FakeHTTPResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self):
            return json.dumps({
                "choices": [{"message": {"content": "hi"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 2},
                "model": "MiniMax-M2.7",
            }).encode()

    with (
        patch("urllib.request.urlopen") as mock_urlopen,
        patch("code_minions.llm.litellm_backend.time.sleep") as mock_sleep,
    ):
        mock_urlopen.side_effect = [
            http.client.RemoteDisconnected("Remote end closed connection without response"),
            FakeHTTPResponse(),
        ]
        be = LiteLLMBackend(provider="minimax", default_model="MiniMax-M2.7", api_key="mini-x")

        resp = be.chat([Message(role="user", content="hi")])

        assert resp.message.content == "hi"
        assert mock_urlopen.call_count == 2
        mock_sleep.assert_called_once()
