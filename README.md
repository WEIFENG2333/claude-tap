# claude-tap

[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/github/license/WEIFENG2333/claude-tap.svg)](LICENSE)
[![Status](https://img.shields.io/badge/status-pre--release-orange.svg)](#install)

> Pre-release fork — not yet published to PyPI. Install from git
> (instructions below). The original `claude-tap` package on PyPI is
> a different (older) project and does not include this fork's
> rewrite.

> Local proxy that traces AI coding-agent CLI traffic — see exactly
> what Claude Code, Codex, Gemini CLI, opencode, and 7 other agents
> send to their upstreams, with the response reassembled for
> inspection.

`claude-tap` sits between your AI CLI and the LLM API, captures every
request/response (SSE streams reassembled, WebSocket frames decoded),
and renders the result as a single self-contained HTML trace you can
share. It is **transparent** — it reads your CLI's own config files
to learn the upstream URL, so a private relay or regional endpoint is
never silently overwritten.

```
┌─────────────┐   set BASE_URL env (reverse) or HTTPS_PROXY (forward)
│ claude /    │ ──────────────────────────────────────────────► ┌────────────┐
│ codex /     │                                                 │ claude-tap │
│ gemini /    │ ◄───── proxied response (chunks streamed) ──── │   proxy    │
│ opencode /  │                                                 └─────┬──────┘
│ ...         │                                                       │
└─────────────┘                                              forwards to
                                                          your real upstream
                                                          (read from your
                                                          CLI's own config)
```

> **Contributors / coding agents**: see [`CLAUDE.md`](CLAUDE.md) for
> architecture, the trace JSON schema, extension points, and developer
> setup.

---

## Install

### 1. Install `claude-tap`

Requires Python 3.11+ and `git`. This fork is not yet on PyPI; install
directly from the GitHub repo:

```bash
# Recommended — isolated venv, binary on PATH:
uv tool install git+https://github.com/WEIFENG2333/claude-tap.git

# Or via pipx:
pipx install git+https://github.com/WEIFENG2333/claude-tap.git

# Or plain pip (user site):
pip install git+https://github.com/WEIFENG2333/claude-tap.git
```

Verify:

```bash
claude-tap --version              # should print 0.x.x
```

If the binary isn't found, make sure `~/.local/bin` (or your platform's
pipx/uv tool dir) is on `PATH`, or run via
`uv tool run --from git+https://github.com/WEIFENG2333/claude-tap.git claude-tap …`.

**For local development** (clone + editable install, recommended if
you want to modify the code):

```bash
git clone https://github.com/WEIFENG2333/claude-tap.git
cd claude-tap
uv sync --extra dev
uv run claude-tap --version
```

### Upgrading

To pull the latest changes from this fork:

```bash
uv tool upgrade claude-tap        # if installed via uv tool
pipx upgrade claude-tap           # if installed via pipx
# Or reinstall:
uv tool install --force git+https://github.com/WEIFENG2333/claude-tap.git
```

The built-in `claude-tap update` command checks PyPI, so it won't be
useful until the fork is published there — for now, use git/uv/pipx
upgrade as above.

### 2. Install the CLI(s) you want to trace

`claude-tap` doesn't install your AI agent CLI — pick whichever you
already use, or install one from the table below. Always check the
linked official docs in case the install command has changed.

| CLI            | Install command                                       | Official docs |
|----------------|-------------------------------------------------------|---------------|
| Claude Code    | `curl -fsSL https://claude.ai/install.sh \| bash`     | [docs.anthropic.com](https://docs.anthropic.com/en/docs/claude-code) |
| Codex CLI      | `npm install -g @openai/codex`                        | [github.com/openai/codex](https://github.com/openai/codex) |
| Gemini CLI     | `npm install -g @google/gemini-cli`                   | [github.com/google-gemini/gemini-cli](https://github.com/google-gemini/gemini-cli) |
| opencode       | `npm install -g opencode-ai`                          | [opencode.ai](https://opencode.ai) |
| Pi             | `npm install -g @mariozechner/pi-coding-agent`        | [github.com/badlogic/pi-mono](https://github.com/badlogic/pi-mono) |
| Kimi CLI       | `uv tool install kimi-cli`                            | [github.com/MoonshotAI/kimi-cli](https://github.com/MoonshotAI/kimi-cli) |
| iFlow CLI      | `npm install -g @iflow-ai/iflow-cli`                  | [github.com/iflow-ai/iflow-cli](https://github.com/iflow-ai/iflow-cli) |
| Cursor Agent   | `curl -fsSL https://cursor.com/install \| bash`       | [cursor.com/cli](https://cursor.com/cli) |
| Qoder CLI      | `npm install -g @qoder-ai/qodercli`                   | [qoder.com/cli](https://qoder.com/cli) |
| Devin CLI      | follow the install script at the docs link            | [docs.devin.ai](https://docs.devin.ai/get-started/devin-intro) |
| Hermes Agent   | `pipx install hermes-agent`                           | [github.com/NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent) |

If the binary isn't found when you run `claude-tap <name>`, the error
message points to the right install page.

### 3. (Optional) Trust the local CA — only for forward-mode CLIs

The five multi-backend CLIs (`opencode` / `pi` / `kimi` / `iflow` /
`hermes`) and `devin` use forward mode, which terminates TLS using a
local CA `claude-tap` generates on first use. For Node and Python
clients, `claude-tap` injects `NODE_EXTRA_CA_CERTS` / `SSL_CERT_FILE`
/ `REQUESTS_CA_BUNDLE` automatically — nothing to do.

Only `devin` (rustls binary) requires installing the CA at the OS
level:

```bash
claude-tap ca install        # prints platform-specific instructions
```

Single-backend CLIs (`claude` / `codex` / `gemini` / `cursor` /
`qoder`) use reverse mode and never need the CA.

---

## Quick start

Run any supported CLI through `claude-tap` by prefixing it:

```bash
claude-tap claude -- -p "What is 2+2?"
```

That's it. The proxy launches, points `claude` at it, captures the API
calls, and on exit prints:

```
[claude-tap] summary:
  api_calls:    2
  tokens:       352 in / 15 out
  trace:        ./.traces/2026-05-06/trace_120137.jsonl
  log:          ./.traces/2026-05-06/trace_120137.log
  view:         ./.traces/2026-05-06/trace_120137.html
```

Open the `.html` file in a browser — the entire trace is
self-contained, shareable, no server required.

Add `-L` (or `--live`) to open a real-time viewer in the browser
**while** the CLI runs:

```bash
claude-tap -L claude -- -p "Explain async/await"
```

---

## Supported CLIs

| CLI            | Command       | Default mode | Auth source                            | Status |
|----------------|---------------|--------------|----------------------------------------|--------|
| Claude Code    | `claude`      | reverse      | `~/.claude/.credentials.json` / `ANTHROPIC_API_KEY` | ✅ verified |
| Codex CLI      | `codex`       | reverse      | `~/.codex/auth.json` / `OPENAI_API_KEY`     | ✅ verified |
| Gemini CLI     | `gemini`      | reverse      | `~/.gemini/oauth_creds.json` / `GEMINI_API_KEY` | ✅ verified |
| Cursor Agent   | `cursor-agent`| reverse      | `CURSOR_API_KEY`                       | ✅ wired |
| Qoder CLI      | `qodercli`    | reverse      | `QODER_ACCESS_TOKEN`                   | ✅ wired |
| Devin CLI      | `devin`       | forward      | `DEVIN_API_TOKEN`                      | ✅ wired (rustls — needs OS-trusted CA) |
| opencode       | `opencode`    | forward      | `~/.local/share/opencode/auth.json`    | ✅ verified |
| Pi             | `pi`          | forward      | `~/.pi/agent/models.json`              | ✅ wired |
| Kimi CLI       | `kimi`        | forward      | `~/.kimi/config.toml`                  | ✅ wired |
| iFlow CLI      | `iflow`       | forward      | `~/.iflow/settings.json`               | ✅ verified |
| Hermes Agent   | `hermes`      | forward      | `~/.hermes/config.yaml`                | ✅ wired |

"verified" = end-to-end tested with real API calls captured.
"wired" = code path implemented and unit-tested; needs the user's
credentials to validate the full loop.

You can use `claude-tap <name>` for any of them:
`claude-tap codex`, `claude-tap kimi`, `claude-tap opencode`, …

---

## How it works

The proxy runs in one of two modes; `claude-tap` picks for you, but
you can override with `-m reverse` or `-m forward`.

### Reverse mode (default — no CA install)

For **single-backend** CLIs whose env var or CLI flag we can rely on.
We:

1. Read your CLI's existing `base_url` from its config file or env.
2. Set `*_BASE_URL=http://127.0.0.1:<port>` (or `-c openai_base_url=…`
   for codex) so the CLI talks to us.
3. Forward each request to the URL we read in step 1, **preserving
   your private relay or regional endpoint exactly**.

This is what makes `claude-tap` transparent: if you have
`ANTHROPIC_BASE_URL=https://my-relay.example.com` already set,
`claude-tap` reads that and forwards there, instead of overwriting
your config and silently sending traffic to `api.anthropic.com`.

### Forward mode (HTTP CONNECT + TLS-MITM)

For **multi-backend** CLIs (opencode, Pi, Kimi, iFlow, Hermes) whose
config-file `baseURL` is honored over any env var we set.
Reverse-mode env redirect would silently fail for these, so we:

1. Set `HTTPS_PROXY=http://127.0.0.1:<port>` on the child.
2. Set `NODE_EXTRA_CA_CERTS` / `SSL_CERT_FILE` / `REQUESTS_CA_BUNDLE`
   so the child trusts our local CA.
3. Accept `CONNECT host:443`, terminate TLS using a per-host leaf
   cert minted from our CA, and forward to the *real* host the
   CONNECT named.

The CA is generated once on first use, lives in
`XDG_DATA_HOME/claude-tap/ca.pem`, and only needs OS-level trust if
you're tracing a rustls-based CLI (currently just `devin`).

---

## CLI reference

```
claude-tap [global] <command> [opts] [-- args forwarded to the client]

Commands
  run [client]    Trace a CLI and launch it under the proxy (default)
  proxy           Run the proxy alone, accept connections from any client
  live            Open the real-time viewer against an existing trace tree
  export FILE     Render a trace JSONL as markdown / json / prompt-md / html
  update          Check for, and optionally install, a new release
  ca {path,…}     Manage the local TLS CA used by forward mode

Global options
  -V, --version    Show version
  -v, --verbose    Increase verbosity (-vv = debug)
  -q, --quiet      Suppress non-error output
      --no-color   Disable ANSI colors (also honors NO_COLOR)

Run / proxy options
  -p, --port PORT      proxy port (default: auto)
  -H, --host HOST      bind address (default: 127.0.0.1 for run, 0.0.0.0 for proxy)
  -t, --target URL     upstream API URL (default: read from client config)
  -m, --mode MODE      reverse | forward (default: per-client)
  -o, --output-dir D   trace output directory (default: ./.traces)
      --max-traces N   keep last N sessions (default: 50; 0 = unlimited)
      --no-update-check
  -L, --live           also start a real-time viewer in the browser
      --live-port P    live viewer port (default: auto)
      --no-open        don't auto-open the HTML viewer on exit
```

`--` separates `claude-tap`'s own flags from arguments forwarded to
the launched CLI:

```bash
claude-tap claude -- --model claude-opus-4-7  # passes --model to claude
claude-tap codex -- exec "say hi"             # codex non-interactive
claude-tap gemini -- -p "explain async"       # gemini headless
```

If no subcommand is given, `run` is implied. `claude-tap` and
`claude-tap run` are the same; both default to `claude` as the client.

### Examples

```bash
# Trace your default CLI for a quick task
claude-tap claude -- -p "What is 2+2?"

# Trace once and export the captured system prompt / instructions / tools
claude-tap run gemini --export-prompt prompt.md --no-live --no-open -- -p "hi"

# Force forward mode (CA install required) for any CLI
claude-tap claude -m forward

# Override the upstream (e.g. point a Codex API-key user at a relay)
claude-tap codex -t https://my-relay.example.com/v1

# Standalone proxy (start the proxy, point external clients at it)
claude-tap proxy -p 8080
ANTHROPIC_BASE_URL=http://127.0.0.1:8080 claude

# Browse historic traces
claude-tap live

# Export a single trace to markdown / json / html
claude-tap export ./.traces/2026-05-06/trace_120137.jsonl -o report.md
claude-tap export ./.traces/2026-05-06/trace_120137.jsonl --format html

# Export the captured system prompt / instructions / tools as Markdown
claude-tap export ./.traces/2026-05-06/trace_120137.jsonl --format prompt-md -o prompt.md
claude-tap export ./.traces/2026-05-06/trace_120137.jsonl -o prompt.prompt.md
```

---

## Troubleshooting

**`claude-tap: command not found`** — `uv tool install` puts binaries
under `~/.local/bin`; make sure that's on your PATH, or use
`uv tool run claude-tap …`.

**Forward mode shows `unable to verify the first certificate`** — the
child process didn't pick up `NODE_EXTRA_CA_CERTS` / `SSL_CERT_FILE`.
Ensure you're not passing `--env-clear` somewhere, and that the CA
file at `claude-tap ca path` exists.

**Devin not captured** — Devin uses `rustls`, which ignores
`SSL_CERT_FILE` etc. Run `claude-tap ca install` and follow the
platform-specific instructions to add the CA to the OS trust store.

**Codex says "missing field name in model_providers.openai"** —
Built-in providers can't be overridden by adding a same-named block.
`claude-tap` 0.2.0+ uses `-c openai_base_url=…` for the built-in path
to avoid this. Upgrade if you see this error.

**API call captured but no body shown** — The viewer renders
`response.body` (the reassembled snapshot). For passthrough protocols
(Cursor / Qoder / Devin) we don't reassemble; check
`response.sse_events` for the raw stream.

---

## License

[MIT](LICENSE) — see `LICENSE` file for the full text.

For architecture, contributing, and development details, see
[`CLAUDE.md`](CLAUDE.md). For workflow and review policy, see
[`AGENTS.md`](AGENTS.md).
