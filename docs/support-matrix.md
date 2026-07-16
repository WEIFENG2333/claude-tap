---
owner: claude-tap-maintainers
last_reviewed: 2026-07-04
source_of_truth: claude_tap/clients.py
---

# Support Matrix

This document tracks the built-in clients that `claude-tap` knows how to
launch and route. The source of truth is `claude_tap/clients.py`; update this
file whenever a client, protocol, default mode, or verification status changes.

## Status Labels

- `Verified`: a real trace has been captured and rendered.
- `Prompt-export verified`: a real CLI emitted a prompt-bearing request in
  `--export-prompt` capture-only mode.
- `Wired`: the client path is implemented and unit-tested, but still needs
  real credentials, upstream availability, or product behavior checks before it
  should be called fully verified.

## Built-In Clients

| Client | Name | Default mode | Protocols | Upstream source | Status |
| --- | --- | --- | --- | --- | --- |
| Claude Code | `claude` | reverse | Anthropic | `ANTHROPIC_BASE_URL`, auth default | Verified |
| Codex CLI | `codex` | reverse | OpenAI | `~/.codex/config.toml`, auth default | Verified |
| Codex App | `codexapp` | forward | Codex App | app-server inherits proxy env | Verified |
| Gemini CLI | `gemini` | reverse | Gemini | Google base URL env vars | Verified |
| Grok Build | `grok` | reverse | OpenAI, passthrough | Grok endpoint env/config | Prompt-export verified |
| Antigravity CLI | `agy` | reverse | Antigravity | `CLOUD_CODE_URL` | Wired |
| OpenClaw | `openclaw` | reverse | Anthropic, OpenAI, Gemini, passthrough | patched OpenClaw config or provider env | Prompt-export verified |
| opencode | `opencode` | forward | Anthropic, OpenAI, Gemini, passthrough | opencode provider config | Verified |
| Kimi CLI | `kimi` | forward | Anthropic, OpenAI, Gemini, passthrough | Kimi TOML config | Prompt-export verified |
| Kimi Code | `kimi-code` | forward | Anthropic, OpenAI, Gemini, passthrough | Kimi Code env/config | Prompt-export verified |
| MiMo Code | `mimo` | forward | Anthropic, OpenAI, Gemini, passthrough | MiMo provider config | Prompt-export verified |
| Pi | `pi` | forward | Anthropic, OpenAI, Gemini, passthrough | Pi `models.json` | Prompt-export verified |
| Oh My Pi | `omp` | forward | Anthropic, OpenAI, Gemini, passthrough | OMP `models.json` | Prompt-export verified |
| Hermes Agent | `hermes` | forward | Anthropic, OpenAI, Gemini, passthrough | Hermes YAML config or provider env | Prompt-export verified |
| iFlow CLI | `iflow` | forward | Anthropic, OpenAI, Gemini, passthrough | iFlow settings/env | Verified |
| Cursor Agent | `cursor` | reverse | passthrough | `CURSOR_API_BASE_URL` | Wired |
| Qoder CLI | `qoder` | reverse | passthrough | `QODER_CENTER_DOMAIN` | Wired |
| Devin CLI | `devin` | forward | passthrough | fixed upstream/auth token | Wired |

Antigravity is intentionally listed as `Wired`: the latest CLI seen during
Phistory smoke testing started onboarding/setup calls but exited without a
prompt-bearing generation request. Keep this as a real-product behavior note,
not a compatibility hack in `claude-tap`.

## Routing Rules

Reverse mode constructs upstream URLs as:

```text
target + protocol.rewrite_upstream_path(incoming_path, target)
```

Forward mode handles `CONNECT host:443`, terminates TLS with the local CA, and
uses the CONNECT host as the upstream. It is the default for config-driven or
multi-backend CLIs where env-based base URL overrides are not reliable.

Codex CLI has one special reverse-mode rule: for ChatGPT OAuth targets, the
client emits `/v1/responses` while `https://chatgpt.com/backend-api/codex`
expects `/responses`. The OpenAI protocol strips `/v1` for non-OpenAI targets
and keeps it for `api.openai.com`.

## Automated Coverage

- `tests/test_clients.py`: client registry, auth detection, env overrides,
  config readers, yolo args, and generic fallback behavior.
- `tests/test_protocols.py`: protocol matching, allowed paths, streaming
  checks, usage extraction, and target-specific path rewrites.
- `tests/test_e2e_reverse_proxy.py`: reverse-mode HTTP/SSE and WebSocket
  capture against fake upstreams.
- `tests/test_e2e_forward_proxy.py`: forward-mode TLS-MITM capture against
  fake HTTPS upstreams.
- `tests/test_prompt_snapshot.py`: prompt-export normalization across
  Anthropic, OpenAI, Gemini, Antigravity, and Codex App shapes.

## Manual Checks

Use real CLIs when credentials are available:

```bash
# Reverse-mode client
uv run claude-tap run claude --no-open -- -p "Reply with OK"

# Forward-mode client
uv run claude-tap run opencode --no-open -- run "Reply with OK"

# Prompt snapshot only
uv run claude-tap run kimi-code --export-prompt kimi-code.prompt.md --no-open -- --prompt "Reply with OK"

# Grok Build via its official npm package
npm install -g @xai-official/grok
uv run claude-tap run grok --export-prompt grok.prompt.md --no-open -- --single "Reply with OK"
```

For a standalone proxy, launch the proxy first and point another process at it:

```bash
uv run claude-tap proxy --protocol openai --target https://api.openai.com --port 8080
OPENAI_BASE_URL=http://127.0.0.1:8080/v1 codex exec "Reply with OK"
```

## Adding Clients

When adding a new built-in client:

1. Add the `Client` in `claude_tap/clients.py`.
2. Add or reuse a `Protocol` in `claude_tap/protocols.py`.
3. Add client tests and, where practical, fake-upstream E2E coverage.
4. Verify a real trace or prompt export with the actual CLI.
5. Update `README.md`, `README_zh.md`, `CLAUDE.md`, this support matrix, and
   `CHANGELOG.md`.
