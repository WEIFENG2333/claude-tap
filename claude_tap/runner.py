"""Spawn the target CLI (one of :mod:`claude_tap.clients`) under the running proxy."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import signal
import sys
from pathlib import Path

from claude_tap.clients import Client

log = logging.getLogger("claude_tap")

_PROXY_ENV_VARS = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
)


def _install_forward_proxy_env(env: dict[str, str], proxy_url: str, ca_cert_path: Path | None) -> None:
    """Inject ``HTTPS_PROXY`` (all standard variants) and CA-trust env vars.

    The CA env covers the three runtimes our supported clients use:
      - ``NODE_EXTRA_CA_CERTS`` — Node.js (Claude Code, Cursor, opencode, Pi, iFlow)
      - ``SSL_CERT_FILE`` — Python ``ssl`` (Kimi, Hermes)
      - ``REQUESTS_CA_BUNDLE`` — Python ``requests`` library
    """

    for k in _PROXY_ENV_VARS:
        env[k] = proxy_url
    env["NO_PROXY"] = "127.0.0.1,localhost"
    if ca_cert_path:
        ca = str(ca_cert_path)
        env["NODE_EXTRA_CA_CERTS"] = ca
        env["SSL_CERT_FILE"] = ca
        env["REQUESTS_CA_BUNDLE"] = ca


def _claude_settings_arg(proxy_url: str, ca_cert_path: Path | None) -> list[str]:
    payload: dict[str, dict[str, str]] = {
        "env": {
            "HTTP_PROXY": proxy_url,
            "HTTPS_PROXY": proxy_url,
            "ALL_PROXY": proxy_url,
            "http_proxy": proxy_url,
            "https_proxy": proxy_url,
            "all_proxy": proxy_url,
        }
    }
    if ca_cert_path:
        payload["env"]["NODE_EXTRA_CA_CERTS"] = str(ca_cert_path)
    return ["--settings", json.dumps(payload, separators=(",", ":"))]


def _strip_proxy_env_for_reverse(env: dict[str, str]) -> None:
    """Avoid sending localhost reverse-proxy traffic through an outer proxy.

    In reverse mode the child client should connect directly to claude-tap's
    local base URL. Some runtimes do not honor NO_PROXY reliably for localhost
    once HTTP_PROXY/ALL_PROXY are present, so remove inherited proxy variables
    from the child process. The claude-tap parent still keeps its own proxy env
    and can use it for outbound upstream traffic.
    """

    for key in _PROXY_ENV_VARS:
        env.pop(key, None)
    env["NO_PROXY"] = "127.0.0.1,localhost"
    env["no_proxy"] = "127.0.0.1,localhost"


async def run_client(
    *,
    client: Client,
    proxy_port: int,
    proxy_host: str,
    forward_args: list[str],
    proxy_mode: str = "reverse",
    ca_cert_path: Path | None = None,
    yolo: bool = True,
) -> int:
    """Spawn ``client.cmd`` pointed at the local proxy and wait for it.

    Hands the foreground process group to the child so its TUI gets full
    terminal control (Cmd+Delete, Ctrl+U etc.). Translates Ctrl+C / Ctrl+Z
    to a clean child shutdown without suspending the proxy.
    """

    if shutil.which(client.cmd) is None:
        sys.stderr.write(
            f"\nError: '{client.cmd}' not found in PATH.\nInstall {client.label} first: {client.install_url}\n"
        )
        return 1

    env = os.environ.copy()
    cmd_args = list(forward_args)
    proxy_url = f"http://{proxy_host}:{proxy_port}"

    redirect_env: dict[str, str] = {}
    redirect_args: list[str] = []
    if proxy_mode == "forward":
        _install_forward_proxy_env(env, proxy_url, ca_cert_path)
        # Claude Code reads proxy settings from its --settings JSON, not just
        # process env. Inject equivalent settings unless the user already
        # supplied --settings.
        if client.name == "claude":
            has_settings = any(a == "--settings" or a.startswith("--settings=") for a in cmd_args)
            if not has_settings:
                cmd_args = _claude_settings_arg(proxy_url, ca_cert_path) + cmd_args
    else:
        _strip_proxy_env_for_reverse(env)
        redirect_env = client.env_overrides(proxy_url)
        env.update(redirect_env)
        # Some clients (codex) ignore env-based base-URL overrides and need
        # CLI-level redirect (e.g. ``-c openai_base_url=…``).
        redirect_args = client.cli_args_overrides(proxy_url, os.environ)
        cmd_args = redirect_args + cmd_args

    for key in client.pre_launch_env_purge:
        env.pop(key, None)

    yolo_args: list[str] = []
    if yolo:
        if client.yolo_args:
            yolo_args = list(client.yolo_args)
            if client.yolo_args_position == "after-first-arg" and cmd_args:
                # Some CLIs put global options after their subcommand.
                cmd_args = [cmd_args[0], *yolo_args, *cmd_args[1:]]
            else:
                # Default to leading flags so user-supplied args can override
                # later when the child CLI resolves conflicts by last value.
                cmd_args = yolo_args + cmd_args
        else:
            sys.stdout.write(
                f"[claude-tap] note: {client.label} has no single-flag yolo mode; "
                f"approve actions in-session or pass --no-yolo to silence this.\n"
            )

    cmd = [client.cmd] + cmd_args
    sys.stdout.write(f"\n[claude-tap] launching {client.label}: {' '.join(cmd)}\n")
    if proxy_mode == "forward":
        sys.stdout.write(f"[claude-tap] HTTPS_PROXY={proxy_url}\n")
        if ca_cert_path:
            sys.stdout.write(f"[claude-tap] NODE_EXTRA_CA_CERTS={ca_cert_path}\n")
    else:
        for key, value in redirect_env.items():
            sys.stdout.write(f"[claude-tap] {key}={value}\n")
        if redirect_args:
            sys.stdout.write(f"[claude-tap] cli args: {' '.join(redirect_args)}\n")
    if yolo_args:
        sys.stdout.write(f"[claude-tap] yolo: {' '.join(yolo_args)}  (default; use --no-yolo to disable)\n")
    sys.stdout.flush()

    use_fg = hasattr(os, "tcsetpgrp") and sys.stdin.isatty()
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        env=env,
        stdin=None,
        stdout=None,
        stderr=None,
        **({"process_group": 0} if use_fg else {}),
    )
    if use_fg:
        try:
            os.tcsetpgrp(sys.stdin.fileno(), proc.pid)
        except OSError:
            pass

    loop = asyncio.get_running_loop()
    old_sigtstp = signal.signal(signal.SIGTSTP, signal.SIG_IGN) if hasattr(signal, "SIGTSTP") else None
    sigint_count = 0

    def _on_sigint() -> None:
        nonlocal sigint_count
        sigint_count += 1
        if proc.returncode is not None:
            return
        if sigint_count == 1:
            proc.terminate()
            sys.stdout.write(f"\n[claude-tap] shutting down {client.label}... (Ctrl+C again to force)\n")
            sys.stdout.flush()
        else:
            proc.kill()

    def _on_sigtstp() -> None:
        if proc.returncode is None:
            proc.terminate()

    try:
        loop.add_signal_handler(signal.SIGINT, _on_sigint)
        if hasattr(signal, "SIGTSTP"):
            loop.add_signal_handler(signal.SIGTSTP, _on_sigtstp)
    except (NotImplementedError, OSError):
        pass

    code = await proc.wait()

    if use_fg:
        old_ttou = signal.signal(signal.SIGTTOU, signal.SIG_IGN)
        try:
            os.tcsetpgrp(sys.stdin.fileno(), os.getpgrp())
        except OSError:
            pass
        signal.signal(signal.SIGTTOU, old_ttou)

    if old_sigtstp is not None and hasattr(signal, "SIGTSTP"):
        signal.signal(signal.SIGTSTP, old_sigtstp)
    for sig in (signal.SIGINT, getattr(signal, "SIGTSTP", None)):
        if sig is None:
            continue
        try:
            loop.remove_signal_handler(sig)
        except (NotImplementedError, OSError):
            pass

    sys.stdout.write(f"\n[claude-tap] {client.label} exited with code {code}\n")
    sys.stdout.flush()
    return code
