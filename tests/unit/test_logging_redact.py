from code_minions.logging import redact_secrets


def test_redacts_env_var_style():
    assert redact_secrets("ANTHROPIC_API_KEY=sk-abc123def456789012345") == "ANTHROPIC_API_KEY=[REDACTED]"


def test_redacts_quoted_json():
    assert 'sk-abc' not in redact_secrets('{"api_key": "sk-abc123def456789012345"}')


def test_redacts_bearer():
    out = redact_secrets("Authorization: Bearer abcd1234efgh5678ijkl")
    assert "abcd1234efgh" not in out
    assert "[REDACTED]" in out


def test_passes_through_normal_text():
    assert redact_secrets("hello world") == "hello world"


def test_redacts_sk_prefix_standalone():
    out = redact_secrets("token: sk-abcdefg1234567890hijklm")
    assert "sk-abcdefg" not in out
