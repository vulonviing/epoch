"""Unit tests for the multi-backend LLM selection layer.

All tests are offline (no network calls).  The adapter constructors are
monkeypatched so that no actual SDK clients are instantiated.
"""
from __future__ import annotations

import importlib
import os
import sys
import types
from typing import Any
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers — reload config with a specific env state
# ---------------------------------------------------------------------------

def _reload_config(env: dict[str, str]) -> types.ModuleType:
    """Reload epoch_switch.config under a controlled env, return the module."""
    # Patch os.environ (dotenv-loaded vars are part of os.environ at this point)
    with patch.dict(os.environ, env, clear=False):
        # Force a fresh import of config by removing it from sys.modules
        for key in list(sys.modules):
            if "epoch_switch.config" in key or key == "epoch_switch.config":
                del sys.modules[key]
        # Also remove epoch_switch so its __init__ doesn't use a stale config
        sys.modules.pop("epoch_switch", None)
        import epoch_switch.config as cfg  # noqa: PLC0415
        return cfg


# ---------------------------------------------------------------------------
# 1. Backend registry completeness
# ---------------------------------------------------------------------------

class TestBackendRegistry:
    def test_all_four_backends_present(self):
        from epoch_switch.config import BACKENDS
        assert set(BACKENDS) >= {"opus48", "sonnet46", "gpt55", "gpt54mini"}

    def test_backend_fields_non_empty(self):
        from epoch_switch.config import BACKENDS
        for name, b in BACKENDS.items():
            assert b.kind, f"{name}.kind is empty"
            assert b.model, f"{name}.model is empty"
            assert b.key_env, f"{name}.key_env is empty"
            assert b.base_url_env, f"{name}.base_url_env is empty"

    def test_anthropic_backends_have_correct_kind(self):
        from epoch_switch.config import BACKENDS
        assert BACKENDS["opus48"].kind == "foundry_anthropic"
        assert BACKENDS["sonnet46"].kind == "foundry_anthropic"

    def test_azure_openai_backends_have_correct_kind(self):
        from epoch_switch.config import BACKENDS
        assert BACKENDS["gpt55"].kind == "azure_openai"
        assert BACKENDS["gpt54mini"].kind == "azure_openai"

    def test_azure_backends_have_reasoning_effort(self):
        from epoch_switch.config import BACKENDS
        assert BACKENDS["gpt55"].reasoning_effort == "xhigh"
        assert BACKENDS["gpt54mini"].reasoning_effort is not None

    def test_azure_backends_have_api_version(self):
        from epoch_switch.config import BACKENDS
        assert BACKENDS["gpt55"].api_version is not None
        assert BACKENDS["gpt54mini"].api_version is not None

    def test_opus48_uses_azure_ad(self):
        from epoch_switch.config import BACKENDS
        assert BACKENDS["opus48"].auth_type == "azure_ad"

    def test_sonnet46_uses_azure_ad(self):
        from epoch_switch.config import BACKENDS
        assert BACKENDS["sonnet46"].auth_type == "azure_ad"

    def test_gpt_backends_use_azure_ad(self):
        from epoch_switch.config import BACKENDS
        assert BACKENDS["gpt55"].auth_type == "azure_ad"
        assert BACKENDS["gpt54mini"].auth_type == "azure_ad"


# ---------------------------------------------------------------------------
# 2. _resolve_backend_from_preset — reads correct env vars
# ---------------------------------------------------------------------------

class TestResolveBackendFromPreset:
    def test_unknown_backend_raises(self):
        from epoch_switch.config import _resolve_backend_from_preset
        with pytest.raises(ValueError, match="Unknown EPOCH_LLM_BACKEND"):
            _resolve_backend_from_preset("does_not_exist")

    def test_gpt55_reads_own_key_env(self):
        from epoch_switch.config import BACKENDS, _resolve_backend_from_preset
        preset = BACKENDS["gpt55"]
        assert preset.key_env == "EPOCH_AZURE_GPT55_API_KEY"
        with patch.dict(os.environ, {
            preset.key_env: "test-key-gpt55",
            preset.base_url_env: "https://example.cognitiveservices.azure.com/",
        }):
            ab = _resolve_backend_from_preset("gpt55")
        assert ab.api_key == "test-key-gpt55"
        assert ab.base_url == "https://example.cognitiveservices.azure.com/"
        assert ab.kind == "azure_openai"
        assert ab.model == "gpt-5.5"
        assert ab.reasoning_effort == "xhigh"

    def test_gpt54mini_reads_own_key_env(self):
        from epoch_switch.config import BACKENDS, _resolve_backend_from_preset
        preset = BACKENDS["gpt54mini"]
        assert preset.key_env == "EPOCH_AZURE_GPT54MINI_API_KEY"
        with patch.dict(os.environ, {
            preset.key_env: "test-key-gpt54mini",
            preset.base_url_env: "https://example.cognitiveservices.azure.com/",
        }):
            ab = _resolve_backend_from_preset("gpt54mini")
        assert ab.api_key == "test-key-gpt54mini"
        assert ab.kind == "azure_openai"
        assert ab.model == "gpt-5.4-mini"

    def test_gpt55_and_gpt54mini_use_different_key_envs(self):
        from epoch_switch.config import BACKENDS
        assert BACKENDS["gpt55"].key_env != BACKENDS["gpt54mini"].key_env

    def test_sonnet46_reads_foundry_sonnet_env(self):
        from epoch_switch.config import BACKENDS, _resolve_backend_from_preset
        preset = BACKENDS["sonnet46"]
        with patch.dict(os.environ, {
            preset.key_env: "test-key-sonnet",
            preset.base_url_env: "https://example.services.ai.azure.com/anthropic",
        }):
            ab = _resolve_backend_from_preset("sonnet46")
        assert ab.api_key == "test-key-sonnet"
        assert ab.kind == "foundry_anthropic"
        assert ab.model == "claude-sonnet-4-6"
        assert ab.auth_type == "azure_ad"

    def test_per_backend_reasoning_effort_override(self):
        from epoch_switch.config import BACKENDS, _resolve_backend_from_preset
        preset = BACKENDS["gpt55"]
        with patch.dict(os.environ, {
            preset.key_env: "k",
            preset.base_url_env: "https://x/",
            "EPOCH_BACKEND_GPT55_REASONING_EFFORT": "medium",
        }):
            ab = _resolve_backend_from_preset("gpt55")
        assert ab.reasoning_effort == "medium"

    def test_per_backend_api_version_override(self):
        from epoch_switch.config import BACKENDS, _resolve_backend_from_preset
        preset = BACKENDS["gpt55"]
        with patch.dict(os.environ, {
            preset.key_env: "k",
            preset.base_url_env: "https://x/",
            "EPOCH_BACKEND_GPT55_API_VERSION": "2025-01-01-preview",
        }):
            ab = _resolve_backend_from_preset("gpt55")
        assert ab.api_version == "2025-01-01-preview"

    def test_backend_name_case_insensitive(self):
        """Backend names should resolve case-insensitively."""
        from epoch_switch.config import BACKENDS, _resolve_backend_from_preset
        preset = BACKENDS["gpt54mini"]
        with patch.dict(os.environ, {
            preset.key_env: "k",
            preset.base_url_env: "https://x/",
        }):
            ab = _resolve_backend_from_preset("GPT54MINI")
        assert ab.kind == "azure_openai"


# ---------------------------------------------------------------------------
# 3. Legacy provider path still works when EPOCH_LLM_BACKEND is empty
# ---------------------------------------------------------------------------

class TestLegacyProviderPath:
    def test_empty_backend_uses_provider(self):
        """When EPOCH_LLM_BACKEND is unset, the legacy provider path runs."""
        env = {
            "EPOCH_LLM_BACKEND": "",
            "EPOCH_LLM_PROVIDER": "openai",
            "EPOCH_OPENAI_API_KEY": "legacy-key",
            "EPOCH_OPENAI_MODEL": "gpt-4o",
        }
        cfg = _reload_config(env)
        assert cfg.ACTIVE_BACKEND.name.startswith("legacy:")
        assert cfg.ACTIVE_BACKEND.kind == "openai"
        assert cfg.MODEL == "gpt-4o"
        assert cfg.API_KEY == "legacy-key"

    def test_backend_overrides_provider(self):
        """When EPOCH_LLM_BACKEND is set it overrides EPOCH_LLM_PROVIDER."""
        from epoch_switch.config import BACKENDS
        preset = BACKENDS["gpt55"]
        env = {
            "EPOCH_LLM_BACKEND": "gpt55",
            "EPOCH_LLM_PROVIDER": "openai",   # should be ignored
            "EPOCH_OPENAI_API_KEY": "should-not-appear",
            preset.key_env: "azure-key",
            preset.base_url_env: "https://x/",
        }
        cfg = _reload_config(env)
        assert cfg.ACTIVE_BACKEND.name == "gpt55"
        assert cfg.ACTIVE_BACKEND.kind == "azure_openai"
        assert cfg.API_KEY == "azure-key"


# ---------------------------------------------------------------------------
# 4. _AzureOpenAIAdapter — behaviour without live network
# ---------------------------------------------------------------------------

class TestAzureOpenAIAdapter:
    def _make_adapter(
        self,
        mock_client: Any,
        model: str = "gpt-5.5",
        reasoning_effort: str | None = "xhigh",
        api_version: str = "2024-12-01-preview",
    ):
        from epoch_switch.core.llm_client import _AzureOpenAIAdapter
        with patch("openai.AzureOpenAI", return_value=mock_client):
            adapter = _AzureOpenAIAdapter(
                api_key="test-key",
                model=model,
                azure_endpoint="https://x/",
                api_version=api_version,
                reasoning_effort=reasoning_effort,
            )
        # After init patch is gone; inject the mock directly
        adapter._client = mock_client
        return adapter

    def test_model_name_property(self):
        adapter = self._make_adapter(MagicMock(), model="gpt-5.5")
        assert adapter.model_name == "gpt-5.5"

    def test_reasoning_effort_forwarded(self):
        """reasoning_effort must appear in the create() call when set."""
        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.choices[0].finish_reason = "stop"
        mock_resp.choices[0].message.content = '{"ok": true}'
        mock_client.chat.completions.create.return_value = mock_resp

        adapter = self._make_adapter(mock_client, reasoning_effort="xhigh")
        adapter.call("sys", "user", json_mode=True, model="gpt-5.5", temperature=0.0, max_tokens=1000)

        call_kwargs = mock_client.chat.completions.create.call_args.kwargs
        assert call_kwargs.get("reasoning_effort") == "xhigh"

    def test_temperature_not_forwarded(self):
        """temperature must NEVER be sent to azure_openai reasoning models."""
        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.choices[0].finish_reason = "stop"
        mock_resp.choices[0].message.content = "hello"
        mock_client.chat.completions.create.return_value = mock_resp

        adapter = self._make_adapter(mock_client)
        adapter.call("sys", "user", json_mode=False, model="gpt-5.5", temperature=0.7, max_tokens=100)

        call_kwargs = mock_client.chat.completions.create.call_args.kwargs
        assert "temperature" not in call_kwargs

    def test_max_completion_tokens_used(self):
        """Azure OpenAI uses max_completion_tokens, not max_tokens."""
        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.choices[0].finish_reason = "stop"
        mock_resp.choices[0].message.content = "hello"
        mock_client.chat.completions.create.return_value = mock_resp

        adapter = self._make_adapter(mock_client)
        adapter.call("sys", "user", json_mode=False, model="gpt-5.5", temperature=0.0, max_tokens=512)

        call_kwargs = mock_client.chat.completions.create.call_args.kwargs
        assert call_kwargs.get("max_completion_tokens") == 512
        assert "max_tokens" not in call_kwargs

    def test_json_mode_sets_response_format(self):
        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.choices[0].finish_reason = "stop"
        mock_resp.choices[0].message.content = '{"x": 1}'
        mock_client.chat.completions.create.return_value = mock_resp

        adapter = self._make_adapter(mock_client)
        adapter.call("sys", "user", json_mode=True, model="gpt-5.5", temperature=0.0, max_tokens=100)

        call_kwargs = mock_client.chat.completions.create.call_args.kwargs
        assert call_kwargs.get("response_format") == {"type": "json_object"}

    def test_truncation_raises(self):
        from epoch_switch.core.llm_client import LLMTruncationError
        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.choices[0].finish_reason = "length"
        mock_resp.choices[0].message.content = "partial"
        mock_client.chat.completions.create.return_value = mock_resp

        adapter = self._make_adapter(mock_client)
        with pytest.raises(LLMTruncationError):
            adapter.call("sys", "user", json_mode=False, model="gpt-5.5", temperature=0.0, max_tokens=10)

    def test_empty_content_raises(self):
        from epoch_switch.core.llm_client import LLMEmptyResponseError
        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.choices[0].finish_reason = "stop"
        mock_resp.choices[0].message.content = ""
        mock_client.chat.completions.create.return_value = mock_resp

        adapter = self._make_adapter(mock_client)
        with pytest.raises(LLMEmptyResponseError):
            adapter.call("sys", "user", json_mode=False, model="gpt-5.5", temperature=0.0, max_tokens=100)

    def test_no_reasoning_effort_not_forwarded(self):
        """When reasoning_effort is None, the key must not appear in the call."""
        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.choices[0].finish_reason = "stop"
        mock_resp.choices[0].message.content = "hi"
        mock_client.chat.completions.create.return_value = mock_resp

        adapter = self._make_adapter(mock_client, reasoning_effort=None)
        adapter.call("sys", "user", json_mode=False, model="gpt-5.5", temperature=0.0, max_tokens=100)

        call_kwargs = mock_client.chat.completions.create.call_args.kwargs
        assert "reasoning_effort" not in call_kwargs


# ---------------------------------------------------------------------------
# 5. complete_json blank-fence retry (regression for UC1 D2 crash)
# ---------------------------------------------------------------------------

class TestCompleteJsonBlankFence:
    """_strip_json_fence can return an empty string from a blank ```json\\n\\n``` fence.

    Before the fix, that empty payload passed the `raw.strip()` guard (raw itself is not
    empty) but then caused `json.loads("")` → JSONDecodeError.  After the fix, the
    emptiness check runs on the stripped payload, so a blank fence triggers the same
    double-tokens retry path as a true empty response.
    """

    _NO_REASONING_META = {
        "reasoning": {"enabled": False, "mode": None, "effort": None},
        "tokens": {"reported_as": "split", "input": 0, "output": 0},
    }

    def _make_client(self, responses: list[tuple[str, int]]):
        """Build an LLMClient with a fake adapter that returns responses in sequence.

        `responses` are (content, duration_ms) pairs; a dummy call_meta (no
        reasoning, zero tokens) is appended to match the adapter's 3-tuple
        `call()` contract."""
        from epoch_switch.core.llm_client import LLMClient
        adapter = MagicMock()
        adapter.call.side_effect = [
            (content, ms, self._NO_REASONING_META) for content, ms in responses
        ]
        adapter.model_name = "test-model"
        return LLMClient(adapter)

    def test_blank_fence_retries_and_succeeds(self):
        """A blank ```json\\n\\n``` fence on the first attempt should trigger a retry,
        and a valid JSON on the second attempt should be returned successfully."""
        from epoch_switch.core.llm_client import LLMEmptyResponseError
        blank_fence = "```json\n\n```"
        valid_response = '{"ok": true}'

        client = self._make_client([
            (blank_fence, 100),   # first attempt: blank fence → triggers retry
            (valid_response, 100), # retry: valid JSON → success
        ])
        result, _ = client.complete_json("sys", "user")
        assert result == {"ok": True}
        assert client._adapter.call.call_count == 2

    def test_blank_fence_both_attempts_raises_empty_response_error(self):
        """If both the first and retry attempts return blank fences, raise
        LLMEmptyResponseError (not JSONDecodeError)."""
        from epoch_switch.core.llm_client import LLMEmptyResponseError
        blank_fence = "```json\n\n```"
        import json

        client = self._make_client([
            (blank_fence, 100),
            (blank_fence, 100),
        ])
        with pytest.raises(LLMEmptyResponseError):
            client.complete_json("sys", "user")

    def test_valid_json_no_retry(self):
        """A valid first response must be returned without a retry."""
        valid_response = '{"status": "good"}'
        client = self._make_client([
            (valid_response, 50),
        ])
        result, _ = client.complete_json("sys", "user")
        assert result == {"status": "good"}
        assert client._adapter.call.call_count == 1


# ---------------------------------------------------------------------------
# 6. reset_llm — singleton is cleared
# ---------------------------------------------------------------------------

class TestResetLlm:
    def test_reset_clears_singleton(self):
        from epoch_switch.core import llm_client
        # Force a non-empty state (per-preset client cache, not a single singleton --
        # see llm_client.get_llm()'s per-backend-name cache).
        llm_client._clients["fake"] = MagicMock()
        assert llm_client._clients
        llm_client.reset_llm()
        assert llm_client._clients == {}
