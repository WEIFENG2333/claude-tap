# claude-tap

[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/github/license/WEIFENG2333/claude-tap.svg)](LICENSE)
[![Status](https://img.shields.io/badge/status-pre--release-orange.svg)](#install)

Trace what AI coding CLIs actually send to their model APIs.

`claude-tap` runs tools like Claude Code, Codex CLI, Gemini CLI, Grok Build,
Antigravity CLI, Kimi Code, MiMo Code, OpenClaw, opencode, Pi, and Oh My Pi
through a local proxy. It records requests, streaming responses, tools, token
usage, and system prompts, then renders the run as a self-contained HTML trace.

Use it when you want to answer questions like:

- What system prompt did this CLI send?
- Which tools were exposed to the model?
- Did it call Anthropic, OpenAI, Gemini, or a custom relay?
- What changed between two CLI versions?
- What exactly happened during a coding-agent run?

This fork is currently installed from GitHub. The PyPI `claude-tap` package is
an older project and does not include this rewrite yet.

## Install

Requires Python 3.11+ and `git`.

```bash
uv tool install git+https://github.com/WEIFENG2333/claude-tap.git@main
```

`@main` installs the newest commit on this fork's `main` branch at that time.
Installed tools do not update automatically.

Verify:

```bash
claude-tap --version
```

If `claude-tap` is not found, make sure your uv tool bin directory is on
`PATH`. You can also run it directly from GitHub:

```bash
uv tool run --from git+https://github.com/WEIFENG2333/claude-tap.git@main claude-tap --version
```

Upgrade this fork with:

```bash
uv tool upgrade claude-tap
```

## Quick Start

Prefix the AI CLI command with `claude-tap`:

```bash
claude-tap claude -- -p "What is 2+2?"
claude-tap codex -- exec "Say hi"
claude-tap gemini -- -p "Explain async/await"
claude-tap grok -- --single "Explain async/await"
claude-tap kimi-code -- --prompt "Say hi"
```

Use any client name from the support table below. Arguments after `--` are
passed to that CLI unchanged.

After the CLI exits, `claude-tap` prints paths like:

```text
[claude-tap] summary:
  api_calls:    2
  tokens:       352 in / 15 out
  trace:        ./.traces/2026-05-06/trace_120137.jsonl
  log:          ./.traces/2026-05-06/trace_120137.log
  view:         ./.traces/2026-05-06/trace_120137.html
```

Open the HTML file to inspect the full run. No server is needed.

Use `-L` to open a live viewer while the CLI is still running:

```bash
claude-tap -L claude -- -p "Explain async/await"
```

## Export A Prompt Snapshot

For prompt-history tools, you usually do not need the whole viewer. Use
`--export-prompt` to write only the stable prompt surface:

```bash
claude-tap run claude --export-prompt claude.prompt.md --no-open -- -p hi
claude-tap run codex --export-prompt codex.prompt.md --no-open -- exec "hi"
claude-tap run gemini --export-prompt gemini.prompt.md --no-open -- -p hi
claude-tap run grok --export-prompt grok.prompt.md --no-open -- --single hi
claude-tap run kimi-code --export-prompt kimi-code.prompt.md --no-open -- --prompt hi
claude-tap run omp --export-prompt omp.prompt.md --no-open -- --print --mode text --no-session hi
```

For CLIs with their own subcommands, pass the client arguments after `--`:

```bash
claude-tap run openclaw --export-prompt openclaw.prompt.md --no-open -- agent --local --message hi --json
```

When prompt export succeeds, `claude-tap` treats the run as a successful
capture even if the child CLI exits non-zero after the request is captured.
This is useful for automation that only cares about the prompt, such as
versioned prompt archives.

You can also export a prompt snapshot from an existing trace:

```bash
claude-tap export ./.traces/2026-05-06/trace_120137.jsonl --format prompt-md -o prompt.md
```

## Supported CLIs

Install the AI CLI you want to trace first. `claude-tap` launches and proxies
CLIs; it does not install those CLIs for you.

| CLI | Command | Default mode | Status |
| --- | --- | --- | --- |
| Claude Code | `claude-tap claude` | reverse | verified |
| Codex CLI | `claude-tap codex` | reverse | verified |
| Codex App | `claude-tap codexapp` | forward | verified |
| Gemini CLI | `claude-tap gemini` | reverse | verified |
| Grok Build | `claude-tap grok` | reverse | prompt-export verified |
| Antigravity CLI | `claude-tap agy` | reverse | wired |
| Kimi Code | `claude-tap kimi-code` | forward | prompt-export verified |
| MiMo Code | `claude-tap mimo` | forward | prompt-export verified |
| OpenClaw | `claude-tap openclaw` | reverse | prompt-export verified |
| opencode | `claude-tap opencode` | forward | verified |
| Kimi CLI | `claude-tap kimi` | forward | prompt-export verified |
| Pi | `claude-tap pi` | forward | prompt-export verified |
| Oh My Pi | `claude-tap omp` | forward | prompt-export verified |
| Hermes Agent | `claude-tap hermes` | forward | prompt-export verified |
| iFlow CLI | `claude-tap iflow` | forward | verified |
| Cursor Agent | `claude-tap cursor` | reverse | wired |
| Qoder CLI | `claude-tap qoder` | reverse | wired |
| Devin CLI | `claude-tap devin` | forward | wired |

`verified` means a real trace has been captured. `prompt-export verified`
means a real CLI emitted a prompt-bearing request in capture-only mode.
`wired` means the client path is implemented and unit-tested, but may still
need user credentials or upstream behavior checks for a full trace.

## How It Works

`claude-tap` starts a local proxy, launches the selected CLI as a child
process, and points that child process at the proxy.

It uses two interception modes:

| Mode | Used for | How |
| --- | --- | --- |
| reverse | Claude Code, Codex, Gemini, Grok Build, Antigravity, OpenClaw, Cursor, Qoder | set a base URL, CLI flag, or temporary child config so the CLI calls `127.0.0.1` |
| forward | Codex App, opencode, Kimi, Kimi Code, MiMo, Pi, Oh My Pi, Hermes, iFlow, Devin | set `HTTPS_PROXY` and use a local CA to intercept HTTPS |

In both modes, `claude-tap` tries to preserve your real upstream. If your CLI
already uses a private relay or regional endpoint, `claude-tap` forwards there
instead of silently replacing it with the vendor default.

Forward mode generates a local CA on first use. Node and Python clients usually
trust it automatically through injected environment variables. If a CLI uses a
TLS stack that ignores those variables, run:

```bash
claude-tap ca install
```

## Common Commands

```bash
# Trace a normal CLI run
claude-tap claude -- -p "What is 2+2?"

# Keep the browser closed after the run
claude-tap claude --no-open -- -p "hi"

# Override the real upstream, for example a private relay
claude-tap codex -t https://my-relay.example.com/v1

# Start only the proxy, then point another process at it
claude-tap proxy -p 8080
ANTHROPIC_BASE_URL=http://127.0.0.1:8080 claude

# Open the trace browser for previous runs
claude-tap live

# Export a trace to Markdown, JSON, or HTML
claude-tap export ./.traces/2026-05-06/trace_120137.jsonl -o report.md
claude-tap export ./.traces/2026-05-06/trace_120137.jsonl --format html
```

Use `claude-tap --help` or `claude-tap run --help` for the full CLI reference.

## Safety Notes

`claude-tap` records what the child CLI sends and receives. Traces can include
prompts, file paths, tool results, tokens, and provider metadata. Review traces
before sharing them publicly.

The proxy is local by default. `run` binds to `127.0.0.1`; `proxy` can be bound
to another host with `--host` when you explicitly need that.

## Development

```bash
git clone https://github.com/WEIFENG2333/claude-tap.git
cd claude-tap
uv sync --extra dev
uv run claude-tap --version
```

For architecture, contributing, and coding-agent guidance, see
[`CLAUDE.md`](CLAUDE.md) and [`AGENTS.md`](AGENTS.md).

## License

[MIT](LICENSE)
