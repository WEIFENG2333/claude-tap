"""``claude-tap`` command-line interface (subcommand-based).

Top-level commands::

    run [client]     Trace a client and launch it (default if no subcommand)
    proxy            Run the proxy alone (for external clients)
    live             Open the live viewer against an existing trace tree
    export FILE      Render a trace as markdown / json / prompt-md / html
    update           Check for, and optionally install, a new release
    ca {path,regen}  Manage the local TLS CA used by forward mode

A standalone ``--`` argument separates ``claude-tap``'s own flags from the
arguments forwarded to the launched client. Everything after ``--`` is given
verbatim to ``claude`` / ``codex`` / ``gemini`` / ``opencode``.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import signal
import sys
import threading
import webbrowser
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import aiohttp
from aiohttp import web

from claude_tap import clients as clients_mod
from claude_tap import manifest as manifest_mod
from claude_tap import protocols as protocols_mod
from claude_tap import update as update_mod
from claude_tap._version import __version__
from claude_tap.certs import CertificateAuthority, ensure_ca
from claude_tap.forward_proxy import ForwardProxyServer
from claude_tap.live_viewer import LiveSink, LiveViewerServer
from claude_tap.logging_setup import configure_logging
from claude_tap.paths import data_dir
from claude_tap.pipeline import ProxyContext
from claude_tap.reverse_proxy import build_app
from claude_tap.runner import run_client
from claude_tap.trace import EventBus, JsonlSink, StatsSink
from claude_tap.viewer import render_html

# Keep print() flushed (uv tool wraps stdout in full-buffered pipes).
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(line_buffering=True)

SUBCOMMANDS = {"run", "proxy", "live", "export", "update", "ca"}


# ---------------------------------------------------------------------------
# Target and mode resolution — kept pure so it's directly testable.
# ---------------------------------------------------------------------------


def resolve_target_and_mode(
    *,
    client: clients_mod.Client | None,
    auth: clients_mod.AuthInfo | None,
    explicit_target: str | None,
    explicit_mode: str | None,
    env: "os._Environ[str] | dict[str, str]",
    fallback_default_target: str,
) -> tuple[str, str, str]:
    """Decide ``(target, target_source, mode)`` for a launch.

    Priority for ``target`` (the real upstream we forward each request to):

    1. ``explicit_target`` (``--target``) wins outright.
    2. ``client.read_configured_upstream(env)`` — the user's actual base_url
       parsed from their CLI's own config files / env vars. This is what
       preserves the transparency contract: never silently overwrite a
       user's private-relay configuration.
    3. ``auth.suggested_target`` — endpoint implied by their login state.
    4. ``fallback_default_target`` — protocol default.

    Mode auto-picks ``reverse`` for clients whose env / CLI-arg redirect is
    reliable. Some multi-backend clients (opencode / pi / omp / kimi /
    kimi-code / mimo / iflow / hermes) honor a config-file ``baseURL`` over
    env, so reverse mode would
    silently capture nothing — those default to ``forward`` (HTTPS_PROXY +
    CA-MITM). OpenClaw is also config-driven, but claude-tap patches a
    temporary OpenClaw config for the child process, so it can stay in reverse
    mode.
    ``--mode`` always wins.
    """

    configured = client.read_configured_upstream(env) if client is not None else None

    if explicit_target:
        target = explicit_target
        target_source = "from --target"
    elif configured:
        target = configured
        target_source = f"from {client.label} config" if client else "from config"
    elif auth is not None and auth.suggested_target:
        target = auth.suggested_target
        target_source = f"auto: {auth.detail}"
    else:
        target = fallback_default_target
        target_source = "default"

    if explicit_mode in ("reverse", "forward"):
        mode = explicit_mode
    elif client is not None and not client.env_redirect_reliable:
        # Some config-driven clients honor a config-file ``baseURL`` over our
        # env override, so reverse mode would silently capture nothing.
        # Forward mode (HTTPS_PROXY + CA-MITM) reliably intercepts those.
        mode = "forward"
    else:
        mode = "reverse"

    return target, target_source, mode


def _resolve_live_default(args: argparse.Namespace) -> bool:
    """Decide whether to start the live viewer when ``--live`` was not given.

    * Explicit ``-L`` / ``--live`` → on.
    * Explicit ``--no-live`` → off.
    * Otherwise: on iff interactive (TTY, not in CI), for both ``run`` and
      ``proxy``. Headless / piped / CI runs stay off so scripts don't spawn a
      viewer or open a browser unexpectedly.
    """
    if args.live is True:
        return True
    if args.live is False:
        return False
    if not sys.stdin.isatty():
        return False
    if os.environ.get("CI"):
        return False
    return True


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="claude-tap",
        description=(
            "Trace Claude Code / Codex CLI API traffic through a local proxy.\n\n"
            "If no subcommand is given, ``run`` is implied. Anything after ``--`` is\n"
            "forwarded verbatim to the launched client."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "EXAMPLES\n"
            "  claude-tap                                # trace claude with defaults\n"
            "  claude-tap -L                             # + live viewer in browser\n"
            "  claude-tap -- --model claude-opus-4-6     # forward args to claude\n"
            "  claude-tap codex                          # auto-picks ChatGPT OAuth target if present\n"
            "  claude-tap gemini                         # uses GEMINI_API_KEY env var\n"
            "  claude-tap opencode                       # multi-protocol: anthropic+openai+gemini\n"
            "  claude-tap openclaw                       # OpenClaw via configured provider\n"
            "  claude-tap proxy --protocol openai        # standalone proxy, OpenAI paths only\n"
            "  claude-tap run gemini --export-prompt prompt.md -- -p hi\n"
            "  claude-tap export trace.jsonl -o out.md\n"
            "  claude-tap export trace.jsonl --format prompt-md -o prompt.md\n"
            "  claude-tap export trace.jsonl --format html\n"
            "  claude-tap ca path                        # print CA cert path\n"
        ),
    )
    parser.add_argument("-V", "--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("-v", "--verbose", action="count", default=0, help="increase verbosity (-vv = debug)")
    parser.add_argument("-q", "--quiet", action="store_true", help="suppress non-error output")
    parser.add_argument("--no-color", action="store_true", help="disable ANSI colors (also honors NO_COLOR)")

    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    _build_run_parser(sub)
    _build_proxy_parser(sub)
    _build_live_parser(sub)
    _build_export_parser(sub)
    _build_update_parser(sub)
    _build_ca_parser(sub)

    return parser


def _add_proxy_options(parser: argparse.ArgumentParser, *, default_host: str) -> None:
    parser.add_argument("-p", "--port", type=int, default=0, help="proxy port (default: auto)")
    parser.add_argument("-H", "--host", default=default_host, help=f"bind address (default: {default_host})")
    parser.add_argument("-t", "--target", default=None, help="upstream API URL (default: protocol's default)")
    parser.add_argument(
        "-m",
        "--mode",
        choices=("reverse", "forward"),
        default=None,
        help="proxy mode (default: per-client; multi-backend clients use forward)",
    )
    parser.add_argument("-o", "--output-dir", default="./.traces", help="trace output directory")
    parser.add_argument("--max-traces", type=int, default=50, help="keep last N sessions (0 = unlimited)")
    parser.add_argument("--no-update-check", action="store_true", help="skip PyPI update check")


def _add_viewer_options(parser: argparse.ArgumentParser) -> None:
    # ``--live`` defaults to None so the runtime can pick a smart default
    # (on for interactive ``run``, off for piped / CI / ``proxy``). Use
    # ``-L`` to force on or ``--no-live`` to force off.
    parser.add_argument(
        "-L",
        "--live",
        dest="live",
        action="store_true",
        default=None,
        help="force the real-time viewer on (overrides smart default)",
    )
    parser.add_argument(
        "--no-live",
        dest="live",
        action="store_false",
        help="force the real-time viewer off (overrides smart default)",
    )
    parser.add_argument("--live-port", type=int, default=0, help="live viewer port (default: auto)")
    parser.add_argument("--no-open", action="store_true", help="don't auto-open the HTML viewer on exit")


def _build_run_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "run",
        help="Trace a client and launch it (default subcommand)",
        description="Start the proxy and spawn the chosen client pointed at it.",
    )
    p.add_argument(
        "client",
        nargs="?",
        default="claude",
        metavar="CLIENT",
        help=(
            "which client to launch (default: claude). Built-in: "
            f"{', '.join(clients_mod.names())}. Any other name runs that command "
            "in generic forward-proxy mode (passthrough capture)."
        ),
    )
    # Yolo (auto-approve all actions) is on by default. Each client
    # translates this to its own equivalent flag (claude:
    # --dangerously-skip-permissions; codex: --full-auto; gemini/kimi/
    # kimi-code/iflow/cursor/qoder/hermes: --yolo; devin:
    # --permission-mode dangerous; opencode: --dangerously-skip-permissions;
    # mimo: --never-ask; omp: --approval-mode yolo. Pi has no one-flag
    # equivalent; we just print a note.
    p.add_argument(
        "--yolo",
        dest="yolo",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=("auto-approve every action by injecting the client's own yolo flag (default on; --no-yolo to disable)"),
    )
    p.add_argument(
        "--export-prompt",
        metavar="PATH",
        default=None,
        help=(
            "after the client exits, export the captured system prompt / instructions / tools as Markdown; "
            "when this succeeds it is treated as a successful capture even if the client exited non-zero"
        ),
    )
    _add_proxy_options(p, default_host="127.0.0.1")
    _add_viewer_options(p)


def _build_proxy_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "proxy",
        help="Run the proxy without launching a client",
        description=(
            "Start the proxy and wait. The proxy accepts requests for any path matching one of "
            "the selected protocols' allowlists; defaults to all known protocols."
        ),
    )
    p.add_argument(
        "--protocol",
        action="append",
        choices=protocols_mod.names(),
        help="upstream protocol to accept (repeat for multiple; default: all known protocols)",
    )
    _add_proxy_options(p, default_host="0.0.0.0")
    _add_viewer_options(p)


def _build_live_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "live",
        help="Open the live viewer against an existing trace tree",
        description="Start an HTTP server that serves the viewer over historic and new traces.",
    )
    p.add_argument("-p", "--port", type=int, default=0)
    p.add_argument("-H", "--host", default="127.0.0.1")
    p.add_argument("-o", "--output-dir", default="./.traces")
    p.add_argument("--no-open", action="store_true")


def _build_export_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "export",
        help="Render a trace JSONL file as markdown / json / prompt-md / html",
        description="Read a trace JSONL (or '-' for stdin) and write a rendered version.",
    )
    p.add_argument("trace", help="path to a .jsonl trace file (use '-' to read stdin)")
    p.add_argument("-o", "--output", default=None, help="output file (default: stdout, '-' = stdout)")
    p.add_argument(
        "--format",
        dest="fmt",
        choices=("markdown", "json", "prompt-md", "html"),
        default=None,
        help="output format (default: inferred from -o, else markdown)",
    )
    p.add_argument(
        "--full-events",
        action="store_true",
        help="(html only) keep per-chunk SSE/WebSocket event lists; can grow the file 5-10x",
    )


def _build_update_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "update",
        help="Check PyPI for updates and (optionally) install",
        description="Without flags this prints the latest version. Pass --install to upgrade.",
    )
    p.add_argument("--install", action="store_true", help="actually install if a newer version exists")


def _build_ca_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "ca",
        help="Manage the local CA used by forward mode",
        description="Print, regenerate, or display install instructions for the local TLS CA.",
    )
    csub = p.add_subparsers(dest="ca_action", metavar="ACTION")
    csub.add_parser("path", help="print the CA certificate path")
    csub.add_parser("regen", help="delete and regenerate the CA")
    csub.add_parser("install", help="print platform-specific instructions to trust the CA")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def _split_forward(argv: list[str]) -> tuple[list[str], list[str]]:
    if "--" not in argv:
        return argv, []
    idx = argv.index("--")
    return argv[:idx], argv[idx + 1 :]


def _normalise_command(argv: list[str]) -> list[str]:
    """If argv does not start with a known subcommand, prepend ``run``."""
    if not argv:
        return ["run"]
    head = argv[0]
    if head in SUBCOMMANDS or head in ("-h", "--help", "-V", "--version"):
        return argv
    # Help-only flags should fall through to the top-level parser, but other
    # leading flags imply the user wants ``run``.
    return ["run"] + argv


def main(argv: list[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    own, forward = _split_forward(raw)
    own = _normalise_command(own)

    parser = build_parser()
    args = parser.parse_args(own)
    args.forward = forward

    if args.no_color:
        os.environ["NO_COLOR"] = "1"

    cmd = args.command
    if cmd in (None, "run"):
        return _run_pipeline(args, launch_client=True)
    if cmd == "proxy":
        return _run_pipeline(args, launch_client=False)
    if cmd == "live":
        return _run_live_only(args)
    if cmd == "export":
        return _run_export(args)
    if cmd == "update":
        return _run_update(args)
    if cmd == "ca":
        return _run_ca(args)
    parser.error(f"unknown command: {cmd}")
    return 2  # pragma: no cover


# ---------------------------------------------------------------------------
# run / proxy
# ---------------------------------------------------------------------------


def _run_pipeline(args: argparse.Namespace, *, launch_client: bool) -> int:
    try:
        return asyncio.run(_run_pipeline_async(args, launch_client=launch_client))
    except KeyboardInterrupt:
        return 0


async def _run_pipeline_async(args: argparse.Namespace, *, launch_client: bool) -> int:
    # Two paths converge here:
    #   - ``run <client>``: we have a Client; its protocols decide what we accept.
    #   - ``proxy --protocol …``: no client; user picks protocols directly.
    if launch_client:
        client_name = getattr(args, "client", None) or "claude"
        client = clients_mod.get_or_generic(client_name)
        if client_name not in clients_mod.names():
            sys.stdout.write(
                f"[claude-tap] '{client_name}' is not a built-in client; running in generic "
                "forward-proxy mode (passthrough capture, no structured snapshot)\n"
            )
        protocols = client.protocols
        # For target auto-detection: ask the client.
        auth = client.detect_auth()
        label = client.label
    else:
        client = None
        chosen = getattr(args, "protocol", None) or list(protocols_mod.names())
        protocols = tuple(protocols_mod.get(n) for n in chosen)
        auth = None
        label = "proxy"

    target, target_source, mode = resolve_target_and_mode(
        client=client,
        auth=auth,
        explicit_target=args.target,
        explicit_mode=args.mode,
        env=os.environ,
        fallback_default_target=protocols[0].default_target,
    )
    args.mode = mode

    sys.stdout.write(
        f"[claude-tap] {label} target: {target}  ({target_source})  protocols: {','.join(p.name for p in protocols)}\n"
    )
    if auth is not None and not auth.logged_in:
        sys.stderr.write(f"[claude-tap] warning: {auth.detail}\n")

    output_dir = Path(args.output_dir).expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)

    now = datetime.now()
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H%M%S")
    ts = now.strftime("%Y%m%d_%H%M%S")
    date_dir = output_dir / date_str
    date_dir.mkdir(parents=True, exist_ok=True)
    trace_path = date_dir / f"trace_{time_str}.jsonl"
    log_path = date_dir / f"trace_{time_str}.log"
    html_path = trace_path.with_suffix(".html")

    configure_logging(verbosity=args.verbose, quiet=args.quiet, log_file=log_path)

    # StatsSink picks the protocol whose ``allowed_paths`` matches each
    # request's path (see ``trace.StatsSink.handle``), so multi-protocol
    # clients (opencode) report usage correctly regardless of which backend
    # the user routed to.
    bus = EventBus()
    jsonl = JsonlSink(trace_path)
    stats = StatsSink(protocols)
    bus.subscribe(jsonl)
    bus.subscribe(stats)

    session = aiohttp.ClientSession(auto_decompress=False, trust_env=_target_allows_env_proxy(target))

    forward_server: ForwardProxyServer | None = None
    web_runner: web.AppRunner | None = None
    ca_cert_path: Path | None = None

    capture_only = bool(launch_client and getattr(args, "export_prompt", None))
    ctx = ProxyContext(protocols=protocols, target=target, bus=bus, session=session, capture_only=capture_only)
    if capture_only:
        sys.stdout.write("[claude-tap] capture-only: exporting prompt without calling upstream\n")

    if args.mode == "forward":
        cert_path, key_path = ensure_ca()
        ca = CertificateAuthority(cert_path, key_path)
        ca_cert_path = cert_path
        forward_server = ForwardProxyServer(args.host, args.port, ca, ctx)
        actual_port = await forward_server.start()
        sys.stdout.write(f"[claude-tap] v{__version__} forward proxy on http://{args.host}:{actual_port}\n")
        sys.stdout.write(f"[claude-tap] CA cert: {cert_path}\n")
    else:
        app = build_app(ctx)
        web_runner = web.AppRunner(app)
        await web_runner.setup()
        site = web.TCPSite(web_runner, args.host, args.port)
        await site.start()
        try:
            actual_port = site._server.sockets[0].getsockname()[1]
        except (AttributeError, IndexError, OSError):
            actual_port = args.port
        sys.stdout.write(f"[claude-tap] v{__version__} listening on http://{args.host}:{actual_port}\n")

    sys.stdout.write(f"[claude-tap] trace: {trace_path}\n")
    sys.stdout.flush()

    live_enabled = _resolve_live_default(args)
    live_server: LiveViewerServer | None = None
    if live_enabled:
        live_server = LiveViewerServer(
            current_jsonl=trace_path,
            port=args.live_port,
            host=args.host,
            output_dir=output_dir,
        )
        await live_server.start()
        bus.subscribe(LiveSink(live_server))
        hint = "" if args.live is True else "  (use --no-live to disable)"
        sys.stdout.write(f"[claude-tap] live viewer: {live_server.url}{hint}\n")
        sys.stdout.flush()
        if not args.no_open:
            _open_browser(live_server.url)

    if not args.no_update_check:
        try:
            latest = await update_mod.latest_version()
            if latest and update_mod.is_newer(latest):
                update_mod.hint(latest)
        except Exception:
            pass

    exit_code = 0
    try:
        if launch_client:
            assert client is not None
            try:
                exit_code = await run_client(
                    client=client,
                    proxy_port=actual_port,
                    proxy_host=args.host,
                    forward_args=args.forward,
                    proxy_mode=args.mode,
                    ca_cert_path=ca_cert_path,
                    yolo=getattr(args, "yolo", True),
                )
            except asyncio.CancelledError:
                pass
        else:
            sys.stdout.write("[claude-tap] proxy-only mode; press Ctrl+C to stop.\n")
            sys.stdout.flush()
            try:
                await _wait_forever()
            except asyncio.CancelledError:
                pass
    finally:
        try:
            await session.close()
        except Exception:
            pass
        if forward_server:
            try:
                await forward_server.stop()
            except Exception:
                pass
        if web_runner:
            try:
                await web_runner.cleanup()
            except Exception:
                pass
        if live_server:
            try:
                await live_server.stop()
            except Exception:
                pass
        await bus.close_all()

        render_html(trace_path, html_path)

        prompt_export_path = getattr(args, "export_prompt", None) if launch_client else None
        prompt_export_rc: int | None = None
        prompt_path: Path | None = None
        if prompt_export_path:
            prompt_export_rc = _export_prompt_from_trace(trace_path, prompt_export_path)
            if prompt_export_rc == 0 and prompt_export_path != "-":
                prompt_path = Path(prompt_export_path).expanduser()

        files = _session_manifest_files(
            output_dir=output_dir,
            trace_path=trace_path,
            log_path=log_path,
            html_path=html_path if html_path.exists() else None,
            prompt_path=prompt_path if prompt_path is not None and prompt_path.exists() else None,
        )
        manifest_mod.register(output_dir, ts, files)
        if args.max_traces > 0:
            removed = manifest_mod.cleanup(output_dir, args.max_traces)
            if removed:
                sys.stdout.write(f"[claude-tap] cleaned up {removed} old trace session(s)\n")

        summary = stats.summary()
        sys.stdout.write("\n[claude-tap] summary:\n")
        sys.stdout.write(f"  api_calls:    {summary['api_calls']}\n")
        if summary["input_tokens"] or summary["output_tokens"]:
            sys.stdout.write(f"  tokens:       {summary['input_tokens']:,} in / {summary['output_tokens']:,} out\n")
            if summary["cache_read_tokens"]:
                sys.stdout.write(f"  cache read:   {summary['cache_read_tokens']:,}\n")
            if summary["cache_create_tokens"]:
                sys.stdout.write(f"  cache write:  {summary['cache_create_tokens']:,}\n")
        sys.stdout.write(f"  trace:        {trace_path}\n")
        sys.stdout.write(f"  log:          {log_path}\n")
        sys.stdout.write(f"  view:         {html_path}\n")
        if prompt_path is not None:
            sys.stdout.write(f"  prompt:       {prompt_path}\n")

        if not args.no_open and html_path.exists() and launch_client:
            _open_browser(f"file://{html_path.absolute()}")

        sys.stdout.flush()

        if prompt_export_rc is not None:
            exit_code = 0 if prompt_export_rc == 0 else 1

    return exit_code


def _export_prompt_from_trace(trace_path: Path, output: str) -> int:
    from claude_tap.export import export

    out_path = None if output == "-" else Path(output).expanduser()
    if out_path is not None:
        out_path.parent.mkdir(parents=True, exist_ok=True)
    return export(trace_path, output=out_path, fmt="prompt-md")


def _target_allows_env_proxy(target: str) -> bool:
    host = urlparse(target).hostname
    if host is None:
        return True
    return host.lower() not in {"127.0.0.1", "::1", "localhost"}


def _path_relative_to(path: Path, base: Path) -> str | None:
    try:
        return str(path.resolve().relative_to(base.resolve()))
    except ValueError:
        return None


def _session_manifest_files(
    *,
    output_dir: Path,
    trace_path: Path,
    log_path: Path,
    html_path: Path | None,
    prompt_path: Path | None,
) -> list[str]:
    files: list[str] = []
    for path in (trace_path, log_path, html_path, prompt_path):
        if path is None:
            continue
        rel = _path_relative_to(path, output_dir)
        if rel is not None:
            files.append(rel)
    return files


async def _wait_forever() -> None:
    stop = asyncio.Event()

    def _stop() -> None:
        stop.set()

    loop = asyncio.get_running_loop()
    try:
        loop.add_signal_handler(signal.SIGINT, _stop)
        loop.add_signal_handler(signal.SIGTERM, _stop)
    except (NotImplementedError, OSError):
        pass
    await stop.wait()


def _open_browser(url: str) -> None:
    threading.Thread(target=lambda: webbrowser.open(url), daemon=True).start()


# ---------------------------------------------------------------------------
# live (history-only viewer)
# ---------------------------------------------------------------------------


def _run_live_only(args: argparse.Namespace) -> int:
    output_dir = Path(args.output_dir).expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)
    return asyncio.run(_run_live_only_async(args, output_dir))


async def _run_live_only_async(args: argparse.Namespace, output_dir: Path) -> int:
    # No live session is being recorded — we are just browsing history.
    server = LiveViewerServer(current_jsonl=None, port=args.port, host=args.host, output_dir=output_dir)
    await server.start()
    sys.stdout.write(f"[claude-tap] live viewer: {server.url}\n")
    sys.stdout.flush()
    if not args.no_open:
        _open_browser(server.url)

    try:
        await _wait_forever()
    finally:
        await server.stop()
    return 0


# ---------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------


def _run_export(args: argparse.Namespace) -> int:
    from claude_tap.export import export

    trace = args.trace
    output = Path(args.output) if args.output and args.output != "-" else None
    return export(
        Path(trace) if trace != "-" else Path("-"),
        output=output,
        fmt=args.fmt,
        full_events=getattr(args, "full_events", False),
    )


# ---------------------------------------------------------------------------
# update
# ---------------------------------------------------------------------------


def _run_update(args: argparse.Namespace) -> int:
    return asyncio.run(_run_update_async(args))


async def _run_update_async(args: argparse.Namespace) -> int:
    latest = await update_mod.latest_version()
    if latest is None:
        sys.stderr.write("[claude-tap] update check failed (no network or PyPI down)\n")
        return 1
    if not update_mod.is_newer(latest):
        sys.stdout.write(f"[claude-tap] up to date ({__version__})\n")
        return 0
    sys.stdout.write(f"[claude-tap] update available: {__version__} -> {latest}\n")
    if not args.install:
        sys.stdout.write("[claude-tap] run `claude-tap update --install` to upgrade\n")
        return 0
    return update_mod.run_upgrade()


# ---------------------------------------------------------------------------
# ca
# ---------------------------------------------------------------------------


def _run_ca(args: argparse.Namespace) -> int:
    action = args.ca_action or "path"
    if action == "path":
        cert_path, key_path = ensure_ca()
        sys.stdout.write(f"{cert_path}\n")
        if args.verbose:
            sys.stderr.write(f"key: {key_path}\n")
        return 0
    if action == "regen":
        d = data_dir()
        for name in ("ca.pem", "ca-key.pem"):
            p = d / name
            if p.exists():
                p.unlink()
        cert_path, _ = ensure_ca()
        sys.stdout.write(f"regenerated: {cert_path}\n")
        return 0
    if action == "install":
        cert_path, _ = ensure_ca()
        sys.stdout.write(f"CA certificate: {cert_path}\n\n")
        if sys.platform == "darwin":
            sys.stdout.write(
                "Add to System keychain (admin):\n"
                f"  sudo security add-trusted-cert -d -r trustRoot \\\n"
                f'    -k /Library/Keychains/System.keychain "{cert_path}"\n'
            )
        elif sys.platform.startswith("linux"):
            target = "/usr/local/share/ca-certificates/claude-tap.crt"
            sys.stdout.write(
                f'Trust on Debian/Ubuntu (admin):\n  sudo cp "{cert_path}" {target} && sudo update-ca-certificates\n'
            )
        else:  # pragma: no cover
            sys.stdout.write(
                "Import the certificate into your OS trust store. Most clients\n"
                "(claude / codex) read NODE_EXTRA_CA_CERTS=<path> instead.\n"
            )
        return 0
    sys.stderr.write(f"unknown ca action: {action}\n")
    return 2
