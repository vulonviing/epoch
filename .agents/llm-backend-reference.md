# LLM backend reference — provider parameter mapping

Wire-level detail for `llm_provenance` recording (AGENTS.md's "LLM Provenance
and Reasoning Configuration" hard rule). This file is provider-SDK reference,
not a project rule — read it only when touching `core/llm_client.py`'s
adapters or `config.py`'s `BACKENDS`. The rule itself (model identity must be
recorded, reasoning must be on and explicit, tokens must come from the
provider's own usage object) lives in AGENTS.md and is not restated here.

## Record shape

```json
"llm_provenance": {
  "provider": "foundry_anthropic",
  "backend_preset": "opus48",
  "model": "claude-opus-4-8",
  "reasoning": {"enabled": true, "mode": "adaptive", "effort": "high"},
  "tokens": {"reported_as": "split", "input": 18412, "output": 3907},
  "calls": 1
}
```

## Provider field mapping

Every API spells these concerns differently, and the record keeps the
provider's own field names rather than inventing a portable vocabulary:

| Concern | `foundry_anthropic` (Anthropic SDK) | `azure_openai` / `openai` (OpenAI SDK) |
|---|---|---|
| Output cap | `max_tokens` | `max_completion_tokens` |
| Reasoning switch | `thinking={"type": "adaptive"}` — the only valid on-mode on current models; `budget_tokens` is rejected with a 400 on Opus 5 / 4.8 / 4.7 and Sonnet 5. Omitting `thinking` leaves thinking **off** on Opus 4.8/4.7 and Sonnet 4.6. | no separate switch — `reasoning_effort=` is both the switch and the level |
| Effort | `output_config={"effort": "low\|medium\|high\|xhigh\|max"}` | `reasoning_effort="low\|medium\|high\|xhigh"` |
| Tokens | `usage.input_tokens`, `usage.output_tokens`, `usage.cache_read_input_tokens`, `usage.cache_creation_input_tokens` — split | `usage.prompt_tokens`, `usage.completion_tokens`, `usage.total_tokens`, `usage.completion_tokens_details.reasoning_tokens` — split plus a provider total |

A backend of kind `openai` (`_OpenAIAdapter`) sends no reasoning parameter at
all today, so it cannot satisfy AGENTS.md's reasoning requirement and must not
back an LLM agent until it does.
