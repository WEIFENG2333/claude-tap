"""CLI clients we know how to launch under the proxy.

A ``Client`` describes *how to start a CLI binary and point it at our
proxy*. It does not own protocol logic — that lives in ``protocols.py``.
A client may use one or several protocols (opencode, for example, can
talk Anthropic, OpenAI, and Gemini in the same session).

Two responsibilities are split for clarity:

* :func:`Client.detect_auth` — *do you have credentials?* — used for UX
  warnings only.
* :func:`Client.read_configured_upstream` — *where does this client send
  its traffic right now?* — used to pick our forward target so we never
  silently overwrite a user-configured ``base_url``.
"""

from __future__ import annotations

import json
import os
import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Literal

from claude_tap.protocols import ANTHROPIC, GEMINI, OPENAI, PASSTHROUGH, Protocol


@dataclass(frozen=True)
class AuthInfo:
    """What we know about the local auth state for a client."""

    logged_in: bool
    mode: str  # 'oauth' | 'apikey' | 'unknown' | 'mixed'
    detail: str = ""
    suggested_target: str | None = None  # only meaningful for single-protocol clients


def _no_overrides(_proxy_url: str) -> dict[str, str]:
    return {}


def _no_cli_args(_proxy_url: str, _env: Mapping[str, str]) -> list[str]:
    return []


def _no_configured_upstream(_env: Mapping[str, str]) -> str | None:
    return None


def _no_auth(*_args, **_kwargs) -> AuthInfo:
    return AuthInfo(logged_in=False, mode="unknown")


@dataclass(frozen=True)
class Client:
    name: str
    cmd: str
    label: str
    install_url: str
    protocols: tuple[Protocol, ...]

    # Reverse-mode redirect via env vars (most clients).
    env_overrides: Callable[[str], dict[str, str]] = field(default=_no_overrides)

    # Reverse-mode redirect via CLI args (codex needs ``-c`` overrides because
    # it ignores ``OPENAI_BASE_URL``). Receives the user's env so the function
    # can look up an active provider from a config file.
    cli_args_overrides: Callable[[str, Mapping[str, str]], list[str]] = field(default=_no_cli_args)

    # CLI args that put this client into "auto-approve every action" / yolo
    # mode. Each CLI uses different wording (``--yolo`` / ``--full-auto`` /
    # ``--dangerously-skip-permissions`` / etc.) — see each client's setup
    # below. claude-tap prepends these to the child argv when yolo is on
    # (default; turned off by ``--no-yolo``). Empty tuple means the CLI has
    # no single-flag yolo path; we'll print a note instead of failing.
    yolo_args: tuple[str, ...] = ()
    yolo_args_position: Literal["prepend", "after-first-arg"] = "prepend"

    # Read the user's configured upstream URL from env / config files. Returns
    # ``None`` if the user has not customized it. Used as the proxy's upstream
    # target when ``--target`` is omitted, so a user's private relay /
    # regional endpoint configured in their CLI's own config is preserved.
    read_configured_upstream: Callable[[Mapping[str, str]], str | None] = field(default=_no_configured_upstream)

    # Whether ``env_overrides`` actually redirects this client's outbound
    # HTTPS. For multi-backend clients (opencode / pi / kimi / iflow /
    # hermes) the user's config-file ``baseURL`` overrides any env we set,
    # so env-based reverse mode silently fails. For those, we default to
    # forward mode (HTTPS_PROXY + CA-MITM) which redirects regardless of
    # config. Single-backend clients honor their env vars reliably.
    env_redirect_reliable: bool = True

    pre_launch_env_purge: tuple[str, ...] = ()
    detect_auth: Callable[[], AuthInfo] = field(default=_no_auth)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _strip_url(value: object) -> str | None:
    """Return ``value`` if it's a non-empty string URL; ``None`` otherwise."""
    if isinstance(value, str):
        v = value.strip().rstrip("/")
        return v or None
    return None


def _read_toml(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        with path.open("rb") as f:
            data = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _read_json(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


# ---------------------------------------------------------------------------
# Claude Code
# ---------------------------------------------------------------------------


def _claude_env(proxy_url: str) -> dict[str, str]:
    return {"ANTHROPIC_BASE_URL": proxy_url}


def _claude_configured(env: Mapping[str, str]) -> str | None:
    return _strip_url(env.get("ANTHROPIC_BASE_URL"))


def _claude_auth() -> AuthInfo:
    if os.environ.get("ANTHROPIC_API_KEY"):
        return AuthInfo(
            logged_in=True,
            mode="apikey",
            detail="ANTHROPIC_API_KEY env var",
            suggested_target=ANTHROPIC.default_target,
        )
    cred = Path.home() / ".claude" / ".credentials.json"
    if cred.is_file():
        try:
            data = json.loads(cred.read_text(encoding="utf-8"))
            oauth = data.get("claudeAiOauth")
            if isinstance(oauth, dict) and oauth.get("accessToken"):
                sub = oauth.get("subscriptionType") or "subscription"
                return AuthInfo(
                    logged_in=True,
                    mode="oauth",
                    detail=f"Claude Code OAuth ({sub})",
                    suggested_target=ANTHROPIC.default_target,
                )
        except (OSError, json.JSONDecodeError):
            pass
    return AuthInfo(
        logged_in=False,
        mode="unknown",
        detail="not logged in (run `claude` and `/login`, or export ANTHROPIC_API_KEY)",
    )


# ---------------------------------------------------------------------------
# Codex CLI — Rust binary, ignores OPENAI_BASE_URL. We must parse
# ~/.codex/config.toml and use ``-c`` CLI overrides to redirect.
# ---------------------------------------------------------------------------


def _codex_configured(env: Mapping[str, str]) -> str | None:
    """``~/.codex/config.toml`` → ``model_providers[<active>].base_url``."""
    cfg = _read_toml(Path.home() / ".codex" / "config.toml")
    if not cfg:
        return None
    active = cfg.get("model_provider")
    if not isinstance(active, str) or not active:
        return None  # built-in 'openai' has a hard-coded base_url; not user-customized
    providers = cfg.get("model_providers")
    if not isinstance(providers, dict):
        return None
    block = providers.get(active)
    if not isinstance(block, dict):
        return None
    return _strip_url(block.get("base_url"))


def _codex_cli_args(proxy_url: str, env: Mapping[str, str]) -> list[str]:
    """Build ``-c`` overrides that send codex's ``/v1/responses`` traffic
    through the proxy *and* force HTTP+SSE instead of the WebSocket
    transport. WS is opaque to per-turn capture: codex keeps one socket
    open for the entire session and uses ``previous_response_id`` so
    successive requests don't re-send conversation history. Forcing HTTP
    gives one trace record per request with the body intact.

    Built-in provider IDs (``openai`` / ``azure`` / ``oss``) are reserved
    and cannot be overridden by a same-named ``[model_providers.X]`` block,
    so for the built-in path we define a sibling custom provider
    (``claude-tap-openai``) that inherits OpenAI auth via
    ``requires_openai_auth = true``.
    """
    cfg = _read_toml(Path.home() / ".codex" / "config.toml") or {}
    active = cfg.get("model_provider") if isinstance(cfg.get("model_provider"), str) else None
    providers = cfg.get("model_providers") if isinstance(cfg.get("model_providers"), dict) else {}

    if active and active in providers:
        return [
            "-c",
            f'model_providers.{active}.base_url="{proxy_url}/v1"',
            "-c",
            f"model_providers.{active}.supports_websockets=false",
        ]

    return [
        "-c",
        'model_provider="claude-tap-openai"',
        "-c",
        'model_providers.claude-tap-openai.name="claude-tap"',
        "-c",
        f'model_providers.claude-tap-openai.base_url="{proxy_url}/v1"',
        "-c",
        'model_providers.claude-tap-openai.wire_api="responses"',
        "-c",
        "model_providers.claude-tap-openai.requires_openai_auth=true",
        "-c",
        "model_providers.claude-tap-openai.supports_websockets=false",
    ]


def _codex_auth() -> AuthInfo:
    """Match codex's own auth-priority: ``~/.codex/auth.json`` wins over
    ``OPENAI_API_KEY`` env. A stale env var must not pull a ChatGPT-OAuth
    user's traffic to ``api.openai.com`` (ChatGPT tokens fail there with
    401 ``Missing scopes: api.responses.write``)."""
    auth = Path.home() / ".codex" / "auth.json"
    if auth.is_file():
        try:
            data = json.loads(auth.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
        mode = data.get("auth_mode") if isinstance(data, dict) else None
        if mode == "chatgpt":
            return AuthInfo(
                logged_in=True,
                mode="oauth",
                detail="Codex ChatGPT OAuth",
                suggested_target="https://chatgpt.com/backend-api/codex",
            )
        if mode == "apikey" and isinstance(data, dict) and data.get("OPENAI_API_KEY"):
            return AuthInfo(
                logged_in=True,
                mode="apikey",
                detail="Codex stored API key",
                suggested_target="https://api.openai.com",
            )
    if os.environ.get("OPENAI_API_KEY"):
        return AuthInfo(
            logged_in=True,
            mode="apikey",
            detail="OPENAI_API_KEY env var",
            suggested_target="https://api.openai.com",
        )
    return AuthInfo(
        logged_in=False,
        mode="unknown",
        detail="not logged in (run `codex login`, or export OPENAI_API_KEY)",
    )


# ---------------------------------------------------------------------------
# Gemini CLI — env-only base URL. No config-file field for it.
# ---------------------------------------------------------------------------


def _gemini_env(proxy_url: str) -> dict[str, str]:
    return {"GOOGLE_GEMINI_BASE_URL": proxy_url}


def _gemini_configured(env: Mapping[str, str]) -> str | None:
    for key in ("GOOGLE_GEMINI_BASE_URL", "GOOGLE_VERTEX_BASE_URL", "CODE_ASSIST_ENDPOINT"):
        url = _strip_url(env.get(key))
        if url:
            return url
    return None


def _gemini_auth() -> AuthInfo:
    for var in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
        if os.environ.get(var):
            return AuthInfo(
                logged_in=True,
                mode="apikey",
                detail=f"{var} env var",
                suggested_target=GEMINI.default_target,
            )
    creds = Path.home() / ".gemini" / "oauth_creds.json"
    if creds.is_file():
        return AuthInfo(
            logged_in=True,
            mode="oauth",
            detail="Gemini CLI OAuth",
            suggested_target=GEMINI.default_target,
        )
    return AuthInfo(
        logged_in=False,
        mode="unknown",
        detail="not logged in (run `gemini` and pick Login with Google, or export GEMINI_API_KEY)",
    )


# ---------------------------------------------------------------------------
# OpenCode — JSON config with provider blocks; multi-backend.
# ---------------------------------------------------------------------------


def _opencode_env(proxy_url: str) -> dict[str, str]:
    """Reverse-mode env: redirect every backend opencode might choose."""
    return {
        "ANTHROPIC_BASE_URL": f"{proxy_url}/v1",
        "OPENAI_BASE_URL": f"{proxy_url}/v1",
        "GOOGLE_GEMINI_BASE_URL": proxy_url,
    }


def _opencode_configured(env: Mapping[str, str]) -> str | None:
    """Read ``provider.<active>.options.baseURL`` where ``<active>`` is the
    first segment of top-level ``model: "<active>/<model_id>"``.

    Returns ``None`` if no custom provider is configured (falls back to
    forward mode for true multi-backend transparency)."""
    cfg_path = env.get("OPENCODE_CONFIG") or str(Path.home() / ".config" / "opencode" / "opencode.json")
    cfg = _read_json(Path(cfg_path).expanduser())
    if not cfg:
        return None
    model = cfg.get("model")
    if not isinstance(model, str) or "/" not in model:
        return None
    provider_id = model.split("/", 1)[0]
    providers = cfg.get("provider")
    if not isinstance(providers, dict):
        return None
    block = providers.get(provider_id)
    if not isinstance(block, dict):
        return None
    options = block.get("options")
    if not isinstance(options, dict):
        return None
    base = options.get("baseURL")
    if isinstance(base, str) and base.startswith("{env:") and base.endswith("}"):
        var = base[5:-1]
        return _strip_url(env.get(var))
    return _strip_url(base)


def _opencode_auth() -> AuthInfo:
    auth = Path.home() / ".local" / "share" / "opencode" / "auth.json"
    if auth.is_file():
        try:
            data = json.loads(auth.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data:
                names = ", ".join(sorted(data.keys()))[:80]
                return AuthInfo(
                    logged_in=True,
                    mode="mixed",
                    detail=f"opencode credentials: {names}",
                    suggested_target=ANTHROPIC.default_target,
                )
        except (OSError, json.JSONDecodeError):
            pass
    for var, target in (
        ("ANTHROPIC_API_KEY", ANTHROPIC.default_target),
        ("OPENAI_API_KEY", "https://api.openai.com"),
        ("GEMINI_API_KEY", GEMINI.default_target),
    ):
        if os.environ.get(var):
            return AuthInfo(
                logged_in=True,
                mode="apikey",
                detail=f"{var} env var",
                suggested_target=target,
            )
    return AuthInfo(
        logged_in=False,
        mode="unknown",
        detail="not logged in (run `opencode providers login`, or export *_API_KEY)",
    )


# ---------------------------------------------------------------------------
# Pi (badlogic/pi-coding-agent) — JSON config under ~/.pi/agent.
# ---------------------------------------------------------------------------


def _pi_agent_dir(env: Mapping[str, str]) -> Path:
    custom = env.get("PI_CODING_AGENT_DIR")
    if custom:
        return Path(custom).expanduser()
    return Path.home() / ".pi" / "agent"


def _pi_configured(env: Mapping[str, str]) -> str | None:
    """Active provider's ``baseUrl``. Active provider is picked from
    ``settings.json: defaultProvider`` if present, else the first key in
    ``models.json: providers``."""
    base = _pi_agent_dir(env)
    models = _read_json(base / "models.json")
    if not models:
        return None
    providers = models.get("providers")
    if not isinstance(providers, dict) or not providers:
        return None

    settings = _read_json(base / "settings.json") or {}
    active = settings.get("defaultProvider") or settings.get("provider")
    if not isinstance(active, str) or active not in providers:
        active = next(iter(providers))

    block = providers[active]
    if not isinstance(block, dict):
        return None
    return _strip_url(block.get("baseUrl"))


def _pi_auth() -> AuthInfo:
    for var, target in (
        ("ANTHROPIC_API_KEY", ANTHROPIC.default_target),
        ("OPENAI_API_KEY", OPENAI.default_target),
    ):
        if os.environ.get(var):
            return AuthInfo(
                logged_in=True,
                mode="apikey",
                detail=f"{var} env var",
                suggested_target=target,
            )
    return AuthInfo(
        logged_in=False,
        mode="unknown",
        detail="not logged in (export ANTHROPIC_API_KEY or OPENAI_API_KEY)",
    )


# ---------------------------------------------------------------------------
# Kimi CLI — TOML config; ``default_model`` → ``models.<m>.provider`` →
# ``providers.<p>.base_url``.
# ---------------------------------------------------------------------------


def _kimi_configured(env: Mapping[str, str]) -> str | None:
    share = env.get("KIMI_SHARE_DIR")
    base_dir = Path(share).expanduser() if share else Path.home() / ".kimi"
    cfg = _read_toml(base_dir / "config.toml")
    if not cfg:
        return None
    default_model = cfg.get("default_model")
    if not isinstance(default_model, str):
        return None
    models = cfg.get("models") if isinstance(cfg.get("models"), dict) else {}
    providers = cfg.get("providers") if isinstance(cfg.get("providers"), dict) else {}
    model_block = models.get(default_model) if isinstance(models, dict) else None
    if not isinstance(model_block, dict):
        return None
    provider_id = model_block.get("provider")
    if not isinstance(provider_id, str):
        return None
    p_block = providers.get(provider_id) if isinstance(providers, dict) else None
    if not isinstance(p_block, dict):
        return None
    return _strip_url(p_block.get("base_url"))


def _kimi_auth() -> AuthInfo:
    for var in ("MOONSHOT_API_KEY", "KIMI_API_KEY", "OPENAI_API_KEY"):
        if os.environ.get(var):
            return AuthInfo(
                logged_in=True,
                mode="apikey",
                detail=f"{var} env var",
                suggested_target="https://api.moonshot.ai",
            )
    return AuthInfo(
        logged_in=False,
        mode="unknown",
        detail="not logged in (export MOONSHOT_API_KEY or run `kimi login`)",
    )


# ---------------------------------------------------------------------------
# iFlow CLI — settings.json wins over env (verified in source).
# ---------------------------------------------------------------------------


def _iflow_configured(env: Mapping[str, str]) -> str | None:
    settings = _read_json(Path.home() / ".iflow" / "settings.json")
    if settings:
        url = _strip_url(settings.get("baseUrl"))
        if url:
            return url
    for key in ("IFLOW_BASE_URL", "IFLOW_BASEURL", "IFLOW_baseUrl", "IFLOW_URL"):
        url = _strip_url(env.get(key))
        if url:
            return url
    return None


def _iflow_auth() -> AuthInfo:
    if os.environ.get("IFLOW_API_KEY"):
        return AuthInfo(
            logged_in=True,
            mode="apikey",
            detail="IFLOW_API_KEY env var",
            suggested_target="https://apis.iflow.cn",
        )
    if os.environ.get("OPENAI_API_KEY"):
        return AuthInfo(
            logged_in=True,
            mode="apikey",
            detail="OPENAI_API_KEY env var (fallback)",
            suggested_target="https://apis.iflow.cn",
        )
    settings = Path.home() / ".iflow" / "settings.json"
    if settings.is_file():
        return AuthInfo(
            logged_in=True,
            mode="apikey",
            detail="iFlow settings.json",
            suggested_target="https://apis.iflow.cn",
        )
    return AuthInfo(
        logged_in=False,
        mode="unknown",
        detail="not logged in (export IFLOW_API_KEY)",
    )


# ---------------------------------------------------------------------------
# Cursor / Qoder / Devin — proprietary clients.
# ---------------------------------------------------------------------------


def _cursor_env(proxy_url: str) -> dict[str, str]:
    return {"CURSOR_API_BASE_URL": proxy_url}


def _cursor_configured(env: Mapping[str, str]) -> str | None:
    return _strip_url(env.get("CURSOR_API_BASE_URL"))


def _cursor_auth() -> AuthInfo:
    if os.environ.get("CURSOR_API_KEY"):
        return AuthInfo(
            logged_in=True,
            mode="apikey",
            detail="CURSOR_API_KEY env var",
            suggested_target="https://api2.cursor.sh",
        )
    return AuthInfo(
        logged_in=False,
        mode="unknown",
        detail="not logged in (run `cursor-agent login` or export CURSOR_API_KEY)",
        suggested_target="https://api2.cursor.sh",
    )


def _qoder_env(proxy_url: str) -> dict[str, str]:
    no_scheme = proxy_url.split("://", 1)[1] if "://" in proxy_url else proxy_url
    return {"QODER_CENTER_DOMAIN": no_scheme}


def _qoder_configured(env: Mapping[str, str]) -> str | None:
    raw = env.get("QODER_CENTER_DOMAIN")
    if not raw:
        return None
    return raw if "://" in raw else f"https://{raw}"


def _qoder_auth() -> AuthInfo:
    if os.environ.get("QODER_ACCESS_TOKEN"):
        return AuthInfo(
            logged_in=True,
            mode="apikey",
            detail="QODER_ACCESS_TOKEN env var",
            suggested_target="https://qoder.com",
        )
    return AuthInfo(
        logged_in=False,
        mode="unknown",
        detail="not logged in (run `qodercli login` or export QODER_ACCESS_TOKEN)",
        suggested_target="https://qoder.com",
    )


def _devin_auth() -> AuthInfo:
    if os.environ.get("DEVIN_API_TOKEN"):
        return AuthInfo(
            logged_in=True,
            mode="apikey",
            detail="DEVIN_API_TOKEN env var",
            suggested_target="https://api.devin.ai",
        )
    return AuthInfo(
        logged_in=False,
        mode="unknown",
        detail="not logged in (run `devin configure` first; rustls means forward mode needs OS-level CA trust)",
        suggested_target="https://api.devin.ai",
    )


# ---------------------------------------------------------------------------
# Hermes — YAML config with per-provider env overrides.
# ---------------------------------------------------------------------------


# Minimal per-provider env map (only the providers users actually deploy).
# Source: NousResearch/hermes-agent runtime_provider.py registry.
_HERMES_PROVIDER_ENV: dict[str, str] = {
    "openrouter": "OPENROUTER_BASE_URL",
    "kimi": "KIMI_BASE_URL",
    "glm": "GLM_BASE_URL",
    "deepseek": "DEEPSEEK_BASE_URL",
    "stepfun": "STEPFUN_BASE_URL",
    "minimax": "MINIMAX_BASE_URL",
    "minimax-cn": "MINIMAX_CN_BASE_URL",
    "dashscope": "DASHSCOPE_BASE_URL",
    "lm": "LM_BASE_URL",
    "qwen": "HERMES_QWEN_BASE_URL",
    "openai-codex": "HERMES_CODEX_BASE_URL",
    "opencode-zen": "OPENCODE_ZEN_BASE_URL",
    "opencode-go": "OPENCODE_GO_BASE_URL",
    "kilocode": "KILOCODE_BASE_URL",
    "hf": "HF_BASE_URL",
    "copilot-acp": "COPILOT_ACP_BASE_URL",
    "custom": "CUSTOM_BASE_URL",
    "auto": "CUSTOM_BASE_URL",
}


def _read_hermes_top_model_block(text: str) -> dict[str, str]:
    """Cheap top-level YAML parser for ``model:`` block — only handles
    ``key: value`` pairs (no nesting). Matches what every hermes config in
    the wild uses."""
    out: dict[str, str] = {}
    in_model = False
    pair_re = re.compile(r"^\s+([a-z_]+)\s*:\s*(.*?)\s*$")
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line or line.lstrip().startswith("#"):
            continue
        if line == "model:":
            in_model = True
            continue
        if in_model:
            if line[:1] not in (" ", "\t"):
                break  # left the block
            m = pair_re.match(line)
            if m:
                out[m.group(1)] = m.group(2).strip().strip("'\"")
    return out


def _hermes_configured(env: Mapping[str, str]) -> str | None:
    cfg_path = Path.home() / ".hermes" / "config.yaml"
    if not cfg_path.is_file():
        return None
    try:
        text = cfg_path.read_text(encoding="utf-8")
    except OSError:
        return None
    block = _read_hermes_top_model_block(text)
    provider = block.get("provider")
    config_url = _strip_url(block.get("base_url"))

    # Hermes' own resolution: per-provider env wins for known providers
    # (matches runtime_provider.py:592). For ``provider: custom`` / unknown,
    # the config file's base_url is the only source.
    if isinstance(provider, str) and provider in _HERMES_PROVIDER_ENV:
        env_url = _strip_url(env.get(_HERMES_PROVIDER_ENV[provider]))
        if env_url:
            return env_url
    return config_url


def _hermes_env(proxy_url: str) -> dict[str, str]:
    """Reverse-mode env override. Set the standard provider envs *plus* the
    per-provider env vars hermes recognizes — hermes' resolution chain
    prefers the provider-specific one."""
    return {
        "OPENAI_BASE_URL": f"{proxy_url}/v1",
        "ANTHROPIC_BASE_URL": proxy_url,
        "GOOGLE_GEMINI_BASE_URL": proxy_url,
        "OPENROUTER_BASE_URL": f"{proxy_url}/v1",
        "CUSTOM_BASE_URL": f"{proxy_url}/v1",
    }


def _hermes_auth() -> AuthInfo:
    for var, target in (
        ("ANTHROPIC_API_KEY", ANTHROPIC.default_target),
        ("OPENAI_API_KEY", OPENAI.default_target),
        ("OPENROUTER_API_KEY", "https://openrouter.ai/api/v1"),
        ("NOUS_API_KEY", "https://inference-api.nousresearch.com/v1"),
    ):
        if os.environ.get(var):
            return AuthInfo(
                logged_in=True,
                mode="apikey",
                detail=f"{var} env var",
                suggested_target=target,
            )
    auth = Path.home() / ".hermes" / "auth.json"
    if auth.is_file():
        return AuthInfo(
            logged_in=True,
            mode="oauth",
            detail="Hermes stored credentials",
            suggested_target=ANTHROPIC.default_target,
        )
    return AuthInfo(
        logged_in=False,
        mode="unknown",
        detail="not logged in (run `hermes login` or export an *_API_KEY)",
    )


# ---------------------------------------------------------------------------
# Concrete clients
# ---------------------------------------------------------------------------


CLAUDE = Client(
    name="claude",
    cmd="claude",
    label="Claude Code",
    install_url="https://docs.anthropic.com/en/docs/claude-code",
    protocols=(ANTHROPIC,),
    env_overrides=_claude_env,
    read_configured_upstream=_claude_configured,
    pre_launch_env_purge=("CLAUDECODE", "CLAUDE_CODE_SSE_PORT"),
    detect_auth=_claude_auth,
    yolo_args=("--dangerously-skip-permissions",),
)


CODEX = Client(
    name="codex",
    cmd="codex",
    label="Codex CLI",
    install_url="https://github.com/openai/codex",
    protocols=(OPENAI,),
    # Codex ignores OPENAI_BASE_URL — env overrides do nothing. We redirect
    # entirely via ``-c model_providers.<active>.base_url=…`` CLI args.
    cli_args_overrides=_codex_cli_args,
    read_configured_upstream=_codex_configured,
    detect_auth=_codex_auth,
    # ``--full-auto`` sandboxes the workspace and auto-approves inside it;
    # the no-sandbox flavour is ``--dangerously-bypass-approvals-and-sandbox``
    # but that's a sharper edge than this default warrants.
    yolo_args=("--full-auto",),
)


GEMINI_CLI = Client(
    name="gemini",
    cmd="gemini",
    label="Gemini CLI",
    install_url="https://github.com/google-gemini/gemini-cli",
    protocols=(GEMINI,),
    env_overrides=_gemini_env,
    read_configured_upstream=_gemini_configured,
    detect_auth=_gemini_auth,
    yolo_args=("--yolo",),
)


# ---------------------------------------------------------------------------
# Multi-backend clients (opencode / Pi / Kimi / iFlow / Hermes).
#
# Each of these reads its upstream ``baseURL`` from its own config file at
# runtime, in a way that overrides any env var we set. Reverse mode would
# silently capture nothing, so they all carry ``env_redirect_reliable=False``
# and the CLI defaults them to forward mode (HTTPS_PROXY + CA-MITM).
# ``read_configured_upstream`` is still used for the "[client] target: ..."
# UI line and for the trace's ``upstream_base_url`` field.
# ---------------------------------------------------------------------------


OPENCODE = Client(
    name="opencode",
    cmd="opencode",
    label="opencode",
    install_url="https://opencode.ai",
    protocols=(ANTHROPIC, OPENAI, GEMINI, PASSTHROUGH),
    env_overrides=_opencode_env,
    read_configured_upstream=_opencode_configured,
    env_redirect_reliable=False,
    detect_auth=_opencode_auth,
    # opencode's run subcommand has --dangerously-skip-permissions; the
    # top-level (interactive TUI) honours the same flag.
    yolo_args=("--dangerously-skip-permissions",),
    yolo_args_position="after-first-arg",
)


PI = Client(
    name="pi",
    cmd="pi",
    label="Pi (pi-coding-agent)",
    install_url="https://github.com/badlogic/pi-mono",
    protocols=(ANTHROPIC, OPENAI, GEMINI, PASSTHROUGH),
    read_configured_upstream=_pi_configured,
    env_redirect_reliable=False,
    detect_auth=_pi_auth,
    # Pi has no single-flag yolo. It uses ``--tools <allowlist>`` for
    # gating; without an explicit allowlist the user is asked per-tool.
    # We leave yolo_args empty and the CLI prints a "not supported" note.
)


KIMI = Client(
    name="kimi",
    cmd="kimi",
    label="Kimi Code CLI",
    install_url="https://github.com/MoonshotAI/kimi-cli",
    protocols=(ANTHROPIC, OPENAI, GEMINI, PASSTHROUGH),
    read_configured_upstream=_kimi_configured,
    env_redirect_reliable=False,
    detect_auth=_kimi_auth,
    yolo_args=("--yolo",),
)


IFLOW = Client(
    name="iflow",
    cmd="iflow",
    label="iFlow CLI",
    install_url="https://github.com/iflow-ai/iflow-cli",
    protocols=(ANTHROPIC, OPENAI, GEMINI, PASSTHROUGH),
    read_configured_upstream=_iflow_configured,
    env_redirect_reliable=False,
    detect_auth=_iflow_auth,
    yolo_args=("--yolo",),
)


CURSOR = Client(
    name="cursor",
    cmd="cursor-agent",
    label="Cursor Agent",
    install_url="https://cursor.com/cli",
    protocols=(PASSTHROUGH,),
    env_overrides=_cursor_env,
    read_configured_upstream=_cursor_configured,
    detect_auth=_cursor_auth,
    # ``--yolo`` is documented as an alias of ``--force`` ("Run Everything").
    yolo_args=("--yolo",),
)


QODER = Client(
    name="qoder",
    cmd="qodercli",
    label="Qoder CLI",
    install_url="https://qoder.com/cli",
    protocols=(PASSTHROUGH,),
    env_overrides=_qoder_env,
    read_configured_upstream=_qoder_configured,
    detect_auth=_qoder_auth,
    yolo_args=("--yolo",),
)


DEVIN = Client(
    name="devin",
    cmd="devin",
    label="Devin CLI",
    install_url="https://docs.devin.ai/get-started/devin-intro",
    protocols=(PASSTHROUGH,),
    # Rust binary using rustls — no env-based redirect works. Forward mode +
    # OS-level CA install is the only path.
    env_redirect_reliable=False,
    detect_auth=_devin_auth,
    # Devin uses ``--permission-mode <mode>`` with values "auto" (read-only
    # auto-approve) and "dangerous" (full auto-approve).
    yolo_args=("--permission-mode", "dangerous"),
)


HERMES = Client(
    name="hermes",
    cmd="hermes",
    label="Hermes Agent",
    install_url="https://github.com/NousResearch/hermes-agent",
    protocols=(ANTHROPIC, OPENAI, GEMINI, PASSTHROUGH),
    env_overrides=_hermes_env,
    read_configured_upstream=_hermes_configured,
    env_redirect_reliable=False,
    detect_auth=_hermes_auth,
    yolo_args=("--yolo",),
)


_REGISTRY: dict[str, Client] = {
    c.name: c for c in (CLAUDE, CODEX, GEMINI_CLI, OPENCODE, PI, KIMI, IFLOW, CURSOR, QODER, DEVIN, HERMES)
}


def get(name: str) -> Client:
    if name not in _REGISTRY:
        raise KeyError(f"unknown client: {name!r} (known: {sorted(_REGISTRY)})")
    return _REGISTRY[name]


def names() -> list[str]:
    return sorted(_REGISTRY)


def is_multi_backend(client: Client) -> bool:
    """A client is multi-backend if it can speak more than one wire protocol
    (ignoring the catch-all PASSTHROUGH). When such a client has no
    discoverable single upstream, the CLI falls back to forward mode."""
    return len([p for p in client.protocols if p is not PASSTHROUGH]) >= 2
