"""Central configuration for data modes, paths, and experiment runs."""
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from dotenv import load_dotenv
import os

# Walk up to find .env (works whether called from package root or notebook)
_here = Path(__file__).resolve()
for _parent in [_here.parent, _here.parent.parent, _here.parent.parent.parent]:
    _env = _parent / ".env"
    if _env.exists():
        load_dotenv(_env)
        break

# ── Named Backend Presets ──────────────────────────────────────────────
# EPOCH_LLM_BACKEND selects a named preset that fully specifies the model,
# adapter kind, endpoint, and auth settings.  When EPOCH_LLM_BACKEND is set
# the legacy EPOCH_LLM_PROVIDER path is bypassed.  When it is empty (or not
# set), the legacy path is used unchanged — full backward compatibility.
#
# Supported backend names:
#   "opus48"    — claude-opus-4-8   (Foundry Anthropic, azure_ad auth, reasoning_effort=high)
#   "opus5"     — claude-opus-5     (Foundry Anthropic, azure_ad auth, reasoning_effort=high)
#   "sonnet46"  — claude-sonnet-4-6 (Foundry Anthropic, azure_ad auth, reasoning_effort=high)
#   "gpt55"     — gpt-5.5           (Azure OpenAI, reasoning_effort=xhigh)
#   "gpt56sol"  — gpt-5.6-sol       (Azure OpenAI, reasoning_effort=xhigh)
#   "gpt54mini" — gpt-5.4-mini      (Azure OpenAI, reasoning_effort=medium)
#
# Each backend reads its secrets from the env vars listed in key_env /
# base_url_env — set those in .env.  The registry itself contains NO secrets.
#
# Per-backend overrides (env vars, all optional):
#   EPOCH_BACKEND_<NAME>_REASONING_EFFORT  — override effort level
#   EPOCH_BACKEND_<NAME>_API_VERSION       — override api_version
# Example: EPOCH_BACKEND_GPT55_REASONING_EFFORT=medium


@dataclass(frozen=True)
class Backend:
    """Immutable descriptor for one named LLM backend preset."""
    kind: str                        # "azure_openai" | "foundry_anthropic" | "openai" | "foundry_openai"
    model: str                       # deployment name / model id
    key_env: str                     # .env variable holding the API key
    base_url_env: str                # .env variable holding the endpoint/base_url
    auth_type: str = "key"           # "key" | "azure_ad"
    reasoning_effort: str | None = None  # azure_openai reasoning_effort=, or
                                          # foundry_anthropic output_config={"effort": ...}
    api_version: str | None = None   # azure_openai api-version string


BACKENDS: dict[str, Backend] = {
    "opus48": Backend(
        kind="foundry_anthropic",
        model="claude-opus-4-8",
        key_env="EPOCH_FOUNDRY_ANTHROPIC_API_KEY",
        base_url_env="EPOCH_FOUNDRY_ANTHROPIC_BASE_URL",
        auth_type="azure_ad",
        reasoning_effort="high",
    ),
    "opus5": Backend(
        kind="foundry_anthropic",
        model="claude-opus-5",
        key_env="EPOCH_FOUNDRY_ANTHROPIC_API_KEY",
        base_url_env="EPOCH_FOUNDRY_ANTHROPIC_BASE_URL",
        auth_type="azure_ad",
        reasoning_effort="high",
    ),
    "sonnet46": Backend(
        kind="foundry_anthropic",
        model="claude-sonnet-4-6",
        key_env="EPOCH_FOUNDRY_SONNET_API_KEY",
        base_url_env="EPOCH_FOUNDRY_SONNET_BASE_URL",
        auth_type="azure_ad",
        reasoning_effort="high",
    ),
    "gpt55": Backend(
        kind="azure_openai",
        model="gpt-5.5",
        key_env="EPOCH_AZURE_GPT55_API_KEY",
        base_url_env="EPOCH_AZURE_OPENAI_ENDPOINT",
        auth_type="azure_ad",
        reasoning_effort="xhigh",
        api_version="2024-12-01-preview",
    ),
    "gpt54mini": Backend(
        kind="azure_openai",
        model="gpt-5.4-mini",
        key_env="EPOCH_AZURE_GPT54MINI_API_KEY",
        base_url_env="EPOCH_AZURE_OPENAI_ENDPOINT",
        auth_type="azure_ad",
        reasoning_effort="medium",
        api_version="2024-12-01-preview",
    ),
    "gpt56sol": Backend(
        kind="azure_openai",
        model="gpt-5.6-sol",
        key_env="EPOCH_AZURE_GPT56SOL_API_KEY",
        base_url_env="EPOCH_AZURE_OPENAI_ENDPOINT",
        auth_type="azure_ad",
        reasoning_effort="xhigh",
        api_version="2024-12-01-preview",
    ),
}


@dataclass(frozen=True)
class ActiveBackend:
    """Normalised, fully-resolved backend struct passed to the adapter factory.

    Both the new EPOCH_LLM_BACKEND path and the legacy EPOCH_LLM_PROVIDER path
    resolve to this common shape so _build_adapter() has exactly one branch per
    kind rather than one branch per provider variant.
    """
    name: str                        # "opus48" | "opus5" | "sonnet46" | "gpt55" | "gpt56sol" | "gpt54mini" | "legacy:<provider>"
    kind: str
    model: str
    api_key: str
    base_url: str | None
    auth_type: str
    reasoning_effort: str | None
    api_version: str | None


def _resolve_backend_from_preset(name: str) -> ActiveBackend:
    """Resolve a named backend preset from BACKENDS registry into ActiveBackend."""
    name_lc = name.strip().lower()
    preset = BACKENDS.get(name_lc)
    if preset is None:
        known = ", ".join(sorted(BACKENDS))
        raise ValueError(
            f"Unknown EPOCH_LLM_BACKEND '{name}'. Known backends: {known}"
        )

    api_key = os.environ.get(preset.key_env, "")
    base_url = os.environ.get(preset.base_url_env) or None

    # Per-backend overrides for reasoning_effort and api_version
    env_prefix = f"EPOCH_BACKEND_{name_lc.upper()}"
    reasoning_effort = (
        os.environ.get(f"{env_prefix}_REASONING_EFFORT")
        or preset.reasoning_effort
    )
    api_version = (
        os.environ.get(f"{env_prefix}_API_VERSION")
        or preset.api_version
    )

    return ActiveBackend(
        name=name_lc,
        kind=preset.kind,
        model=preset.model,
        api_key=api_key,
        base_url=base_url,
        auth_type=preset.auth_type,
        reasoning_effort=reasoning_effort,
        api_version=api_version,
    )


# ── LLM Provider registry (legacy path) ───────────────────────────────
# EPOCH_LLM_PROVIDER selects the active provider.  Add new entries here as
# new provider variants become available (e.g. "foundry_openai").
#
# Supported keys and their .env namespace prefixes:
#   "openai"              → EPOCH_OPENAI_*
#   "foundry_anthropic"   → EPOCH_FOUNDRY_ANTHROPIC_*
#   (future) "foundry_openai" → EPOCH_FOUNDRY_OPENAI_*
#
# Each provider block is read as:
#   EPOCH_<NAMESPACE>_API_KEY  (required)
#   EPOCH_<NAMESPACE>_MODEL    (required)
#   EPOCH_<NAMESPACE>_BASE_URL (optional)
#
# Legacy fallback: if the namespaced key is missing, EPOCH_LLM_API_KEY /
# EPOCH_LLM_MODEL / EPOCH_LLM_BASE_URL are tried for backward compatibility.

_PROVIDER_NAMESPACES: dict[str, str] = {
    "openai": "OPENAI",
    "foundry_anthropic": "FOUNDRY_ANTHROPIC",
    "foundry_openai": "FOUNDRY_OPENAI",
}


def _resolve_provider_auth_type(provider: str) -> str:
    """Return auth_type for the given provider ('key' or 'azure_ad')."""
    ns = _PROVIDER_NAMESPACES.get(provider, "")
    key = f"EPOCH_{ns}_AUTH_TYPE" if ns else ""
    return (os.environ.get(key) or os.environ.get("EPOCH_LLM_AUTH_TYPE") or "key").strip().lower()


def _resolve_provider_str(provider: str) -> tuple[str, str, str | None]:
    """Return (api_key, model, base_url_or_None) for the given provider key."""
    ns = _PROVIDER_NAMESPACES.get(provider)
    if ns is None:
        supported = ", ".join(sorted(_PROVIDER_NAMESPACES))
        raise ValueError(
            f"Unknown EPOCH_LLM_PROVIDER '{provider}'. "
            f"Supported: {supported}"
        )
    prefix = f"EPOCH_{ns}"

    # Namespaced key takes priority; fall back to legacy EPOCH_LLM_* keys.
    api_key = (
        os.environ.get(f"{prefix}_API_KEY")
        or os.environ.get("EPOCH_LLM_API_KEY")
    )
    if not api_key:
        raise KeyError(
            f"No API key found for provider '{provider}'. "
            f"Set {prefix}_API_KEY (or legacy EPOCH_LLM_API_KEY) in .env"
        )

    model = (
        os.environ.get(f"{prefix}_MODEL")
        or os.environ.get("EPOCH_LLM_MODEL")
        or "claude-opus-4-8"
    )

    base_url = (
        os.environ.get(f"{prefix}_BASE_URL")
        or os.environ.get("EPOCH_LLM_BASE_URL")
        or None
    )
    return api_key, model, base_url


def _resolve_backend_from_provider(provider: str) -> ActiveBackend:
    """Resolve the legacy EPOCH_LLM_PROVIDER path into an ActiveBackend struct."""
    api_key, model, base_url = _resolve_provider_str(provider)
    auth_type = _resolve_provider_auth_type(provider)
    # Map provider → adapter kind (same mapping as the old _build_adapter)
    kind_map = {
        "openai": "openai",
        "foundry_anthropic": "foundry_anthropic",
        "foundry_openai": "foundry_openai",
    }
    kind = kind_map.get(provider, provider)
    return ActiveBackend(
        name=f"legacy:{provider}",
        kind=kind,
        model=model,
        api_key=api_key,
        base_url=base_url,
        auth_type=auth_type,
        # No effort on the legacy path for any provider kind — a run on this
        # path cannot satisfy AGENTS.md's reasoning requirement (R23); use a
        # named EPOCH_LLM_BACKEND preset for an LLM/Hybrid agent instead.
        reasoning_effort=None,
        api_version=None,
    )


# ── Active backend resolution ──────────────────────────────────────────
# EPOCH_LLM_BACKEND (preset name) takes priority.
# Falls back to EPOCH_LLM_PROVIDER (legacy).

_BACKEND_NAME: str = os.getenv("EPOCH_LLM_BACKEND", "").strip()
LLM_PROVIDER: str = os.getenv("EPOCH_LLM_PROVIDER", "openai").strip().lower()


def _resolve_active_backend() -> ActiveBackend:
    if _BACKEND_NAME:
        return _resolve_backend_from_preset(_BACKEND_NAME)
    return _resolve_backend_from_provider(LLM_PROVIDER)


ACTIVE_BACKEND: ActiveBackend = _resolve_active_backend()

# Legacy flat symbols — still used directly by agents and llm_client.
# When a named backend is active these are derived from it; when the legacy
# provider path is active they come from the provider namespace as before.
API_KEY: str = ACTIVE_BACKEND.api_key
MODEL: str = ACTIVE_BACKEND.model
BASE_URL: str | None = ACTIVE_BACKEND.base_url
AUTH_TYPE: str = ACTIVE_BACKEND.auth_type

TEMPERATURE: float = float(os.getenv("EPOCH_LLM_TEMPERATURE", "0.0"))
MAX_TOKENS: int = int(os.getenv("EPOCH_LLM_MAX_TOKENS", "32000"))

# ── Per-agent model policy ────────────────────────────────────────────
# No agent is pinned to a fixed backend anymore -- every agent follows the
# active MODEL (EPOCH_LLM_BACKEND) unless an explicit env override
# (EPOCH_R1_LLM_MODEL, EPOCH_DA1_LLM_MODEL, EPOCH_EX_LLM_MODEL, ...) is set.
#
# EX1/EX2 depend on call_json_websearch(), which only the foundry_anthropic
# adapter implements -- if EPOCH_LLM_BACKEND is switched to an azure_openai
# preset (gpt55 / gpt56sol / gpt54mini), EX1/EX2 will raise NotImplementedError
# unless EPOCH_EX_LLM_MODEL / a per-agent backend override points them back at
# a foundry_anthropic preset.

AGENT_MODEL_PINS: dict[str, str] = {}


def _pinned_agent_model(agent: str, env_var: str) -> str:
    """Return the model string for *agent*, respecting env override and backend guard."""
    override = os.getenv(env_var)
    if override:
        return override
    preset_name = AGENT_MODEL_PINS.get(agent)
    if preset_name and ACTIVE_BACKEND.kind == "foundry_anthropic":
        return BACKENDS[preset_name].model
    return MODEL


R1_MODEL: str  = _pinned_agent_model("R1",  "EPOCH_R1_LLM_MODEL")
DA1_MODEL: str = _pinned_agent_model("DA1", "EPOCH_DA1_LLM_MODEL")
CP1_MODEL: str = os.getenv("EPOCH_CP1_LLM_MODEL", MODEL)   # not yet scored
TS1_MODEL: str = os.getenv("EPOCH_TS1_LLM_MODEL", MODEL)   # topology selector, not yet scored
TS1_MAX_TOKENS: int = int(os.getenv("EPOCH_TS1_MAX_TOKENS", "32000"))
P1_MODEL: str  = os.getenv("EPOCH_P1_LLM_MODEL",  MODEL)   # Direct chain lead_interpreter
F1_MODEL: str  = os.getenv("EPOCH_F1_LLM_MODEL",  MODEL)   # unified topology-agnostic finalizer
R2_MODEL: str  = os.getenv("EPOCH_R2_LLM_MODEL",  MODEL)   # Layer B deferred
D2_MODEL: str  = os.getenv("EPOCH_D2_LLM_MODEL",  MODEL)   # not yet scored

# Per-agent max_tokens overrides — fall back to MAX_TOKENS.
# Two tiers under Opus 5 (its adaptive thinking counts against max_tokens and
# runs heavier than Opus 4.8's did):
#   - "medium" (32000): per-batch analysts and small single-call agents.
#   - "heavy"  (64000): single-call agents that emit one whole-portfolio or
#     whole-table JSON (F1, F2, R1, C2) — these are the ones that actually
#     truncated at 32000.
R1_MAX_TOKENS:  int = int(os.getenv("EPOCH_R1_MAX_TOKENS",  "64000"))   # also re-spent on its own self-repair call
R2_MAX_TOKENS:  int = int(os.getenv("EPOCH_R2_MAX_TOKENS",  str(MAX_TOKENS)))
DA1_MAX_TOKENS: int = int(os.getenv("EPOCH_DA1_MAX_TOKENS", str(MAX_TOKENS)))
D1_MAX_TOKENS:  int = int(os.getenv("EPOCH_D1_MAX_TOKENS",  str(MAX_TOKENS)))
D2_MAX_TOKENS:  int = int(os.getenv("EPOCH_D2_MAX_TOKENS",  str(MAX_TOKENS)))   # UC1 threshold scope needs headroom
CP1_MAX_TOKENS: int = int(os.getenv("EPOCH_CP1_MAX_TOKENS", str(MAX_TOKENS)))
P1_MAX_TOKENS:      int = int(os.getenv("EPOCH_P1_MAX_TOKENS",      str(MAX_TOKENS)))   # per-batch budget (10 rows × ~1600 tok)
P1_BATCH_SIZE:      int = int(os.getenv("EPOCH_P1_BATCH_SIZE",      "5"))        # locations per batch (all of a site's rule-rows stay together)
P1_MAX_CONCURRENCY: int = int(os.getenv("EPOCH_P1_MAX_CONCURRENCY", "8"))        # max parallel batch workers
P1_WEB_MAX_USES:    int = int(os.getenv("EPOCH_P1_WEB_MAX_USES",    "5"))        # web search calls per batch
F1_MAX_TOKENS:      int = int(os.getenv("EPOCH_F1_MAX_TOKENS",      "64000"))   # single-call full-portfolio budget

# Debate topology output agents — P2 (Energy-Method Interpreter) + P3 (Reported-Method Interpreter)
# P2 and P3 run concurrently; default concurrency of 4 avoids doubling Foundry web-search load.
P2_MODEL: str           = os.getenv("EPOCH_P2_LLM_MODEL",          MODEL)
P2_MAX_TOKENS:      int = int(os.getenv("EPOCH_P2_MAX_TOKENS",      "32000"))  # per-batch budget (all quarters of a site)
P2_BATCH_SIZE:      int = int(os.getenv("EPOCH_P2_BATCH_SIZE",      "5"))       # sites per batch
P2_MAX_CONCURRENCY: int = int(os.getenv("EPOCH_P2_MAX_CONCURRENCY", "4"))       # reduced: P2‖P3 doubles web-search load
P2_WEB_MAX_USES:    int = int(os.getenv("EPOCH_P2_WEB_MAX_USES",    "5"))

P3_MODEL: str           = os.getenv("EPOCH_P3_LLM_MODEL",          MODEL)
P3_MAX_TOKENS:      int = int(os.getenv("EPOCH_P3_MAX_TOKENS",      "32000"))
P3_BATCH_SIZE:      int = int(os.getenv("EPOCH_P3_BATCH_SIZE",      "5"))
P3_MAX_CONCURRENCY: int = int(os.getenv("EPOCH_P3_MAX_CONCURRENCY", "4"))
P3_WEB_MAX_USES:    int = int(os.getenv("EPOCH_P3_WEB_MAX_USES",    "5"))

# S1 Synthesizer — no web search; batched by site (same pattern as P2/P3)
S1_MODEL: str           = os.getenv("EPOCH_S1_LLM_MODEL",          MODEL)
S1_MAX_TOKENS:      int = int(os.getenv("EPOCH_S1_MAX_TOKENS",      "32000"))   # per-batch budget
S1_BATCH_SIZE:      int = int(os.getenv("EPOCH_S1_BATCH_SIZE",      "5"))       # sites per batch
S1_MAX_CONCURRENCY: int = int(os.getenv("EPOCH_S1_MAX_CONCURRENCY", "4"))

# UC4 document pipeline — RC1.1/RC1.2 (regulatory change classifier blind-pass
# pair) + RM1.1/RM1.2 (Siemens exposure mapper blind-pass pair), all four pure
# LLM agents batched by ESRS standard (5 batches: E1-E5). No web search; all
# four read from the deterministic corpus/ layer. All four default to the
# same model tier (MODEL) so any divergence between blind and reconciled
# passes comes from viewpoint, not model strength (AGENTS.md decision #7).
RC1_1_MODEL: str           = os.getenv("EPOCH_RC1_1_LLM_MODEL",          MODEL)
RC1_1_MAX_TOKENS:      int = int(os.getenv("EPOCH_RC1_1_MAX_TOKENS",      "32000"))
RC1_1_MAX_CONCURRENCY: int = int(os.getenv("EPOCH_RC1_1_MAX_CONCURRENCY", "5"))

RC1_MODEL: str           = os.getenv("EPOCH_RC1_LLM_MODEL",          MODEL)
RC1_MAX_TOKENS:      int = int(os.getenv("EPOCH_RC1_MAX_TOKENS",      "32000"))
RC1_BATCH_SIZE:      int = int(os.getenv("EPOCH_RC1_BATCH_SIZE",      "1"))     # one standard per batch
RC1_MAX_CONCURRENCY: int = int(os.getenv("EPOCH_RC1_MAX_CONCURRENCY", "5"))

RM1_1_MODEL: str           = os.getenv("EPOCH_RM1_1_LLM_MODEL",          MODEL)
RM1_1_MAX_TOKENS:      int = int(os.getenv("EPOCH_RM1_1_MAX_TOKENS",      "32000"))
RM1_1_MAX_CONCURRENCY: int = int(os.getenv("EPOCH_RM1_1_MAX_CONCURRENCY", "5"))

RM1_MODEL: str           = os.getenv("EPOCH_RM1_LLM_MODEL",          MODEL)
RM1_MAX_TOKENS:      int = int(os.getenv("EPOCH_RM1_MAX_TOKENS",      "32000"))
RM1_BATCH_SIZE:      int = int(os.getenv("EPOCH_RM1_BATCH_SIZE",      "1"))     # one standard per batch
RM1_MAX_CONCURRENCY: int = int(os.getenv("EPOCH_RM1_MAX_CONCURRENCY", "5"))

# Persona assessors (P4 Conservative / P5 Balanced / P6 Maximum-Assurance) --
# one PersonaAssessorAgent class, three agent_id instances. All three always
# run, independently, regardless of the topology TS1 selects (registry.py
# UC4 hard requirement — see plan). Batched by ESRS standard.
PERSONA_MODEL: str           = os.getenv("EPOCH_PERSONA_LLM_MODEL",          MODEL)
PERSONA_MAX_TOKENS:      int = int(os.getenv("EPOCH_PERSONA_MAX_TOKENS",      "32000"))
PERSONA_BATCH_SIZE:      int = int(os.getenv("EPOCH_PERSONA_BATCH_SIZE",      "1"))
PERSONA_MAX_CONCURRENCY: int = int(os.getenv("EPOCH_PERSONA_MAX_CONCURRENCY", "5"))

# F2 — ESRS impact finalizer. Single call, no batching (same pattern as F1).
F2_MODEL: str      = os.getenv("EPOCH_F2_LLM_MODEL",      MODEL)
F2_MAX_TOKENS: int = int(os.getenv("EPOCH_F2_MAX_TOKENS", "64000"))

# C1 (Divisional Attestor) + C2 (Coalition Synthesis Coordinator) -- UC3's
# Coalition topology. C1 fans out one isolated call per division found in
# D3's consolidation rows (division count is data-driven, not fixed here);
# C2 is a single consolidation call, structurally like S1 but for N
# independently-attested sub-results instead of two rival reads.
C1_MODEL: str           = os.getenv("EPOCH_C1_LLM_MODEL",          MODEL)
C1_MAX_TOKENS:      int = int(os.getenv("EPOCH_C1_MAX_TOKENS",      "32000"))
C1_MAX_CONCURRENCY: int = int(os.getenv("EPOCH_C1_MAX_CONCURRENCY", "5"))

C2_MODEL: str      = os.getenv("EPOCH_C2_LLM_MODEL",      MODEL)
C2_MAX_TOKENS: int = int(os.getenv("EPOCH_C2_MAX_TOKENS", "64000"))

# EX1/EX2 -- external-perspective commentator (one class, two stage instances).
# Runs once before every Stage-1 human gate (EX1) and once before every
# Stage-2 human gate (EX2), for every use case (AGENTS.md hard rule). Pinned
# to opus48 -- web search is only available on the foundry_anthropic backend,
# and this agent's whole purpose depends on live, source-grounded search
# restricted to the official institutions in ex_sources.py.
EX_MODEL: str          = _pinned_agent_model("EX", "EPOCH_EX_LLM_MODEL")
EX_MAX_TOKENS:     int = int(os.getenv("EPOCH_EX_MAX_TOKENS",     "32000"))
EX_WEB_MAX_USES:   int = int(os.getenv("EPOCH_EX_WEB_MAX_USES",   "10"))


# ── Per-agent backend selection (programmatic override) ────────────────
# Every LLM agent can be pointed at a specific backend preset,
# independently of every other agent and of the global ACTIVE_BACKEND. This
# is what lets, for example, R1 run on sonnet46 while DA1 runs on gpt55 in
# the same process -- core/llm_client.py's per-preset adapter cache is what
# makes mixing backend *kinds* (foundry_anthropic vs azure_openai) safe.
#
# Maps agent code -> the module-level "<AGENT>_MODEL" symbol name it falls
# back to when no override is set. Deterministic agents (D1, D3, RD1, RD2,
# RD3) are absent by design -- they never call an LLM.
SELECTABLE_AGENTS: dict[str, str] = {
    "R1": "R1_MODEL", "DA1": "DA1_MODEL", "R2": "R2_MODEL", "D2": "D2_MODEL",
    "CP1": "CP1_MODEL", "TS1": "TS1_MODEL",
    "P1": "P1_MODEL", "P2": "P2_MODEL", "P3": "P3_MODEL", "S1": "S1_MODEL",
    "F1": "F1_MODEL",
    "RC1.1": "RC1_1_MODEL", "RC1.2": "RC1_MODEL",
    "RM1.1": "RM1_1_MODEL", "RM1.2": "RM1_MODEL",
    "P4": "PERSONA_MODEL", "P5": "PERSONA_MODEL", "P6": "PERSONA_MODEL",
    "F2": "F2_MODEL",
    "EX1": "EX_MODEL", "EX2": "EX_MODEL",
    "C1": "C1_MODEL", "C2": "C2_MODEL",
}

# Runtime overrides set via set_agent_backend(). Empty by default -- no agent is pinned to a
# fixed backend; every agent follows the global ACTIVE_BACKEND (EPOCH_LLM_BACKEND)
# until set_agent_backend() is called explicitly.
AGENT_BACKENDS: dict[str, str] = {}


def backend_for_agent(agent: str) -> ActiveBackend:
    """Resolve the ActiveBackend for *agent*: its override preset if set,
    otherwise the process-wide ACTIVE_BACKEND."""
    preset_name = AGENT_BACKENDS.get(agent)
    if preset_name:
        return _resolve_backend_from_preset(preset_name)
    return ACTIVE_BACKEND


def model_for_agent(agent: str) -> str:
    """Resolve the model string for *agent*: an EPOCH_<AGENT>_LLM_MODEL env
    override wins first, then its override preset's model, then its default
    "<AGENT>_MODEL" module symbol."""
    env_var = f"EPOCH_{agent.replace('.', '_').upper()}_LLM_MODEL"
    override = os.getenv(env_var)
    if override:
        return override
    preset_name = AGENT_BACKENDS.get(agent)
    if preset_name:
        return BACKENDS[preset_name].model
    default_symbol = SELECTABLE_AGENTS.get(agent)
    return globals()[default_symbol] if default_symbol else MODEL


def set_agent_backend(agent: str, preset: str | None) -> None:
    """Set (or, with preset=None, clear) *agent*'s backend override.

    Validates *agent* against SELECTABLE_AGENTS and *preset* against BACKENDS.
    No adapter reset needed: core/llm_client.get_llm() caches one client per
    preset, so switching an agent's override just changes which cached
    client the next llm_for_agent(agent) call returns.
    """
    if agent not in SELECTABLE_AGENTS:
        raise ValueError(f"Unknown or non-selectable agent '{agent}'.")
    if preset is None:
        AGENT_BACKENDS.pop(agent, None)
        return
    if preset.strip().lower() not in BACKENDS:
        known = ", ".join(sorted(BACKENDS))
        raise ValueError(f"Unknown backend preset '{preset}'. Known backends: {known}")
    AGENT_BACKENDS[agent] = preset.strip().lower()


def set_llm_backend(name: str) -> None:
    """Switch the active LLM backend at runtime (in-process, for testing).

    Resolves the named preset, updates the module-level MODEL / API_KEY /
    BASE_URL / AUTH_TYPE / ACTIVE_BACKEND symbols (and per-agent *_MODEL
    overrides that were not set via env), and resets the LLM singleton so
    the next get_llm() call picks up the new adapter.

    The primary usage pattern is: set EPOCH_LLM_BACKEND in .env and start a
    fresh CLI process.  This function is for single-process test loops where
    you want to run the pipeline multiple times against different backends
    without restarting.

    Env-based per-agent model overrides (EPOCH_R1_LLM_MODEL etc.) are NOT
    cleared by this function — they continue to take precedence.  To switch
    all agents to the new backend model, leave those env vars unset.
    """
    global ACTIVE_BACKEND, API_KEY, MODEL, BASE_URL, AUTH_TYPE
    global R1_MODEL, DA1_MODEL, CP1_MODEL, TS1_MODEL, P1_MODEL, F1_MODEL, R2_MODEL, D2_MODEL
    global P2_MODEL, P3_MODEL, S1_MODEL
    global RC1_1_MODEL, RC1_MODEL, RM1_1_MODEL, RM1_MODEL, PERSONA_MODEL, F2_MODEL
    global EX_MODEL
    global C1_MODEL, C2_MODEL

    new_backend = _resolve_backend_from_preset(name)
    ACTIVE_BACKEND = new_backend
    API_KEY = new_backend.api_key
    MODEL = new_backend.model
    BASE_URL = new_backend.base_url
    AUTH_TYPE = new_backend.auth_type

    # Update per-agent model symbols only when they were NOT overridden via env
    if not os.getenv("EPOCH_R1_LLM_MODEL"):
        R1_MODEL = MODEL
    if not os.getenv("EPOCH_DA1_LLM_MODEL"):
        DA1_MODEL = MODEL
    if not os.getenv("EPOCH_CP1_LLM_MODEL"):
        CP1_MODEL = MODEL
    if not os.getenv("EPOCH_TS1_LLM_MODEL"):
        TS1_MODEL = MODEL
    if not os.getenv("EPOCH_P1_LLM_MODEL"):
        P1_MODEL = MODEL
    if not os.getenv("EPOCH_F1_LLM_MODEL"):
        F1_MODEL = MODEL
    if not os.getenv("EPOCH_R2_LLM_MODEL"):
        R2_MODEL = MODEL
    if not os.getenv("EPOCH_D2_LLM_MODEL"):
        D2_MODEL = MODEL
    if not os.getenv("EPOCH_P2_LLM_MODEL"):
        P2_MODEL = MODEL
    if not os.getenv("EPOCH_P3_LLM_MODEL"):
        P3_MODEL = MODEL
    if not os.getenv("EPOCH_S1_LLM_MODEL"):
        S1_MODEL = MODEL
    if not os.getenv("EPOCH_RC1_1_LLM_MODEL"):
        RC1_1_MODEL = MODEL
    if not os.getenv("EPOCH_RC1_LLM_MODEL"):
        RC1_MODEL = MODEL
    if not os.getenv("EPOCH_RM1_1_LLM_MODEL"):
        RM1_1_MODEL = MODEL
    if not os.getenv("EPOCH_RM1_LLM_MODEL"):
        RM1_MODEL = MODEL
    if not os.getenv("EPOCH_PERSONA_LLM_MODEL"):
        PERSONA_MODEL = MODEL
    if not os.getenv("EPOCH_F2_LLM_MODEL"):
        F2_MODEL = MODEL
    if not os.getenv("EPOCH_EX_LLM_MODEL"):
        EX_MODEL = MODEL
    if not os.getenv("EPOCH_C1_LLM_MODEL"):
        C1_MODEL = MODEL
    if not os.getenv("EPOCH_C2_LLM_MODEL"):
        C2_MODEL = MODEL

    # Reset the LLM singleton so get_llm() rebuilds with the new adapter.
    from epoch_switch.core import llm_client as _lc
    _lc.reset_llm()


# ── Paths ─────────────────────────────────────────────────────────────
# Access all data through DATA_DIR; never hardcode absolute data paths.
# experiments/public/ is the local counterpart for document-family runs (UC4
# and later). Runtime directories are gitignored in this public release.
PACKAGE_ROOT = Path(__file__).parent          # epoch_switch/epoch_switch/
PROJECT_ROOT = PACKAGE_ROOT.parent.parent     # repo root

# EPOCH_DATA_MODE selects the data backend: "synthetic" (default, committed
# data/synthetic/) or "real" (your own gitignored data/real/, not shipped in
# this repository). All data access flows through DATA_DIR, so this single
# switch flips the whole pipeline.
DATA_MODE = os.getenv("EPOCH_DATA_MODE", "synthetic").strip().lower()
if DATA_MODE not in ("real", "synthetic"):
    DATA_MODE = "synthetic"

DATA_DIR               = PROJECT_ROOT / "data" / DATA_MODE
EXPERIMENTS_DIR        = PROJECT_ROOT / "experiments" / DATA_MODE
PUBLIC_EXPERIMENTS_DIR = PROJECT_ROOT / "experiments" / "public"
REGULATIONS_DIR = PROJECT_ROOT / "regulations"
# Backward-compatible alias. Tabular runs live directly under experiments/<DATA_MODE>/.
ARTIFACTS_DIR   = EXPERIMENTS_DIR
AGENTS_DIR      = PACKAGE_ROOT / "agents"

EXPERIMENTS_DIR.mkdir(parents=True, exist_ok=True)
PUBLIC_EXPERIMENTS_DIR.mkdir(parents=True, exist_ok=True)


def create_experiment_dir(
    name: str, description: str = "", visibility: str = "real"
) -> Path:
    """Create a unique, dated experiment directory with an initial README.

    `visibility="real"` (default) writes under experiments/<DATA_MODE>/.
    `visibility="public"` writes under local experiments/public/ — use only
    for runs whose inputs are public data, such as document-family use cases.
    """
    root = PUBLIC_EXPERIMENTS_DIR if visibility == "public" else EXPERIMENTS_DIR
    safe_name = "".join(
        character.lower() if character.isalnum() else "_"
        for character in name.strip()
    ).strip("_")
    while "__" in safe_name:
        safe_name = safe_name.replace("__", "_")
    if not safe_name:
        raise ValueError("Experiment name must contain at least one letter or number")

    started_at = datetime.now(timezone.utc)
    stem = f"{started_at:%Y-%m-%d_%H%M%SZ}_{safe_name}"
    run_dir = root / stem
    sequence = 2
    while run_dir.exists():
        run_dir = root / f"{stem}_{sequence:02d}"
        sequence += 1

    run_dir.mkdir(parents=True)
    readme = [
        "# Experiment Run",
        "",
        f"- **Started (UTC):** {started_at.isoformat()}",
        f"- **Data mode:** `{visibility}`",
        f"- **Run name:** `{safe_name}`",
        "- **Status:** Output directory created",
    ]
    if description:
        readme.append(f"- **Purpose:** {description}")
    if visibility == "public":
        readme.append(
            "- **Note:** inputs are public data; this run directory is committed to git."
        )
    readme.extend([
        "",
        "All outputs produced by this run are kept in this directory.",
        "",
    ])
    (run_dir / "README.md").write_text("\n".join(readme), encoding="utf-8")
    return run_dir
