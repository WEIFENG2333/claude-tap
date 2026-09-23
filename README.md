# claude-tap

[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/github/license/WEIFENG2333/claude-tap.svg)](LICENSE)
[![Status](https://img.shields.io/badge/status-pre--release-orange.svg)](#install)

Trace what AI coding CLIs actually send to their model APIs.

`claude-tap` runs tools like Claude Code, Codex CLI, DeepSeek Harness, Gemini CLI, Grok Build,
MiniMax Code, Antigravity CLI, Kimi Code, MiMo Code, OpenClaw, opencode, Pi, and Oh My Pi
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
claude-tap dsh -- --profile headless "Say hi"
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

## Session-aware Viewer

The viewer separates a **capture** (one JSONL file) from a logical **session**
(one CLI conversation). The collapsible left panel groups requests by normalized
session identity without exposing raw ids. The center virtual list has one row
per LLM request. Its stable two-line activity preview keeps model prose and
direct tool calls in one predictable rhythm, with local time, model, duration,
and compact per-request input/output usage. Routine 2xx status and aggregate
session token totals stay out of the way. The right reading surface opens on
Messages and renders only the message sequence from that request body: one role
section per source item and one readable block per source content block,
preserving raw order, provider types, tool-call ids, and tool-result ids without
regrouping data from other requests. The System tab is one continuous prompt,
while Tools contains only the tool definitions mounted on that request and
formats their schemas across Anthropic, OpenAI Chat/Responses, Gemini, and Codex
namespace shapes. Each tab uses one primary vertical reading scroller. Request,
response, header, and full-trace JSON use a lazy folding tree with DeepSeek
Harness-compatible hover, copy, context-menu, and keyboard behavior rather than
whole-document truncation. Stream errors remain visible even when the HTTP
status is 200. The wide inspector is resizable, neighboring requests prefetch in
the background, and the viewer remembers its tab and scroll position when
folded or switched. On mobile, sessions become a drawer and request details
become a full-screen reading surface.

Thread, sub-agent, and turn identifiers are retained as optional hierarchy when
the client sends them; they are not required for grouping and remain available
in the raw request data.

Session adapters are based on observed wire traffic rather than a recursive
search for any field named `sessionId`:

| Client | Session signal | Optional hierarchy | Evidence |
| --- | --- | --- | --- |
| Claude Code | `X-Claude-Code-Session-Id` | `X-Claude-Code-Agent-Id`, `X-Claude-Code-Parent-Agent-Id` | capture-only run, 2.1.234 |
| Codex CLI | `x-codex-turn-metadata.session_id`, then `session-id` | thread, turn, window, parent thread | capture-only run, 0.144.1 |
| DeepSeek Harness | `X-DeepSeek-Harness-Session-Id` | — | capture-only run, 0.1.0-rc.7 |
| Grok Build | `X-Grok-Session-Id` | agent and turn index | capture-only run, 1.0.3 |
| Pi | `session_id` | `x-session-affinity` fallback | capture-only run, 0.72.1 |
| Hermes Agent | `session_id` | — | capture-only run, 0.0.0 agent build |
| opencode-compatible traffic | `X-Session-ID` | `X-Parent-Session-ID` | compatibility adapter; local bootstrap prevented a prompt capture |
| Gemini CLI | none in the model request | — | verified absent in a 0.40.1 capture-only run |

OpenAI Responses requests also use an explicit top-level `conversation` when
present and retain `previous_response_id` only as a link, never as an invented
session. Generic clients can use the allowlisted `x-session-id`, `session-id`,
`session_id`, `x-conversation-id`, or top-level body equivalents. If no safe
wire identity exists (including current Gemini CLI and older Claude Code), the
viewer labels the group as a capture-level fallback instead of guessing.

Live viewer schema v2 exposes indexed detail endpoints: it sends a metadata index
up front, reads full records by JSONL byte offset, and emits typed
`record.appended` events. Selected and nearby request bodies use bounded LRU
caching with in-flight request deduplication. This keeps large traces responsive
without loading the entire capture into browser memory.

## Export A Prompt Snapshot

For prompt-history tools, you usually do not need the whole viewer. Use
`--export-prompt` to write only the stable prompt surface:

```bash
claude-tap run claude --export-prompt claude.prompt.md --no-open -- -p hi
claude-tap run codex --export-prompt codex.prompt.md --no-open -- exec "hi"
claude-tap run dsh --export-prompt dsh.prompt.md --no-open -- --profile headless "hi"
claude-tap run gemini --export-prompt gemini.prompt.md --no-open -- -p hi
claude-tap run grok --export-prompt grok.prompt.md --no-open -- --single hi
claude-tap run agy --export-prompt antigravity.prompt.md --no-open -- --print hi --dangerously-skip-permissions
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

Capture-only responses use the client's wire protocol, including the CloudCode
response envelope for Antigravity. CloudCode requests marked
`requestType: "checkpoint"` remain in the raw trace but are excluded from prompt
snapshots. If only checkpoint requests were captured, prompt export fails.

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
| DeepSeek Harness | `claude-tap dsh` | forward | prompt-export verified |
| Gemini CLI | `claude-tap gemini` | reverse | verified |
| Grok Build | `claude-tap grok` | reverse | prompt-export verified |
| MiniMax Code | `claude-tap minimax-code` | reverse | prompt-export verified¹ |
| Antigravity CLI | `claude-tap agy` | forward | prompt-export verified |
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

¹ MiniMax Code currently ships as a desktop app rather than a public CLI.
This client targets a headless runtime launcher that honors
`MINIMAX_CODE_BASE_URL`; Phistory provides and validates that launcher.

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
| reverse | Claude Code, Codex, Gemini, Grok Build, MiniMax Code, OpenClaw, Cursor, Qoder | set a base URL, CLI flag, or temporary child config so the CLI calls `127.0.0.1` |
| forward | Codex App, DeepSeek Harness, Antigravity, opencode, Kimi, Kimi Code, MiMo, Pi, Oh My Pi, Hermes, iFlow, Devin | set `HTTPS_PROXY` and use a local CA to intercept HTTPS |

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

For architecture and maintenance guidance, see [`MAINTAINING.md`](MAINTAINING.md). Contribution
rules for coding agents are in [`AGENTS.md`](AGENTS.md).

## License

[MIT](LICENSE)
