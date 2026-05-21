"""Argument parsing: subcommand dispatch, ``--`` forwarding, defaults.

Also covers ``resolve_target_and_mode`` — the transparency-contract resolver
that decides which upstream URL to forward to and which proxy mode to use.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from claude_tap import clients as clients_mod
from claude_tap.cli import (
    _normalise_command,
    _resolve_live_default,
    _split_forward,
    _target_allows_env_proxy,
    build_parser,
    resolve_target_and_mode,
)

# --- splitting / normalisation --------------------------------------------


def test_split_forward_with_separator():
    own, fwd = _split_forward(["-L", "--", "--model", "x"])
    assert own == ["-L"]
    assert fwd == ["--model", "x"]


def test_split_forward_without_separator():
    own, fwd = _split_forward(["-L", "-p", "8080"])
    assert own == ["-L", "-p", "8080"]
    assert fwd == []


def test_normalise_default_to_run_when_empty():
    assert _normalise_command([]) == ["run"]


def test_normalise_keeps_known_subcommand():
    assert _normalise_command(["proxy", "-p", "8080"]) == ["proxy", "-p", "8080"]
    assert _normalise_command(["export", "trace.jsonl"]) == ["export", "trace.jsonl"]


def test_normalise_preserves_top_level_help():
    """``-h`` / ``--help`` must reach the top-level parser, not get wrapped in run."""
    assert _normalise_command(["-h"]) == ["-h"]
    assert _normalise_command(["--help"]) == ["--help"]
    assert _normalise_command(["-V"]) == ["-V"]


def test_normalise_prepends_run_for_bare_flags():
    """``claude-tap -L`` should mean ``claude-tap run -L``."""
    assert _normalise_command(["-L"]) == ["run", "-L"]
    assert _normalise_command(["-p", "8080"]) == ["run", "-p", "8080"]


# --- run subcommand parsing -----------------------------------------------


@pytest.fixture
def parser():
    return build_parser()


def test_run_default_client_is_claude(parser):
    args = parser.parse_args(["run"])
    assert args.command == "run"
    assert args.client == "claude"
    assert args.host == "127.0.0.1"  # default for run
    # ``--live`` is None by default; runtime picks based on TTY / CI / mode.
    assert args.live is None
    # ``--mode`` is None by default; the per-client default kicks in at runtime.
    assert args.mode is None


def test_run_with_codex_and_target(parser):
    args = parser.parse_args(["run", "codex", "-t", "https://chatgpt.com/backend-api/codex"])
    assert args.client == "codex"
    assert args.target == "https://chatgpt.com/backend-api/codex"


def test_run_with_gemini_and_opencode(parser):
    args = parser.parse_args(["run", "gemini"])
    assert args.client == "gemini"
    args = parser.parse_args(["run", "opencode"])
    assert args.client == "opencode"


def test_run_short_options_work(parser):
    args = parser.parse_args(["run", "-p", "9000", "-H", "0.0.0.0", "-L", "--live-port", "3001", "-o", "/tmp/x"])
    assert args.port == 9000
    assert args.host == "0.0.0.0"
    assert args.live is True
    assert args.live_port == 3001
    assert args.output_dir == "/tmp/x"


def test_run_no_live_overrides_smart_default(parser):
    args = parser.parse_args(["run", "--no-live"])
    assert args.live is False


def test_run_yolo_defaults_on(parser):
    args = parser.parse_args(["run"])
    assert args.yolo is True


def test_run_no_yolo_disables(parser):
    args = parser.parse_args(["run", "--no-yolo"])
    assert args.yolo is False


def test_run_export_prompt_path(parser):
    args = parser.parse_args(["run", "gemini", "--export-prompt", "prompt.md"])
    assert args.client == "gemini"
    assert args.export_prompt == "prompt.md"


def test_local_targets_do_not_use_outer_env_proxy():
    assert not _target_allows_env_proxy("http://127.0.0.1:1234")
    assert not _target_allows_env_proxy("http://localhost:1234")
    assert not _target_allows_env_proxy("http://[::1]:1234")
    assert _target_allows_env_proxy("https://api.anthropic.com")


def test_run_live_short_form(parser):
    args = parser.parse_args(["run", "-L"])
    assert args.live is True


def test_proxy_default_host_is_zero(parser):
    """proxy mode defaults to 0.0.0.0 so external clients can connect."""
    args = parser.parse_args(["proxy"])
    assert args.command == "proxy"
    assert args.host == "0.0.0.0"


def test_proxy_protocol_flag(parser):
    args = parser.parse_args(["proxy", "--protocol", "openai"])
    assert args.protocol == ["openai"]


def test_proxy_protocol_repeatable(parser):
    args = parser.parse_args(["proxy", "--protocol", "anthropic", "--protocol", "gemini"])
    assert args.protocol == ["anthropic", "gemini"]


def test_proxy_no_protocol_means_all(parser):
    args = parser.parse_args(["proxy"])
    assert args.protocol is None  # CLI dispatcher fills in all protocols


def test_export_inferred_format_from_extension(parser):
    args = parser.parse_args(["export", "t.jsonl", "-o", "out.json"])
    assert args.output == "out.json"
    assert args.fmt is None  # inferred at runtime


def test_export_explicit_html_format(parser):
    args = parser.parse_args(["export", "t.jsonl", "--format", "html"])
    assert args.fmt == "html"


def test_export_full_events_flag_defaults_off(parser):
    args = parser.parse_args(["export", "t.jsonl", "--format", "html"])
    assert args.full_events is False


def test_export_full_events_flag_can_be_set(parser):
    args = parser.parse_args(["export", "t.jsonl", "--format", "html", "--full-events"])
    assert args.full_events is True


def test_export_stdin_marker(parser):
    args = parser.parse_args(["export", "-"])
    assert args.trace == "-"


def test_update_install_flag(parser):
    args = parser.parse_args(["update", "--install"])
    assert args.command == "update"
    assert args.install is True


def test_ca_action_path(parser):
    args = parser.parse_args(["ca", "path"])
    assert args.command == "ca"
    assert args.ca_action == "path"


def test_unknown_subcommand_errors(parser):
    with pytest.raises(SystemExit):
        parser.parse_args(["does-not-exist"])


# ---------------------------------------------------------------------------
# resolve_target_and_mode — transparency contract
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_home(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setenv("HOME", str(tmp_path))
    for v in (
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "GEMINI_API_KEY",
        "ANTHROPIC_BASE_URL",
        "GOOGLE_GEMINI_BASE_URL",
        "OPENROUTER_BASE_URL",
    ):
        monkeypatch.delenv(v, raising=False)
    return tmp_path


def test_resolve_explicit_target_wins(fake_home: Path):
    """``--target`` overrides everything, including a configured base_url."""
    target, src, mode = resolve_target_and_mode(
        client=clients_mod.get("claude"),
        auth=clients_mod.get("claude").detect_auth(),
        explicit_target="https://override.example.com",
        explicit_mode=None,
        env={"ANTHROPIC_BASE_URL": "https://from-env.example.com"},
        fallback_default_target="https://api.anthropic.com",
    )
    assert target == "https://override.example.com"
    assert src == "from --target"
    assert mode == "reverse"


def test_resolve_configured_upstream_wins_over_auth(fake_home: Path):
    """User's ``ANTHROPIC_BASE_URL`` beats the auth-derived default — this
    is the core transparency contract."""
    target, src, mode = resolve_target_and_mode(
        client=clients_mod.get("claude"),
        auth=clients_mod.get("claude").detect_auth(),
        explicit_target=None,
        explicit_mode=None,
        env={"ANTHROPIC_BASE_URL": "https://my-relay.example.com"},
        fallback_default_target="https://api.anthropic.com",
    )
    assert target == "https://my-relay.example.com"
    assert "config" in src.lower()
    assert mode == "reverse"


def test_resolve_falls_back_to_auth_when_no_config(fake_home: Path, monkeypatch):
    """No env, no config → use the auth-derived suggested target."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    target, src, mode = resolve_target_and_mode(
        client=clients_mod.get("claude"),
        auth=clients_mod.get("claude").detect_auth(),
        explicit_target=None,
        explicit_mode=None,
        env={},
        fallback_default_target="https://api.anthropic.com",
    )
    assert target == "https://api.anthropic.com"
    assert src.startswith("auto:")
    assert mode == "reverse"


def test_resolve_codex_reads_user_provider_from_toml(fake_home: Path):
    """Codex's transparency: read ``model_providers.<active>.base_url`` from
    ``~/.codex/config.toml``."""
    cfg = fake_home / ".codex" / "config.toml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text(
        'model_provider = "my-relay"\n[model_providers.my-relay]\nbase_url = "https://relay.example.com/v1"\n',
        encoding="utf-8",
    )
    codex = clients_mod.get("codex")
    target, src, mode = resolve_target_and_mode(
        client=codex,
        auth=codex.detect_auth(),
        explicit_target=None,
        explicit_mode=None,
        env={},
        fallback_default_target="https://api.openai.com",
    )
    assert target == "https://relay.example.com/v1"
    assert "config" in src.lower()
    assert mode == "reverse"


def test_resolve_config_driven_clients_force_forward_mode(fake_home: Path):
    """opencode/pi/kimi/iflow/hermes honor a config-file ``baseURL`` over
    env vars. Reverse mode would silently capture nothing, so we default
    to forward (HTTPS_PROXY + CA) for these. Verified empirically against
    real opencode binary."""
    for name in ("opencode", "pi", "kimi", "iflow", "hermes", "devin"):
        client = clients_mod.get(name)
        _, _, mode = resolve_target_and_mode(
            client=client,
            auth=clients_mod.AuthInfo(logged_in=True, mode="apikey", suggested_target="https://x.example.com"),
            explicit_target=None,
            explicit_mode=None,
            env={},
            fallback_default_target="https://api.anthropic.com",
        )
        assert mode == "forward", f"{name} should default to forward (env redirect unreliable)"


def test_resolve_single_backend_clients_use_reverse(fake_home: Path):
    """claude / codex / gemini / cursor / qoder all honor env or
    CLI-arg overrides reliably — reverse mode captures their traffic."""
    for name in ("claude", "codex", "gemini", "cursor", "qoder"):
        client = clients_mod.get(name)
        _, _, mode = resolve_target_and_mode(
            client=client,
            auth=clients_mod.AuthInfo(logged_in=True, mode="apikey", suggested_target="https://x.example.com"),
            explicit_target=None,
            explicit_mode=None,
            env={},
            fallback_default_target="https://x.example.com",
        )
        assert mode == "reverse", f"{name} should default to reverse (env redirect works)"


def test_resolve_explicit_mode_always_honored(fake_home: Path):
    """``--mode forward`` forces forward even when reverse would have worked."""
    _, _, mode = resolve_target_and_mode(
        client=clients_mod.get("claude"),
        auth=clients_mod.get("claude").detect_auth(),
        explicit_target=None,
        explicit_mode="forward",
        env={"ANTHROPIC_BASE_URL": "https://x.example.com"},
        fallback_default_target="https://api.anthropic.com",
    )
    assert mode == "forward"


# ---------------------------------------------------------------------------
# _resolve_live_default — TTY-aware smart default
# ---------------------------------------------------------------------------


class _Args:
    """Minimal argparse.Namespace stand-in for resolver tests."""

    def __init__(self, *, live):
        self.live = live


def test_live_explicit_on_always_wins(monkeypatch):
    monkeypatch.setattr("sys.stdin", type("Stdin", (), {"isatty": staticmethod(lambda: False)})())
    monkeypatch.setenv("CI", "1")
    assert _resolve_live_default(_Args(live=True), launch_client=True) is True
    assert _resolve_live_default(_Args(live=True), launch_client=False) is True


def test_live_explicit_off_always_wins(monkeypatch):
    monkeypatch.setattr("sys.stdin", type("Stdin", (), {"isatty": staticmethod(lambda: True)})())
    monkeypatch.delenv("CI", raising=False)
    assert _resolve_live_default(_Args(live=False), launch_client=True) is False


def test_live_default_off_for_proxy_subcommand(monkeypatch):
    monkeypatch.setattr("sys.stdin", type("Stdin", (), {"isatty": staticmethod(lambda: True)})())
    monkeypatch.delenv("CI", raising=False)
    assert _resolve_live_default(_Args(live=None), launch_client=False) is False


def test_live_default_off_when_stdin_is_pipe(monkeypatch):
    """Headless / piped invocations get no browser auto-open."""
    monkeypatch.setattr("sys.stdin", type("Stdin", (), {"isatty": staticmethod(lambda: False)})())
    monkeypatch.delenv("CI", raising=False)
    assert _resolve_live_default(_Args(live=None), launch_client=True) is False


def test_live_default_off_in_ci(monkeypatch):
    monkeypatch.setattr("sys.stdin", type("Stdin", (), {"isatty": staticmethod(lambda: True)})())
    monkeypatch.setenv("CI", "true")
    assert _resolve_live_default(_Args(live=None), launch_client=True) is False


def test_live_default_on_for_interactive_run(monkeypatch):
    monkeypatch.setattr("sys.stdin", type("Stdin", (), {"isatty": staticmethod(lambda: True)})())
    monkeypatch.delenv("CI", raising=False)
    assert _resolve_live_default(_Args(live=None), launch_client=True) is True


def test_resolve_proxy_only_no_client(fake_home: Path):
    """``proxy`` subcommand has no client; resolver should still produce a
    sensible default target."""
    target, src, mode = resolve_target_and_mode(
        client=None,
        auth=None,
        explicit_target=None,
        explicit_mode=None,
        env={},
        fallback_default_target="https://api.anthropic.com",
    )
    assert target == "https://api.anthropic.com"
    assert src == "default"
    assert mode == "reverse"
