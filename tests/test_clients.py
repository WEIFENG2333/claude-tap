"""Client layer: registry, env_overrides, configured-upstream readers."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from claude_tap import clients


@pytest.fixture
def fake_home(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setenv("HOME", str(tmp_path))
    for v in (
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "ANTHROPIC_BASE_URL",
        "OPENAI_BASE_URL",
        "GOOGLE_GEMINI_BASE_URL",
        "GOOGLE_VERTEX_BASE_URL",
        "CODE_ASSIST_ENDPOINT",
        "CURSOR_API_BASE_URL",
        "QODER_CENTER_DOMAIN",
        "OPENROUTER_BASE_URL",
        "CUSTOM_BASE_URL",
        "IFLOW_BASE_URL",
        "PI_CODING_AGENT_DIR",
        "OPENCODE_CONFIG",
    ):
        monkeypatch.delenv(v, raising=False)
    return tmp_path


# --- registry -------------------------------------------------------------


def test_registry_lists_known_clients():
    assert sorted(clients.names()) == [
        "claude",
        "codex",
        "cursor",
        "devin",
        "gemini",
        "hermes",
        "iflow",
        "kimi",
        "opencode",
        "pi",
        "qoder",
    ]


def test_registry_get_unknown_raises():
    with pytest.raises(KeyError):
        clients.get("does-not-exist")


# --- env_overrides --------------------------------------------------------


def test_claude_env_redirects_anthropic_base_url():
    env = clients.get("claude").env_overrides("http://127.0.0.1:8080")
    assert env == {"ANTHROPIC_BASE_URL": "http://127.0.0.1:8080"}


def test_gemini_env_uses_google_var():
    env = clients.get("gemini").env_overrides("http://127.0.0.1:8080")
    assert env == {"GOOGLE_GEMINI_BASE_URL": "http://127.0.0.1:8080"}


def test_codex_has_no_env_overrides_only_cli_args():
    """Codex ignores OPENAI_BASE_URL — env is the wrong knob; CLI ``-c`` is."""
    assert clients.get("codex").env_overrides("http://127.0.0.1:8080") == {}


def test_codex_cli_args_define_sibling_provider_for_builtin(fake_home: Path):
    """Built-in ``openai`` provider has ``supports_websockets = true``
    hard-coded and can't be overridden by name. We define a sibling
    provider so we can disable WS (which is opaque to per-turn tracing
    because codex keeps one socket open across the session)."""
    args = clients.get("codex").cli_args_overrides("http://127.0.0.1:9000", {})
    assert args == [
        "-c",
        'model_provider="claude-tap-openai"',
        "-c",
        'model_providers.claude-tap-openai.name="claude-tap"',
        "-c",
        'model_providers.claude-tap-openai.base_url="http://127.0.0.1:9000/v1"',
        "-c",
        'model_providers.claude-tap-openai.wire_api="responses"',
        "-c",
        "model_providers.claude-tap-openai.requires_openai_auth=true",
        "-c",
        "model_providers.claude-tap-openai.supports_websockets=false",
    ]


def test_codex_cli_args_extends_user_provider_with_ws_disable(fake_home: Path):
    """User's custom provider keeps its ``name`` / ``wire_api`` etc. We
    only swap the URL and force ``supports_websockets = false`` for clean
    per-turn traces."""
    cfg = fake_home / ".codex" / "config.toml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text(
        'model_provider = "my-relay"\n'
        "[model_providers.my-relay]\n"
        'name = "My Relay"\n'
        'base_url = "https://relay.example.com/v1"\n'
        'wire_api = "chat_completions"\n',
        encoding="utf-8",
    )
    args = clients.get("codex").cli_args_overrides("http://127.0.0.1:9000", {})
    assert args == [
        "-c",
        'model_providers.my-relay.base_url="http://127.0.0.1:9000/v1"',
        "-c",
        "model_providers.my-relay.supports_websockets=false",
    ]


def test_opencode_env_redirects_all_three_backends():
    env = clients.get("opencode").env_overrides("http://127.0.0.1:8080")
    assert env["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:8080/v1"
    assert env["OPENAI_BASE_URL"] == "http://127.0.0.1:8080/v1"
    assert env["GOOGLE_GEMINI_BASE_URL"] == "http://127.0.0.1:8080"


# --- protocols mapping ----------------------------------------------------


def test_single_protocol_clients():
    assert [p.name for p in clients.get("claude").protocols] == ["anthropic"]
    assert [p.name for p in clients.get("codex").protocols] == ["openai"]
    assert [p.name for p in clients.get("gemini").protocols] == ["gemini"]


def test_multi_backend_clients_advertise_three_protocols():
    for name in ("opencode", "pi", "kimi", "iflow", "hermes"):
        names = sorted(p.name for p in clients.get(name).protocols)
        assert {"anthropic", "openai", "gemini"}.issubset(set(names)), name


def test_yolo_args_match_each_cli_published_flag():
    # Pinning each client's yolo invocation here so a future "let's update
    # the wording" PR has to update the test too — these flags are taken
    # from each CLI's own --help output, not invented.
    expected = {
        "claude": ("--dangerously-skip-permissions",),
        "codex": ("--full-auto",),
        "gemini": ("--yolo",),
        "opencode": ("--dangerously-skip-permissions",),
        "kimi": ("--yolo",),
        "iflow": ("--yolo",),
        "cursor": ("--yolo",),
        "qoder": ("--yolo",),
        "hermes": ("--yolo",),
        "devin": ("--permission-mode", "dangerous"),
        "pi": (),  # no single-flag yolo; runner prints a note instead
    }
    for name, want in expected.items():
        assert clients.get(name).yolo_args == want, name


def test_proprietary_clients_use_passthrough_protocol():
    from claude_tap.protocols import PASSTHROUGH

    for name in ("cursor", "qoder", "devin"):
        assert clients.get(name).protocols == (PASSTHROUGH,)


def test_passthrough_protocol_accepts_any_path():
    from claude_tap.protocols import PASSTHROUGH

    assert PASSTHROUGH.matches("/anything")
    assert PASSTHROUGH.matches("/api/agent/random")
    assert PASSTHROUGH.matches("/")


# --- read_configured_upstream — the transparency contract -----------------
#
# Each test feeds in a synthetic config and proves we extract the *real*
# upstream the user has set (so we can use it as our forward target rather
# than silently overwriting it with a hard-coded default).


def test_claude_configured_reads_anthropic_base_url():
    env = {"ANTHROPIC_BASE_URL": "https://my-relay.example.com"}
    assert clients.get("claude").read_configured_upstream(env) == "https://my-relay.example.com"


def test_claude_configured_returns_none_when_unset():
    assert clients.get("claude").read_configured_upstream({}) is None


def test_codex_configured_reads_active_provider_base_url(fake_home: Path):
    cfg = fake_home / ".codex" / "config.toml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text(
        'model_provider = "my-relay"\n'
        "[model_providers.my-relay]\n"
        'base_url = "https://relay.example.com/v1"\n'
        'wire_api = "responses"\n',
        encoding="utf-8",
    )
    assert clients.get("codex").read_configured_upstream({}) == "https://relay.example.com/v1"


def test_codex_configured_returns_none_when_using_builtin_openai(fake_home: Path):
    """Built-in 'openai' provider has a hard-coded base_url — not a custom
    user choice, so we let auth.suggested_target take over."""
    cfg = fake_home / ".codex" / "config.toml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text('model = "gpt-5.4"\n', encoding="utf-8")
    assert clients.get("codex").read_configured_upstream({}) is None


def test_codex_configured_handles_missing_config(fake_home: Path):
    assert clients.get("codex").read_configured_upstream({}) is None


def test_codex_configured_handles_corrupt_toml(fake_home: Path):
    cfg = fake_home / ".codex" / "config.toml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text("not toml {{{", encoding="utf-8")
    assert clients.get("codex").read_configured_upstream({}) is None


def test_gemini_configured_reads_each_env_in_priority_order():
    g = clients.get("gemini")
    assert g.read_configured_upstream({"GOOGLE_GEMINI_BASE_URL": "https://a"}) == "https://a"
    # Vertex env is honored when GOOGLE_GEMINI_BASE_URL is missing.
    assert g.read_configured_upstream({"GOOGLE_VERTEX_BASE_URL": "https://v"}) == "https://v"
    # CODE_ASSIST_ENDPOINT for OAuth/Code Assist mode.
    assert g.read_configured_upstream({"CODE_ASSIST_ENDPOINT": "https://c"}) == "https://c"
    # GOOGLE_GEMINI_BASE_URL wins over the others.
    env = {"GOOGLE_GEMINI_BASE_URL": "https://a", "GOOGLE_VERTEX_BASE_URL": "https://v"}
    assert g.read_configured_upstream(env) == "https://a"


def test_gemini_configured_returns_none_when_no_env():
    assert clients.get("gemini").read_configured_upstream({}) is None


def test_kimi_configured_resolves_default_model_to_provider_base_url(fake_home: Path):
    cfg = fake_home / ".kimi" / "config.toml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text(
        'default_model = "kimi-k2-thinking"\n'
        "\n"
        "[providers.moonshot]\n"
        'type = "openai_responses"\n'
        'base_url = "https://api.moonshot.cn/v1"\n'
        'api_key = "sk-..."\n'
        "\n"
        "[models.kimi-k2-thinking]\n"
        'provider = "moonshot"\n'
        'model = "kimi-k2-thinking"\n',
        encoding="utf-8",
    )
    assert clients.get("kimi").read_configured_upstream({}) == "https://api.moonshot.cn/v1"


def test_kimi_configured_handles_missing_config(fake_home: Path):
    """Brand-new install (kimi never run) has no config.toml yet."""
    assert clients.get("kimi").read_configured_upstream({}) is None


def test_iflow_configured_settings_wins_over_env(fake_home: Path):
    """Verified in iflow source: ``r?.baseUrl || f2e()`` — settings.json
    takes priority over IFLOW_BASE_URL env."""
    settings = fake_home / ".iflow" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(json.dumps({"baseUrl": "https://settings.example.com"}), encoding="utf-8")
    env = {"IFLOW_BASE_URL": "https://env.example.com"}
    assert clients.get("iflow").read_configured_upstream(env) == "https://settings.example.com"


def test_iflow_configured_falls_back_to_env_when_settings_has_no_baseurl(fake_home: Path):
    settings = fake_home / ".iflow" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(json.dumps({"cna": "abc"}), encoding="utf-8")
    env = {"IFLOW_BASE_URL": "https://env.example.com"}
    assert clients.get("iflow").read_configured_upstream(env) == "https://env.example.com"


def test_pi_configured_picks_default_provider_base_url(fake_home: Path):
    base = fake_home / ".pi" / "agent"
    base.mkdir(parents=True)
    (base / "models.json").write_text(
        json.dumps(
            {
                "providers": {
                    "anthropic": {"baseUrl": "https://api.anthropic.com"},
                    "myrelay": {"baseUrl": "https://relay.example.com"},
                }
            }
        ),
        encoding="utf-8",
    )
    (base / "settings.json").write_text(json.dumps({"defaultProvider": "myrelay"}), encoding="utf-8")
    assert clients.get("pi").read_configured_upstream({}) == "https://relay.example.com"


def test_pi_configured_falls_back_to_first_provider(fake_home: Path):
    base = fake_home / ".pi" / "agent"
    base.mkdir(parents=True)
    (base / "models.json").write_text(
        json.dumps({"providers": {"anthropic": {"baseUrl": "https://api.anthropic.com"}}}),
        encoding="utf-8",
    )
    assert clients.get("pi").read_configured_upstream({}) == "https://api.anthropic.com"


def test_pi_configured_handles_no_config(fake_home: Path):
    assert clients.get("pi").read_configured_upstream({}) is None


def test_opencode_configured_reads_active_provider_baseurl(fake_home: Path):
    cfg = fake_home / ".config" / "opencode" / "opencode.json"
    cfg.parent.mkdir(parents=True)
    cfg.write_text(
        json.dumps(
            {
                "model": "myrelay/claude-opus-4",
                "provider": {
                    "myrelay": {
                        "options": {"baseURL": "https://relay.example.com"},
                    },
                    "openai": {"options": {"baseURL": "https://api.openai.com/v1"}},
                },
            }
        ),
        encoding="utf-8",
    )
    assert clients.get("opencode").read_configured_upstream({}) == "https://relay.example.com"


def test_opencode_configured_resolves_env_interpolation(fake_home: Path):
    cfg = fake_home / ".config" / "opencode" / "opencode.json"
    cfg.parent.mkdir(parents=True)
    cfg.write_text(
        json.dumps(
            {
                "model": "myrelay/x",
                "provider": {"myrelay": {"options": {"baseURL": "{env:MY_RELAY_URL}"}}},
            }
        ),
        encoding="utf-8",
    )
    env = {"MY_RELAY_URL": "https://from-env.example.com"}
    assert clients.get("opencode").read_configured_upstream(env) == "https://from-env.example.com"


def test_opencode_configured_returns_none_without_explicit_model(fake_home: Path):
    cfg = fake_home / ".config" / "opencode" / "opencode.json"
    cfg.parent.mkdir(parents=True)
    cfg.write_text(json.dumps({"provider": {"openai": {}}}), encoding="utf-8")
    assert clients.get("opencode").read_configured_upstream({}) is None


def test_hermes_configured_provider_env_wins_for_openrouter(fake_home: Path):
    """Verified in hermes runtime_provider.py:592."""
    cfg = fake_home / ".hermes" / "config.yaml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text(
        "model:\n"
        "  default: anthropic/claude-opus-4-5\n"
        "  provider: openrouter\n"
        "  base_url: https://config-says.example.com\n",
        encoding="utf-8",
    )
    env = {"OPENROUTER_BASE_URL": "https://env-wins.example.com"}
    assert clients.get("hermes").read_configured_upstream(env) == "https://env-wins.example.com"


def test_hermes_configured_config_wins_for_custom_provider(fake_home: Path):
    cfg = fake_home / ".hermes" / "config.yaml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text(
        "model:\n  default: my-model\n  provider: custom\n  base_url: https://my-relay.example.com\n",
        encoding="utf-8",
    )
    assert clients.get("hermes").read_configured_upstream({}) == "https://my-relay.example.com"


def test_hermes_configured_returns_none_for_empty_base_url(fake_home: Path):
    cfg = fake_home / ".hermes" / "config.yaml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text("model:\n  provider: anthropic\n  base_url: ''\n", encoding="utf-8")
    assert clients.get("hermes").read_configured_upstream({}) is None


def test_cursor_configured_reads_env():
    assert clients.get("cursor").read_configured_upstream({"CURSOR_API_BASE_URL": "https://x"}) == "https://x"
    assert clients.get("cursor").read_configured_upstream({}) is None


def test_qoder_configured_adds_https_scheme():
    """QODER_CENTER_DOMAIN is stored without scheme; we re-attach it for our
    target so the proxy knows where to forward."""
    assert clients.get("qoder").read_configured_upstream({"QODER_CENTER_DOMAIN": "qoder.com"}) == "https://qoder.com"


def test_devin_has_no_configured_upstream():
    """Devin uses a fixed endpoint; no user-customizable base_url."""
    assert clients.get("devin").read_configured_upstream({}) is None


# --- detect_auth ----------------------------------------------------------


def test_claude_oauth_detected(fake_home: Path):
    cred = fake_home / ".claude" / ".credentials.json"
    cred.parent.mkdir(parents=True)
    cred.write_text(json.dumps({"claudeAiOauth": {"accessToken": "x", "subscriptionType": "team"}}))
    info = clients.get("claude").detect_auth()
    assert info.logged_in
    assert info.mode == "oauth"
    assert info.suggested_target == "https://api.anthropic.com"


def test_claude_apikey_env_takes_precedence(fake_home: Path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    info = clients.get("claude").detect_auth()
    assert info.mode == "apikey"


def test_codex_chatgpt_oauth_picks_chatgpt_target(fake_home: Path):
    auth = fake_home / ".codex" / "auth.json"
    auth.parent.mkdir(parents=True)
    auth.write_text(json.dumps({"auth_mode": "chatgpt", "tokens": {"id_token": "x"}}))
    info = clients.get("codex").detect_auth()
    assert info.suggested_target == "https://chatgpt.com/backend-api/codex"


def test_codex_chatgpt_oauth_wins_over_stale_openai_api_key(fake_home: Path, monkeypatch):
    """auth.json (the user's explicit ``codex login`` choice) must beat a
    stale ``OPENAI_API_KEY`` env. Otherwise we'd pull ChatGPT-OAuth traffic
    to api.openai.com and the upstream returns 401 Missing scopes."""
    auth = fake_home / ".codex" / "auth.json"
    auth.parent.mkdir(parents=True)
    auth.write_text(json.dumps({"auth_mode": "chatgpt", "tokens": {"id_token": "x"}}))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-stale-key-from-some-other-shell")
    info = clients.get("codex").detect_auth()
    assert info.mode == "oauth"
    assert info.suggested_target == "https://chatgpt.com/backend-api/codex"


def test_codex_env_used_when_no_auth_json(fake_home: Path, monkeypatch):
    """No ``codex login`` ever run: env var is the only credential source."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-real-api-key")
    info = clients.get("codex").detect_auth()
    assert info.mode == "apikey"
    assert info.suggested_target == "https://api.openai.com"


def test_gemini_apikey_env(fake_home: Path, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "AIza-test")
    info = clients.get("gemini").detect_auth()
    assert info.mode == "apikey"
    assert info.suggested_target == "https://generativelanguage.googleapis.com"


def test_opencode_credentials_file_detected(fake_home: Path):
    auth = fake_home / ".local" / "share" / "opencode" / "auth.json"
    auth.parent.mkdir(parents=True)
    auth.write_text('{"anthropic": {"key": "x"}, "openai": {"key": "y"}}')
    info = clients.get("opencode").detect_auth()
    assert info.logged_in
    assert info.mode == "mixed"


# --- pre_launch_env_purge -------------------------------------------------


def test_claude_purges_nesting_env():
    """Claude Code refuses to start if it sees CLAUDECODE; we strip it."""
    purge = clients.get("claude").pre_launch_env_purge
    assert "CLAUDECODE" in purge
    assert "CLAUDE_CODE_SSE_PORT" in purge


def test_other_clients_have_no_purge_list():
    for name in ("codex", "gemini", "opencode", "pi", "kimi", "iflow", "cursor", "qoder", "devin", "hermes"):
        assert clients.get(name).pre_launch_env_purge == ()


# --- proprietary auth detection (Cursor / Qoder / Devin) ------------------


def test_proprietary_client_auth_detect_messages_are_actionable():
    """No creds → tell the user how to fix it (login command or env var)."""
    actionable_keywords = ("login", "configure", "_TOKEN", "_API_KEY", "_ACCESS_TOKEN")
    for name in ("cursor", "qoder", "devin"):
        info = clients.get(name).detect_auth()
        assert any(k.lower() in info.detail.lower() for k in actionable_keywords), (
            f"{name}: detail not actionable: {info.detail!r}"
        )


# --- multi-backend classification (drives forward-mode fallback) ----------


def test_is_multi_backend_for_multi_protocol_clients():
    for name in ("opencode", "pi", "kimi", "iflow", "hermes"):
        assert clients.is_multi_backend(clients.get(name)), name


def test_is_multi_backend_false_for_single_protocol_clients():
    for name in ("claude", "codex", "gemini", "cursor", "qoder", "devin"):
        assert not clients.is_multi_backend(clients.get(name)), name


def test_env_redirect_reliable_matches_client_capability():
    """Single-backend clients honor env / CLI-arg redirect. Multi-backend
    clients have config-file ``baseURL`` that overrides our env override,
    so they default to forward mode instead. Devin is single-backend but its
    rustls binary does not honor our env redirect, so it also defaults to
    forward mode."""
    for name in ("claude", "codex", "gemini", "cursor", "qoder"):
        assert clients.get(name).env_redirect_reliable, name
    for name in ("opencode", "pi", "kimi", "iflow", "hermes", "devin"):
        assert not clients.get(name).env_redirect_reliable, name
