"""Provider-agnostic LLM wrapper — all agents route through here.

Supported providers (via EPOCH_LLM_BACKEND preset or EPOCH_LLM_PROVIDER):
  "openai"            — OpenAI-compatible API (plain OpenAI client)
  "foundry_anthropic" — Microsoft Azure Foundry serving Claude via Anthropic SDK
  "azure_openai"      — Microsoft Azure OpenAI (AzureOpenAI client, supports
                        reasoning_effort for gpt-5.x reasoning models)

Named backend presets (EPOCH_LLM_BACKEND in .env):
  opus48    → foundry_anthropic  · claude-opus-4-8   (azure_ad auth)
  opus5     → foundry_anthropic  · claude-opus-5     (azure_ad auth)
  sonnet46  → foundry_anthropic  · claude-sonnet-4-6 (key auth)
  gpt55     → azure_openai       · gpt-5.5            (reasoning_effort=xhigh)
  gpt56sol  → azure_openai       · gpt-5.6-sol        (reasoning_effort=xhigh)
  gpt54mini → azure_openai       · gpt-5.4-mini       (reasoning_effort=medium)

Adding a new adapter:
  1. Implement a new _*Adapter class below following the _BaseAdapter interface.
  2. Add a branch in _build_adapter() keyed on ACTIVE_BACKEND.kind.
  3. If you need a new named preset, add it to config.BACKENDS.

The public surface (complete / complete_json / model_name / get_llm / reset_llm)
is intentionally unchanged — no agent needs to know which provider is active.
"""
from __future__ import annotations

import json
import re
import time
from abc import ABC, abstractmethod
from typing import Any, TYPE_CHECKING

from epoch_switch import config

if TYPE_CHECKING:
    from epoch_switch.core.llm_provenance import ProvenanceCollector


# ── Exceptions ────────────────────────────────────────────────────────────────

class LLMTruncationError(RuntimeError):
    """Raised when the model's output was cut off because it hit max_tokens.

    Catch this to increase the token budget or split the request rather than
    silently getting back malformed JSON.
    """


class LLMEmptyResponseError(RuntimeError):
    """Raised when the model returns no usable text content.

    Includes stop_reason and content block types (Anthropic) or finish_reason
    (OpenAI) so the caller can diagnose the root cause without guessing.

    Common causes on Anthropic/Opus 4.8:
      - Adaptive thinking consumed the entire output token budget → raise
        the per-agent EPOCH_<AGENT>_MAX_TOKENS override.
      - Model returned only thinking blocks, no text block → prompt too vague
        or token budget too small for both thinking and text output.
      - Refusal / safety filter → inspect the prompt / scope.
    """


# ── JSON fence strip + prose extraction ──────────────────────────────────────
_FENCE_RE = re.compile(r"^```(?:json)?\s*\n(.*?)\n```\s*$", re.DOTALL)


def _strip_json_fence(text: str) -> str:
    """Remove a leading/trailing ```json … ``` fence if present."""
    m = _FENCE_RE.match(text.strip())
    return m.group(1) if m else text


def _extract_json_blob(text: str) -> str:
    """Extract the first complete JSON object from text that may contain prose.

    Handles three cases in order:
      1. Plain JSON (starts with '{' after stripping) — returned as-is.
      2. Fenced JSON (```json ... ```) — fence is stripped, then case 1/3 applies.
      3. Prose-wrapped JSON — scans for the first '{' and walks matching braces
         to extract the outermost object.

    Returns the extracted JSON string, or the stripped text unchanged when no
    '{' is found (so the caller's json.loads raises a clear JSONDecodeError).
    """
    stripped = _strip_json_fence(text).strip()
    if stripped.startswith("{"):
        return stripped
    start = stripped.find("{")
    if start == -1:
        return stripped  # no object found; json.loads will fail informatively
    depth = 0
    for i, ch in enumerate(stripped[start:], start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return stripped[start : i + 1]
    return stripped  # unbalanced braces; json.loads will fail informatively


# ── Provider adapters ─────────────────────────────────────────────────────────

class _BaseAdapter(ABC):
    """Shared interface all provider adapters must implement."""

    @property
    @abstractmethod
    def model_name(self) -> str: ...

    @abstractmethod
    def call(
        self,
        system: str,
        user: str,
        *,
        json_mode: bool,
        model: str,
        temperature: float,
        max_tokens: int,
        stream: bool = False,
        images: list[dict[str, Any]] | None = None,
    ) -> tuple[str, int, dict]:
        """Return (content_str, duration_ms, call_meta).  Raise LLMTruncationError
        on cut-off.

        ``stream`` requests incremental delivery where the adapter supports it.
        It never changes the return contract or token accounting — it only lets
        an adapter bypass client-side non-streaming guards (e.g. the Anthropic
        SDK's 10-minute limit that trips on large max_tokens).  Adapters that do
        not implement streaming accept the flag and ignore it.

        ``images``, when given, is a list of
        ``{"media_type": "image/jpeg", "data_b64": "...", "alt": "..."}`` dicts
        placed before the text in the user message. Adapters without vision
        support raise NotImplementedError when the list is non-empty.

        ``call_meta`` is this call's contribution to AGENTS.md's
        ``llm_provenance`` (LLM Provenance and Reasoning Configuration): a dict
        with ``reasoning`` (``{"enabled", "mode", "effort"}``) and ``tokens``
        (``{"reported_as": "split", "input", "output"}`` or
        ``{"reported_as": "combined", "total"}``), read from the provider's own
        response object — never estimated. ``ProvenanceCollector`` accumulates
        these across an agent's calls into the final shelf-record block.
        """

    def call_json_websearch(
        self,
        system: str,
        user: str,
        *,
        model: str,
        max_tokens: int,
        max_uses: int = 5,
        allowed_domains: list[str] | None = None,
    ) -> tuple[str, int, dict]:
        """Return (content_str, duration_ms, call_meta) after a web-search-enabled
        LLM call. See ``call()`` for the ``call_meta`` shape.

        Only implemented by ``_AnthropicFoundryAdapter`` (server-side web_search
        tool via Anthropic API).  All other adapters raise ``NotImplementedError``
        so callers can detect unsupported backends.

        ``allowed_domains``, when given, restricts the server-side search to
        those domains (e.g. EX1/EX2 restrict to official institutional sources).
        ``None`` means unrestricted search, the existing P1/P2/P3 behaviour.
        """
        raise NotImplementedError(
            "Web-search-enabled LLM calls are only supported on the "
            "'foundry_anthropic' backend.  Current adapter: "
            f"{type(self).__name__}."
        )


class _OpenAIAdapter(_BaseAdapter):
    """Adapter for OpenAI-compatible APIs (openai, and future foundry_openai)."""

    def __init__(self, api_key: str, model: str, base_url: str | None = None):
        from openai import OpenAI  # local import — never imported on Anthropic path
        kwargs: dict[str, Any] = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        self._client = OpenAI(**kwargs)
        self._model = model

    @property
    def model_name(self) -> str:
        return self._model

    def call(
        self,
        system: str,
        user: str,
        *,
        json_mode: bool,
        model: str,
        temperature: float,
        max_tokens: int,
        stream: bool = False,  # accepted for interface parity — not implemented here
        images: list[dict[str, Any]] | None = None,
    ) -> tuple[str, int, dict]:
        if images:
            raise NotImplementedError(
                "Vision (images=...) is only supported on the 'foundry_anthropic' "
                f"and 'azure_openai' backends. Current adapter: {type(self).__name__}."
            )
        t0 = time.monotonic()
        kwargs: dict[str, Any] = {
            "model": model,
            "temperature": temperature,
            "max_completion_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        resp = self._client.chat.completions.create(**kwargs)
        duration_ms = int((time.monotonic() - t0) * 1000)

        finish_reason = (resp.choices[0].finish_reason or "").lower()
        if finish_reason == "length":
            raise LLMTruncationError(
                f"OpenAI response truncated (finish_reason='length') — "
                f"model={model}, max_tokens={max_tokens}. "
                "Raise EPOCH_LLM_MAX_TOKENS or the agent-specific override."
            )

        content = resp.choices[0].message.content or ""
        if not content.strip():
            raise LLMEmptyResponseError(
                f"OpenAI returned empty content (finish_reason={finish_reason!r}) — "
                f"model={model}, max_tokens={max_tokens}. "
                "Raise EPOCH_LLM_MAX_TOKENS or the agent-specific override."
            )
        # This adapter sends no reasoning parameter — per
        # .agents/llm-backend-reference.md it must not back an LLM agent until
        # it does; reasoning.enabled=False makes that visible on the record.
        usage = getattr(resp, "usage", None)
        call_meta = {
            "reasoning": {"enabled": False, "mode": None, "effort": None},
            "tokens": {
                "reported_as": "split",
                "input": getattr(usage, "prompt_tokens", None),
                "output": getattr(usage, "completion_tokens", None),
            },
        }
        return content, duration_ms, call_meta


class _AzureOpenAIAdapter(_BaseAdapter):
    """Adapter for Azure OpenAI deployments (gpt-5.x reasoning models).

    Key differences from plain _OpenAIAdapter:
    - Uses ``openai.AzureOpenAI`` (requires ``api_version`` + ``azure_endpoint``).
    - Sends ``reasoning_effort`` when configured (gpt-5.x o-series style).
    - Does NOT send ``temperature`` — reasoning models reject it with a 400.
    - Uses ``max_completion_tokens`` (Azure OpenAI convention).

    Credentials are read from the env vars listed in config.ACTIVE_BACKEND
    (EPOCH_AZURE_OPENAI_API_KEY / EPOCH_AZURE_OPENAI_ENDPOINT).
    """

    _AZURE_SCOPE = "https://cognitiveservices.azure.com/.default"

    def __init__(
        self,
        api_key: str,
        model: str,
        azure_endpoint: str,
        api_version: str,
        reasoning_effort: str | None = None,
        auth_type: str = "key",
    ):
        from openai import AzureOpenAI  # local import
        if auth_type == "azure_ad":
            from azure.identity import DefaultAzureCredential, get_bearer_token_provider
            token_provider = get_bearer_token_provider(
                DefaultAzureCredential(), self._AZURE_SCOPE
            )
            self._client = AzureOpenAI(
                azure_endpoint=azure_endpoint,
                azure_ad_token_provider=token_provider,
                api_version=api_version,
            )
        else:
            self._client = AzureOpenAI(
                api_key=api_key,
                azure_endpoint=azure_endpoint,
                api_version=api_version,
            )
        self._model = model
        self._reasoning_effort = reasoning_effort

    @property
    def model_name(self) -> str:
        return self._model

    def call(
        self,
        system: str,
        user: str,
        *,
        json_mode: bool,
        model: str,
        temperature: float,  # received but NOT forwarded — reasoning models reject it
        max_tokens: int,
        stream: bool = False,  # accepted for interface parity — not implemented here
        images: list[dict[str, Any]] | None = None,
    ) -> tuple[str, int, dict]:
        t0 = time.monotonic()
        user_content: Any = user
        if images:
            user_content = [
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{img['media_type']};base64,{img['data_b64']}"},
                }
                for img in images
            ] + [{"type": "text", "text": user}]
        kwargs: dict[str, Any] = {
            "model": model,
            "max_completion_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user_content},
            ],
        }
        if self._reasoning_effort:
            kwargs["reasoning_effort"] = self._reasoning_effort
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        resp = self._client.chat.completions.create(**kwargs)
        duration_ms = int((time.monotonic() - t0) * 1000)

        finish_reason = (resp.choices[0].finish_reason or "").lower()
        if finish_reason == "length":
            raise LLMTruncationError(
                f"AzureOpenAI response truncated (finish_reason='length') — "
                f"model={model}, max_tokens={max_tokens}. "
                "Raise EPOCH_LLM_MAX_TOKENS or the agent-specific override."
            )
        if finish_reason == "content_filter":
            raise LLMEmptyResponseError(
                f"AzureOpenAI content filter triggered (finish_reason='content_filter') — "
                f"model={model}. Review the prompt or content filter policy."
            )

        content = resp.choices[0].message.content or ""
        if not content.strip():
            raise LLMEmptyResponseError(
                f"AzureOpenAI returned empty content (finish_reason={finish_reason!r}) — "
                f"model={model}, max_tokens={max_tokens}. "
                "Raise EPOCH_LLM_MAX_TOKENS or the agent-specific override."
            )
        usage = getattr(resp, "usage", None)
        call_meta = {
            "reasoning": {
                "enabled": bool(self._reasoning_effort),
                "mode": "reasoning_effort" if self._reasoning_effort else None,
                "effort": self._reasoning_effort,
            },
            "tokens": {
                "reported_as": "split",
                "input": getattr(usage, "prompt_tokens", None),
                "output": getattr(usage, "completion_tokens", None),
            },
        }
        return content, duration_ms, call_meta


class _AnthropicFoundryAdapter(_BaseAdapter):
    """Adapter for Microsoft Azure Foundry serving Claude via Anthropic SDK.

    Auth modes (EPOCH_FOUNDRY_ANTHROPIC_AUTH_TYPE in .env):
      "key"      — API key sent as-is (default, but often disabled by Azure policy)
      "azure_ad" — Azure AD token via DefaultAzureCredential (az login / managed
                   identity / env vars). Requires azure-identity package.

    Quirks handled automatically:
    - temperature: NOT sent (Opus 4.8 rejects sampling params with a 400).
    - budget_tokens: NOT sent (rejected with a 400 on Opus 5 / 4.8 / 4.7 and
      Sonnet 5). thinking={"type": "adaptive"} + output_config={"effort": ...}
      IS sent when a reasoning_effort is configured (confirmed against the live
      endpoint — no 400, and thinking_tokens increases with effort). Omitting
      both leaves thinking off, matching the pre-provenance behaviour.
    - JSON mode: Anthropic has no response_format — prompts already say
      "Return exactly one JSON object", so no extra parameter needed.
    - Response: content is a list of blocks; we join all text blocks.
    - Truncation: stop_reason == "max_tokens" raises LLMTruncationError.
    """

    _AZURE_SCOPE = "https://cognitiveservices.azure.com/.default"

    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str,
        auth_type: str = "key",
        reasoning_effort: str | None = None,
    ):
        from anthropic import AnthropicFoundry  # local import
        # base_url must point to the /anthropic endpoint, e.g.:
        # https://<your-resource>.services.ai.azure.com/anthropic
        if auth_type == "azure_ad":
            from azure.identity import DefaultAzureCredential
            _credential = DefaultAzureCredential()

            def _token_provider() -> str:
                return _credential.get_token(self._AZURE_SCOPE).token

            self._client = AnthropicFoundry(
                base_url=base_url,
                azure_ad_token_provider=_token_provider,
            )
        else:
            self._client = AnthropicFoundry(base_url=base_url, api_key=api_key)
        self._model = model
        self._reasoning_effort = reasoning_effort

    def _reasoning_kwargs(self) -> dict[str, Any]:
        """thinking + output_config kwargs for messages.create()/stream(), or {}
        when no reasoning_effort is configured (thinking stays off)."""
        if not self._reasoning_effort:
            return {}
        return {
            "thinking": {"type": "adaptive"},
            "output_config": {"effort": self._reasoning_effort},
        }

    def _reasoning_meta(self) -> dict[str, Any]:
        return {
            "enabled": bool(self._reasoning_effort),
            "mode": "adaptive" if self._reasoning_effort else None,
            "effort": self._reasoning_effort,
        }

    @property
    def model_name(self) -> str:
        return self._model

    def call(
        self,
        system: str,
        user: str,
        *,
        json_mode: bool,  # noted but not forwarded — Anthropic has no equivalent
        model: str,
        temperature: float,  # noted but not forwarded — Opus 4.8 rejects it
        max_tokens: int,
        stream: bool = False,
        images: list[dict[str, Any]] | None = None,
    ) -> tuple[str, int, dict]:
        t0 = time.monotonic()

        user_content: Any = user
        if images:
            user_content = [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": img["media_type"],
                        "data": img["data_b64"],
                    },
                }
                for img in images
            ] + [{"type": "text", "text": user}]

        reasoning_kwargs = self._reasoning_kwargs()

        # Streaming bypasses the SDK's non-streaming 10-minute guard, which trips
        # when max_tokens is large (adaptive thinking + output can be estimated
        # over the limit). get_final_message() returns the same Message shape as
        # messages.create(), so stop_reason / content handling below is identical.
        if stream:
            with self._client.messages.stream(
                model=model,
                max_tokens=max_tokens,  # required by Anthropic API
                system=system,
                messages=[{"role": "user", "content": user_content}],
                **reasoning_kwargs,
            ) as s:
                resp = s.get_final_message()
        else:
            resp = self._client.messages.create(
                model=model,
                max_tokens=max_tokens,  # required by Anthropic API
                system=system,
                messages=[{"role": "user", "content": user_content}],
                **reasoning_kwargs,
            )
        duration_ms = int((time.monotonic() - t0) * 1000)

        if (resp.stop_reason or "").lower() == "max_tokens":
            raise LLMTruncationError(
                f"Anthropic response truncated (stop_reason='max_tokens') — "
                f"model={model}, max_tokens={max_tokens}. "
                "Raise EPOCH_LLM_MAX_TOKENS or the agent-specific override."
            )

        content = "".join(
            block.text for block in resp.content if block.type == "text"
        )
        if not content.strip():
            block_types = [block.type for block in resp.content]
            raise LLMEmptyResponseError(
                f"Anthropic returned empty text content "
                f"(stop_reason={resp.stop_reason!r}, block_types={block_types}) — "
                f"model={model}, max_tokens={max_tokens}. "
                "Likely cause: thinking tokens consumed output budget, or refusal. "
                "Raise EPOCH_LLM_MAX_TOKENS or the agent-specific override."
            )
        call_meta = {
            "reasoning": self._reasoning_meta(),
            "tokens": {
                "reported_as": "split",
                "input": getattr(resp.usage, "input_tokens", None),
                "output": getattr(resp.usage, "output_tokens", None),
            },
        }
        return content, duration_ms, call_meta

    def call_json_websearch(
        self,
        system: str,
        user: str,
        *,
        model: str,
        max_tokens: int,
        max_uses: int = 5,
        allowed_domains: list[str] | None = None,
    ) -> tuple[str, int, dict]:
        """Return (content_str, duration_ms, call_meta) using the Anthropic
        server-side web_search tool. See ``call()`` for the ``call_meta`` shape.

        The web_search_20250305 server tool lets the model perform live web
        searches during a single API call.  The server handles the search loop;
        the client receives the final message (with any web_search_tool_result
        blocks followed by the model's text).  Only text blocks are extracted —
        tool-related blocks are silently skipped, which is correct because P1's
        final output is always a JSON text block after the search phase.

        ``allowed_domains``, when given, is passed through to the tool so the
        server restricts search results to those domains (e.g. EX1/EX2 limit
        search to official institutional sources).  ``None`` (the P1/P2/P3
        default) leaves search unrestricted.

        Uses streaming (same reason as ``call`` with stream=True: large max_tokens
        can trip the SDK's non-streaming 10-minute guard).

        Retries once on transport errors (httpx.RemoteProtocolError — "peer closed
        connection without sending complete message body").  These occur when Azure
        Foundry drops the streaming connection before the response is complete,
        typically under high load or when the generation takes too long.  A single
        retry is enough to handle transient drops; a second failure propagates.

        Raises:
          LLMTruncationError  — if stop_reason is "max_tokens".
          LLMEmptyResponseError — if no text content was returned.
        """
        import httpx  # local import — only used on the Foundry path

        t0 = time.monotonic()
        tools = [
            {
                "type": "web_search_20250305",
                "name": "web_search",
                "max_uses": max_uses,
                **({"allowed_domains": allowed_domains} if allowed_domains else {}),
            }
        ]

        reasoning_kwargs = self._reasoning_kwargs()

        def _stream_once() -> object:
            with self._client.messages.stream(
                model=model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
                tools=tools,  # type: ignore[arg-type]
                **reasoning_kwargs,
            ) as s:
                return s.get_final_message()

        try:
            resp = _stream_once()
        except httpx.RemoteProtocolError:
            # Transient connection drop from Azure Foundry — retry once.
            resp = _stream_once()

        duration_ms = int((time.monotonic() - t0) * 1000)

        if (resp.stop_reason or "").lower() == "max_tokens":
            raise LLMTruncationError(
                f"Anthropic web-search response truncated (stop_reason='max_tokens') — "
                f"model={model}, max_tokens={max_tokens}. "
                "Raise EPOCH_P1_MAX_TOKENS or the agent-specific override."
            )

        # Extract only text blocks — web_search_tool_result blocks are skipped.
        content = "".join(
            block.text for block in resp.content if block.type == "text"
        )
        if not content.strip():
            block_types = [block.type for block in resp.content]
            raise LLMEmptyResponseError(
                f"Anthropic web-search returned empty text content "
                f"(stop_reason={resp.stop_reason!r}, block_types={block_types}) — "
                f"model={model}, max_tokens={max_tokens}. "
                "Raise EPOCH_P1_MAX_TOKENS or the agent-specific override."
            )
        call_meta = {
            "reasoning": self._reasoning_meta(),
            "tokens": {
                "reported_as": "split",
                "input": getattr(resp.usage, "input_tokens", None),
                "output": getattr(resp.usage, "output_tokens", None),
            },
        }
        return content, duration_ms, call_meta


# ── LLMClient ─────────────────────────────────────────────────────────────────

class LLMClient:
    """Provider-agnostic LLM client.  All agents call complete() / complete_json()
    without knowing which backend is active."""

    def __init__(self, adapter: _BaseAdapter):
        self._adapter = adapter

    @property
    def model_name(self) -> str:
        """Configured default model used by agent calls."""
        return self._adapter.model_name

    def complete(
        self,
        system: str,
        user: str,
        *,
        json_mode: bool = False,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        stream: bool = False,
        images: list[dict[str, Any]] | None = None,
        provenance: "ProvenanceCollector | None" = None,
    ) -> tuple[str, int]:
        """Return (content_str, duration_ms).

        Raises LLMTruncationError if the model output was cut off at max_tokens.

        ``stream`` is forwarded to the adapter; only adapters that implement
        streaming act on it (currently the Anthropic Foundry adapter).

        ``images``, when given, is forwarded to the adapter (currently supported
        by the Anthropic Foundry and Azure OpenAI adapters; others raise
        NotImplementedError).

        ``provenance``, when given, receives this call's ``call_meta`` (model
        identity, reasoning, token usage) for AGENTS.md's ``llm_provenance``
        record. Return shape is unchanged either way.
        """
        content, duration_ms, call_meta = self._adapter.call(
            system,
            user,
            json_mode=json_mode,
            model=model or config.MODEL,
            temperature=temperature if temperature is not None else config.TEMPERATURE,
            max_tokens=max_tokens or config.MAX_TOKENS,
            stream=stream,
            images=images,
        )
        if provenance is not None:
            provenance.add(call_meta)
        return content, duration_ms

    def complete_json(
        self,
        system: str,
        user: str,
        *,
        provenance: "ProvenanceCollector | None" = None,
        **kwargs,
    ) -> tuple[dict, int]:
        """Return (parsed_dict, duration_ms).

        Strips ```json … ``` fences before parsing so both OpenAI and Anthropic
        responses are handled uniformly.  Raises LLMTruncationError on cut-off.

        On LLMEmptyResponseError (empty text content) or JSONDecodeError (non-empty
        but non-JSON prose — e.g. thinking tokens crowded out the output), retries
        once with doubled max_tokens and a terse "JSON only" reminder appended to the
        user prompt.  If the retry still fails, the exception propagates.

        ``provenance``, when given, accumulates every attempt's ``call_meta``
        (including the retry, if one happens — both are real provider calls).
        """
        base_max = kwargs.get("max_tokens") or config.MAX_TOKENS
        retry_max = min(base_max * 2, 128_000)
        retry_user = user + (
            "\n\nReturn ONLY the JSON object — no prose, no analysis, no explanation."
        )

        def _attempt(usr: str, **kw) -> tuple[str, int]:
            """Complete and strip the fence; raise LLMEmptyResponseError if payload is empty.

            _strip_json_fence can return an empty inner group from a blank ``` ``` fence
            (e.g. ```json\\n\\n```), which passes the raw.strip() guard but causes
            json.loads("") to raise JSONDecodeError.  By checking *after* stripping here,
            blank-fence responses flow through the same retry path as adapter-level empty
            responses, and the caller always sees LLMEmptyResponseError — never a raw
            JSONDecodeError — when the model returns degenerate output.
            """
            raw, ms = self.complete(system, usr, json_mode=True, provenance=provenance, **kw)
            payload = _strip_json_fence(raw).strip()
            if not payload:
                raise LLMEmptyResponseError(
                    "Model returned empty content (blank fence or empty response). "
                    "Raise the agent-specific EPOCH_*_MAX_TOKENS or check provider logs."
                )
            return payload, ms

        try:
            payload, ms = _attempt(user, **kwargs)
            return json.loads(payload), ms
        except (LLMEmptyResponseError, json.JSONDecodeError):
            # Retry on both empty responses and non-JSON prose (e.g. thinking tokens
            # crowded out the output, or the model returned explanation text instead
            # of a JSON object).  Double the token budget and remind the model.
            # Any exception from the retry propagates directly — no second catch.
            payload, ms = _attempt(retry_user, **{**kwargs, "max_tokens": retry_max})
            return json.loads(payload), ms

    def complete_json_websearch(
        self,
        system: str,
        user: str,
        *,
        model: str | None = None,
        max_tokens: int | None = None,
        max_uses: int = 5,
        allowed_domains: list[str] | None = None,
        provenance: "ProvenanceCollector | None" = None,
    ) -> tuple[dict, int]:
        """Return (parsed_dict, duration_ms) after a web-search-enabled LLM call.

        Delegates to the adapter's ``call_json_websearch`` method (only
        ``_AnthropicFoundryAdapter`` implements it; other adapters raise
        ``NotImplementedError``).  Strips ```json … ``` fences before parsing.

        ``allowed_domains`` restricts the server-side search to those domains
        (e.g. EX1/EX2 restrict to official institutional sources).  ``None``
        (the P1/P2/P3 default) leaves search unrestricted.

        Unlike ``complete_json``, this method does NOT retry on empty / JSON error —
        web-search calls are already expensive; a failure should surface immediately
        so the caller can decide how to proceed.

        ``provenance``, when given, receives the call's ``call_meta``.

        Raises:
          NotImplementedError  — if the active backend does not support web search.
          LLMTruncationError   — if the response was cut off at max_tokens.
          LLMEmptyResponseError — if no text content was returned.
          json.JSONDecodeError — if the model's final text is not valid JSON.
        """
        def _ws_attempt(usr: str, *, uses: int) -> tuple[str, int]:
            raw, ms, call_meta = self._adapter.call_json_websearch(
                system,
                usr,
                model=model or config.MODEL,
                max_tokens=max_tokens or config.MAX_TOKENS,
                max_uses=uses,
                allowed_domains=allowed_domains,
            )
            if provenance is not None:
                provenance.add(call_meta)
            # _extract_json_blob handles prose-wrapped JSON (model sometimes writes
            # a preamble like "Based on my research..." before the JSON block).
            payload = _extract_json_blob(raw).strip()
            if not payload:
                raise LLMEmptyResponseError(
                    "Web-search LLM call returned empty JSON payload after extraction. "
                    "Check the prompt — model may have returned prose or an empty fence. "
                    f"Raw response (first 500 chars): {raw[:500]!r}"
                )
            return payload, ms

        retry_user = (
            user
            + "\n\nReturn ONLY the JSON object — no prose, no analysis, no explanation."
        )
        try:
            payload, ms = _ws_attempt(user, uses=max_uses)
            return json.loads(payload), ms
        except (LLMEmptyResponseError, json.JSONDecodeError):
            # Retry once with web search + a strict JSON-only reminder.
            # The first attempt may have produced prose-wrapped JSON; the reminder
            # forces clean output.  Web search uses the same max_uses budget.
            payload, ms = _ws_attempt(retry_user, uses=max_uses)
            return json.loads(payload), ms


# ── Factory ───────────────────────────────────────────────────────────────────

def _build_adapter(ab: config.ActiveBackend) -> _BaseAdapter:
    """Build the adapter for the given backend.

    Dispatches on ``ab.kind`` — a single place that handles both named backend
    presets (EPOCH_LLM_BACKEND) and the legacy provider path. Takes the backend
    explicitly (rather than reading config.ACTIVE_BACKEND) so callers can build
    an adapter for any preset, not just the process-wide active one — this is
    what lets different agents run against different backends in one process.
    """
    kind = ab.kind
    api_key = ab.api_key
    model = ab.model
    base_url = ab.base_url

    if kind == "azure_openai":
        if not base_url:
            raise ValueError(
                f"Backend '{ab.name}': no endpoint found in env var "
                f"'{config.BACKENDS.get(ab.name.replace('legacy:', ''), None) and config.BACKENDS[ab.name].base_url_env}'. "
                "Set EPOCH_AZURE_OPENAI_ENDPOINT in .env."
            )
        api_version = ab.api_version or "2024-12-01-preview"
        return _AzureOpenAIAdapter(
            api_key=api_key,
            model=model,
            azure_endpoint=base_url,
            api_version=api_version,
            reasoning_effort=ab.reasoning_effort,
            auth_type=ab.auth_type,
        )

    if kind == "foundry_anthropic":
        if not base_url:
            raise ValueError(
                f"Backend '{ab.name}': no base_url found. "
                "Set the corresponding EPOCH_FOUNDRY_*_BASE_URL in .env "
                "(e.g. https://<resource>.services.ai.azure.com/anthropic)"
            )
        return _AnthropicFoundryAdapter(
            api_key=api_key, model=model, base_url=base_url,
            auth_type=ab.auth_type, reasoning_effort=ab.reasoning_effort,
        )

    if kind == "openai":
        return _OpenAIAdapter(api_key=api_key, model=model, base_url=base_url)

    if kind == "foundry_openai":
        if not base_url:
            raise ValueError(
                f"Backend '{ab.name}': no base_url found. "
                "Set EPOCH_FOUNDRY_OPENAI_BASE_URL in .env."
            )
        return _OpenAIAdapter(api_key=api_key, model=model, base_url=base_url)

    raise ValueError(
        f"No adapter implemented for backend kind '{kind}' (backend='{ab.name}'). "
        "Add a branch in _build_adapter() in core/llm_client.py."
    )


# One client per backend preset, cached by backend name, so distinct agents
# can hold their own adapter (own api_key/base_url/kind) at the same time —
# e.g. R1 on a foundry_anthropic preset and DA1 on an azure_openai preset in
# the same process. Recreated on demand if reset_llm() clears the cache.
_clients: dict[str, LLMClient] = {}


def get_llm(backend: config.ActiveBackend | None = None) -> LLMClient:
    """Return the LLMClient for *backend*, building and caching it if new.

    Defaults to config.ACTIVE_BACKEND — the process-wide default — when no
    backend is given, matching every existing call site's behaviour.
    """
    ab = backend or config.ACTIVE_BACKEND
    client = _clients.get(ab.name)
    if client is None:
        client = LLMClient(_build_adapter(ab))
        _clients[ab.name] = client
    return client


def llm_for_agent(agent: str) -> tuple[LLMClient, str]:
    """Return (client, model) for *agent*, honoring any per-agent backend
    override set via config.set_agent_backend().

    This is the entry point agents should call instead of get_llm() directly
    when they want to support per-agent model selection.
    """
    return get_llm(config.backend_for_agent(agent)), config.model_for_agent(agent)


def reset_llm() -> None:
    """Clear the LLM client cache so the next get_llm() call builds fresh adapters.

    Called by config.set_llm_backend() after switching the default backend at
    runtime. Not needed for normal CLI usage (each process starts clean).
    """
    global _clients
    _clients = {}
