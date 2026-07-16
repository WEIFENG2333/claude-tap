# claude-tap — agent / contributor guide

This file is the developer-facing knowledge base for `claude-tap`. The
public README is for users; this file is for **coding agents and
contributors who need to extend, debug, or consume the proxy** without
prior context.

For workflow / review / commit policy, see [`AGENTS.md`](AGENTS.md).

---

## What is this?

A local HTTP proxy that intercepts the API traffic between an AI coding
CLI (Claude Code, Codex CLI, Codex App, Gemini CLI, Antigravity CLI,
Grok Build, Kimi CLI, Kimi Code, MiMo Code, OpenClaw, opencode, Pi,
Oh My Pi, iFlow, Cursor, Qoder, Devin, Hermes — 18 built in) and the LLM upstream,
captures every request/response (SSE streams reassembled, WebSocket
frames decoded), and renders the trace as a single self-contained HTML
file you can share.

Two transport modes:

* **Reverse proxy** — set the child's `*_BASE_URL` env (or CLI flag)
  to `http://127.0.0.1:<port>`. Cheap, no CA install needed.
* **Forward proxy** — set `HTTPS_PROXY` + `NODE_EXTRA_CA_CERTS` etc.
  on the child; do TLS-MITM with a per-host leaf cert minted by a
  local CA. Used when env redirect is unreliable (multi-backend
  clients) or impossible (rustls binaries like devin).

The **transparency contract**: never silently overwrite the user's
configured `base_url`. We read it from their CLI's own config and
forward to it verbatim.

---

## Repository layout

```
claude_tap/
├── cli.py              argparse entry; resolves target + mode; runs the pipeline
├── runner.py           spawns the child CLI under the proxy (signal handling, TTY)
├── clients.py          built-in Client instances; per-CLI launch + redirect knowledge
├── protocols.py        Protocol instances; per-upstream wire format
├── pipeline.py         transport-agnostic record builder (build_http_record / build_ws_record)
├── reverse_proxy.py    aiohttp web app; HTTP + WebSocket relay, SSE streaming
├── forward_proxy.py    raw asyncio CONNECT + per-host TLS termination
├── certs.py            local CA + per-host leaf cert minting
├── trace.py            EventBus + Sink protocol; JsonlSink / StatsSink / LiveSink
├── viewer.py           server-side HTML render (jsonl → standalone .html)
├── viewer.html         SPA template (HTML + CSS + JS, bundles marked.js)
├── live_viewer.py      aiohttp server: serves viewer.html + /api/* JSON + /api/stream SSE
├── sse.py              SSE parser + per-protocol reassemblers
├── manifest.py         trace-folder manifest + cleanup
├── paths.py            XDG paths for CA + state
├── logging_setup.py    centralised logger
├── update.py           PyPI update check
└── _version.py         importlib.metadata-based version

tests/
├── test_clients.py        per-client env_overrides / read_configured_upstream / detect_auth
├── test_protocols.py      protocol matching, path rewrites, usage extraction
├── test_pipeline.py       record builders, header redaction, decompression
├── test_sse.py            SSE parsing across chunk boundaries; reassembler state
├── test_cli_parsing.py    subcommand dispatch + resolve_target_and_mode + _resolve_live_default
├── test_manifest.py       trace cleanup + legacy .cloudtap-* migration
├── test_viewer.py         marker injection, lazy-mode threshold, </script> escape
├── test_export.py         markdown / json / html export shape
├── test_logging_setup.py  verbosity → level mapping
├── test_update.py         version compare + installer detection
├── test_e2e_reverse_proxy.py    end-to-end reverse-mode against a mock upstream
├── test_e2e_forward_proxy.py    end-to-end forward-mode TLS-MITM with CA
├── test_e2e_live_viewer.py      live SSE delivery to an HTTP client
└── test_e2e_cli.py        `python -m claude_tap …` end-to-end
```

The full test suite runs quickly and covers client registry behavior,
protocol matching, reverse proxy capture, forward proxy capture, prompt
export, and viewer rendering.

---

## Core abstraction: Client × Protocol

Two orthogonal axes, kept separate so adding either side is a
single-file change:

* **Protocol** (`protocols.py`) — one upstream API's wire format.
  Fields: `name`, `default_target`, `allowed_paths`, `is_streaming`,
  `rewrite_upstream_path`, `make_reassembler`, `extract_usage`.
  Concrete: `ANTHROPIC`, `OPENAI`, `GEMINI`, `ANTIGRAVITY`,
  `CODEX_APP`, `PASSTHROUGH`.
* **Client** (`clients.py`) — one CLI binary's launch metadata.
  Fields: `name`, `cmd`, `label`, `install_url`, `protocols` (1+),
  `env_overrides(proxy_url)`, `cli_args_overrides(proxy_url, env)`,
  `read_configured_upstream(env)`, `env_redirect_reliable: bool`,
  `pre_launch_env_purge`, `detect_auth()`.

A client may declare multiple protocols. `opencode` carries
`(ANTHROPIC, OPENAI, GEMINI, PASSTHROUGH)` so requests are routed
by path to the matching protocol's reassembler.

`PASSTHROUGH` is the fallback for proprietary RPC formats
(Cursor / Qoder / Devin) — we capture every byte but don't
reassemble a structured response snapshot.

---

## Two transports, one pipeline

`pipeline.py` is transport-agnostic; both proxies feed it the same
record builders (`build_http_record` / `build_ws_record`):

* **Reverse proxy** (`reverse_proxy.py`) — aiohttp web app. The child
  sends HTTP/WS to `127.0.0.1:<port>`; we forward each request to
  `ctx.target` (a fixed URL).
* **Forward proxy** (`forward_proxy.py`) — raw `asyncio.start_server`
  handling `CONNECT host:443`. Uses a temporary loopback TLS server
  to terminate TLS without `loop.start_tls` (which is unreliable on
  some Python builds — notably macOS 3.11). The CONNECT host is the
  real upstream — no static `--target` needed.

Records flow: `proxy.handle() → pipeline.build_*_record() → bus.publish() → all sinks`.

---

## Trace record schema

One JSON line per request:

```json
{
  "timestamp": "2026-05-06T12:01:37.410+00:00",
  "request_id": "req_c3b5776232ce",
  "turn": 1,
  "duration_ms": 1088,
  "transport": "websocket",          // optional; absent for plain HTTP
  "request": {
    "method": "POST",                // or "WEBSOCKET"
    "path": "/v1/messages?beta=true",
    "headers": { "Authorization": "Bearer sk-an...", "...": "..." },
    "body": { "model": "claude-haiku-4-5", "messages": [...] }
  },
  "response": {
    "status": 200,                   // 101 for WS upgrade
    "headers": { "content-type": "text/event-stream", "...": "..." },
    "body": {                        // reassembled snapshot (null for PASSTHROUGH)
      "id": "msg_xxx",
      "content": [{"type": "text", "text": "hello"}],
      "stop_reason": "end_turn",
      "usage": { "input_tokens": 352, "output_tokens": 15 }
    },
    "sse_events": [                  // raw event stream (HTTP path)
      {"event": "message_start", "data": {...}},
      {"event": "content_block_delta", "data": {...}}
    ],
    "ws_events": [...]               // raw event stream (WS path)
  },
  "upstream_base_url": "https://api.anthropic.com"
}
```

Notes for consumers:

* `body` (response) is the **reassembled snapshot** — equivalent to a
  non-streaming reply. Streaming chunks are in `sse_events` /
  `ws_events`.
* Sensitive headers (`Authorization`, `x-api-key`) are redacted to the
  first 12 characters at write time. Bodies are not redacted.
* `upstream_base_url` is the actual host hit (CONNECT host in forward
  mode, `--target` in reverse mode).
* `PASSTHROUGH` clients leave `body: null` because we don't reassemble
  their format; raw events still in `sse_events` / `ws_events`.

---

## Target & mode resolution

`cli.py: resolve_target_and_mode` is a pure function. Priority:

```
target = --target
       ?? client.read_configured_upstream(env)   ← reads user's actual base_url
       ?? auth.suggested_target                   ← derived from login state
       ?? protocol.default_target

mode   = --mode
       ?? "reverse" if client.env_redirect_reliable
       ?? "forward"
```

`env_redirect_reliable=False` is set on config-driven, multi-backend, or
TLS-hard clients such as `CODEX_APP_CLIENT`, `OPENCODE`, `PI`, `OMP`,
`KIMI`, `KIMI_CODE`, `MIMO`, `IFLOW`, `HERMES`, and `DEVIN`. For those
clients, config files or runtime behavior can override env vars, so
reverse mode would silently miss traffic and the resolver falls back to
forward.

---

## Configuration sources (where each CLI's base_url lives)

Verified against each CLI's actual source code. Used by
`Client.read_configured_upstream` to make `claude-tap` transparent.

| CLI       | Source for `base_url` (priority order)                            |
|-----------|-------------------------------------------------------------------|
| claude    | `ANTHROPIC_BASE_URL` env                                          |
| codex     | `~/.codex/config.toml` → `model_provider` → `model_providers.<X>.base_url` |
| codexapp  | app-server inherits `HTTPS_PROXY`; no reverse-mode config source |
| gemini    | `GOOGLE_GEMINI_BASE_URL` / `GOOGLE_VERTEX_BASE_URL` / `CODE_ASSIST_ENDPOINT` env |
| agy       | `CLOUD_CODE_URL` env |
| grok      | `GROK_XAI_API_BASE_URL` / `GROK_CLI_CHAT_PROXY_BASE_URL` env, then matching keys under `~/.grok/config.toml` → `endpoints` |
| opencode  | `~/.config/opencode/opencode.json` → `provider.<active>.options.baseURL` (active = first part of `model: <provider>/<id>`) |
| pi        | `~/.pi/agent/models.json` → `providers.<defaultProvider>.baseUrl` |
| omp       | `~/.omp/agent/models.json` → `providers.<defaultProvider>.baseUrl` |
| kimi      | `~/.kimi/config.toml` → `default_model` → `models.<m>.provider` → `providers.<p>.base_url` |
| kimi-code | `KIMI_CODE_BASE_URL` / `KIMI_BASE_URL` / `MOONSHOT_BASE_URL` env, then `~/.kimi-code/config.toml` |
| mimo      | `~/.config/mimocode/mimocode.json` → active provider `baseURL` / `baseUrl` / `base_url` |
| openclaw  | `~/.openclaw/openclaw.json` or `OPENCLAW_CONFIG_PATH` → active provider `baseUrl` |
| iflow     | `~/.iflow/settings.json` → `baseUrl` (settings wins over env)     |
| hermes    | `~/.hermes/config.yaml` → `model.base_url` (per-provider env override for OpenRouter) |
| cursor    | `CURSOR_API_BASE_URL` env                                         |
| qoder     | `QODER_CENTER_DOMAIN` env                                         |
| devin     | (not user-customizable; rustls binary)                            |

---

## Codex special cases

Codex (Rust binary) is the most idiosyncratic supported CLI; several
non-obvious rules apply.

### Auth priority

`~/.codex/auth.json` (`auth_mode`) wins over `OPENAI_API_KEY` env.
This matches codex's own internal priority. A stale env key would
otherwise pull a ChatGPT-OAuth user's traffic to `api.openai.com`,
which returns `401 Missing scopes: api.responses.write`.

### `OPENAI_BASE_URL` is ignored

Codex doesn't read `OPENAI_BASE_URL`. Built-in provider IDs
(`openai` / `azure` / `oss`) **cannot be redirected by overriding
`[model_providers.openai]`** — codex rejects same-named blocks as
reserved.

The redirect path (`_codex_cli_args` in `clients.py`):

* If the user has a custom `model_provider` with their own block,
  override its `base_url` and force `supports_websockets = false`
  via `-c model_providers.<active>.base_url=…` etc.
* Otherwise (built-in `openai`), define a sibling custom provider
  `claude-tap-openai` and switch to it. `requires_openai_auth = true`
  inherits ChatGPT OAuth or `OPENAI_API_KEY` cleanly.

### WebSocket transport

Codex defaults to a WebSocket transport for `/v1/responses`. WS keeps
**one long-lived connection across the whole session** and uses
`previous_response_id` so successive requests don't re-send conversation
history — bad for per-turn tracing.

`supports_websockets = false` on our custom provider forces HTTP+SSE.
This is now the default.

### Output items

OpenAI Responses API streams output items (messages, function_calls,
reasoning) as `response.output_item.done` events. **Codex's
`response.completed.response.output` is left empty** — items only
arrive via `output_item.done`. Both `OpenAIReassembler` (sse.py) and
`build_ws_record` (pipeline.py) accumulate the items and splice them
into the snapshot when the upstream-final list is empty.

### `chatgpt_base_url` is a red herring

`chatgpt_base_url` only redirects ChatGPT *web* endpoints (plugins /
connectors), not the LLM API. Use `openai_base_url` for capture.

---

## Per-protocol message + usage normalisation (viewer side)

The viewer (`viewer.html`) consumes records from all three upstreams.
Three quirks live in `getMessages()` and `getUsage()`:

### Messages

| Upstream         | Field                | Item shape                                      |
|------------------|----------------------|-------------------------------------------------|
| Anthropic        | `body.messages`      | `[{role, content[]}]` directly                  |
| OpenAI Responses | `body.input`         | mixed: `message` / `reasoning` / `function_call` / `function_call_output` |
| Google Gemini    | `body.contents`      | `[{role, parts:[{text|functionCall|functionResponse|inlineData}]}]` |

`getMessages()` converts each into the unified `{role, content[]}`
shape that `renderContent` understands. For OpenAI Responses, all
four item types map to message+content blocks (function_call →
`tool_use`, function_call_output → `tool_result`, reasoning →
`thinking`). For Gemini, `model` role becomes `assistant`,
`functionCall` → `tool_use`, etc.

### Usage tokens

| Upstream         | Field                                          |
|------------------|------------------------------------------------|
| Anthropic        | `response.body.usage` directly (input_tokens / output_tokens / cache_*) |
| OpenAI Responses | `response.body.usage` OR scan SSE for `response.completed.usage` |
| Google Gemini    | `response.body.usageMetadata` → `{promptTokenCount → input_tokens, candidatesTokenCount → output_tokens, cachedContentTokenCount → cache_read_input_tokens}` |

### System prompt

| Upstream         | Field                                          |
|------------------|------------------------------------------------|
| Anthropic        | `body.system` (string or text-block array)     |
| OpenAI Responses | `body.instructions` (string)                   |
| Google Gemini    | `body.systemInstruction.parts[*].text`         |

---

## viewer.html — the standalone SPA

`viewer.html` is **a single self-contained HTML file** (~4000 lines)
bundling its own CSS, JS, and the `marked.js` markdown parser. It's
used in two modes:

* **Live**: `live_viewer.py` serves it with an `LIVE_MODE = true`
  injection script + an SSE stream at `/api/stream`.
* **Static**: `viewer.py` injects `EMBEDDED_TRACE_DATA` (small traces)
  or `EMBEDDED_TRACE_META` + raw JSONL inside `<script type="text/plain">`
  (lazy mode, >50 records) and writes it next to the trace file.

### Top-level structure

```
viewer.html
├── <head>
│   ├── inline <style>     ~1000 lines of CSS
│   └── (no external deps; all assets self-contained)
└── <body>
    ├── header              logo + path filter chips + live status pill + lang/theme
    ├── main
    │   ├── #sidebar-wrap   session picker + search + turn list
    │   ├── #drop-zone      "drag & drop a .jsonl" placeholder (file-load mode)
    │   └── #detail         per-turn detail panel (sections)
    └── <script>
        ├── marked v13.0.3   bundled markdown parser
        └── application JS   ~3000 lines, ~130 functions
```

### Bootstrap modes (`bootCommon` + branch)

`<script>` checks four globals injected by the server / static export:

1. `LIVE_MODE = true` → `bootstrapLive()` (fetch `/api/sessions`, open SSE)
2. `EMBEDDED_TRACE_META.length > 0` → lazy mode (stub entries; full
   parse on demand from `<script type="text/plain" id="trace-raw">`)
3. `EMBEDDED_TRACE_DATA.length > 0` → small static export, fully inline
4. None → drop-zone for the user to load a .jsonl file

`bootCommon()` runs `initTheme(); initLang(); initGlobalSearch();
initToolFilterEvents(); initBackToTop()` for every mode.

### Detail panel sections

Each turn renders these sections (skipped when empty):

* **Action bar** — Request JSON / cURL / Diff with Prev buttons +
  per-turn token chips (right-aligned via `margin-left: auto`).
  *Not* sticky.
* **Tools** — request body's tool definitions (collapsible cards;
  recursive parameter rendering).
* **System Prompt** — raw / markdown toggle in section header.
* **Messages** — request conversation (mixed roles, tool_use,
  tool_result, thinking blocks).
* **Response** — reassembled response content.
* **SSE Events** — raw event stream.
* **Full JSON** — collapsible tree (Expand / Collapse / Wrap toolbar).

All section headers are `position: sticky; top: 0; z-index: 3` so
the user can collapse a long section from anywhere within it.

### Live SSE protocol (`/api/stream`)

Server emits three event types (`live_viewer.py: _handle_stream`):

* `event: hello` — initial frame, `{session, schema, server}`
* `event: record` — every published record, full JSON
* `event: heartbeat` — every 30s; keeps the proxy connection alive

Browser-side handler (`viewer.html: openLiveStream`):

* `hello` → set `liveCurrentSessionId`; if user hasn't picked a
  session, auto-load this one.
* `record` → if `viewingSessionId === liveCurrentSessionId`, push to
  `entries` and re-render (50ms debounce); else increment
  `pendingNewCount` and update the live-status pill.
* `heartbeat` → just refreshes `updateLiveStatus`.

### Path filter auto-tracking

`activePaths` (Set) gates which records make it into `filtered`. The
flag `userTouchedPathFilter` controls behavior:

* If false (default): `renderApp` re-derives `activePaths` from the
  current entries on every render. This way a brand-new path arriving
  via SSE in live mode auto-includes itself.
* If true (after the user clicks a chip): respect their selection,
  never auto-modify.

### Live default decision (`_resolve_live_default` in `cli.py`)

* `-L` / `--live` → on
* `--no-live` → off
* No flag, `run` subcommand, interactive TTY, no `CI` env → on
* Otherwise (proxy, piped, CI) → off

### Markdown rendering

`renderMarkdown(text)` calls bundled `marked.parse` (GFM: tables,
strikethrough, autolinks, fenced code with language hints). A
`marked.use({ renderer: { link } })` override forces all links to
`target="_blank" rel="noopener noreferrer"`.

Don't replace this with a hand-rolled parser. Use `marked` features
(extensions, custom renderers) when you need to extend.

### Full JSON tree

`renderJsonTree(value, depth)` returns nested `<details class="jdet">`.
Top two levels open by default; `setJsonExpansion(host, open)` toggles
all `details.jdet` at once. Commas live INSIDE each child div as
`<span class="jp">,</span>` so block layout doesn't drop them onto
their own line.

### Performance notes

* `LAZY_THRESHOLD = 50` — above this we virtualise the sidebar
  (`vsRenderVisible` etc.) and stub-load entries.
* `getKnownToolNames()` caches the tool-name set keyed by
  `entries.length + last request_id` — the tool filter would
  otherwise re-walk all entries on every keystroke.
* `selectEntry()` does an O(1) class swap (cached prev/next refs);
  the old `querySelectorAll('.sidebar-item').forEach` was painful
  with arrow-key navigation in long traces.
* `onSearch()` is debounced 120ms before triggering `applyFilter()`.

---

## Extending

### Add a new CLI

Edit `claude_tap/clients.py`:

```python
def _mycli_env(proxy_url: str) -> dict[str, str]:
    return {"MYCLI_BASE_URL": proxy_url}

def _mycli_configured(env: Mapping[str, str]) -> str | None:
    return _strip_url(env.get("MYCLI_BASE_URL"))

def _mycli_auth() -> AuthInfo:
    if os.environ.get("MYCLI_API_KEY"):
        return AuthInfo(
            logged_in=True, mode="apikey",
            detail="MYCLI_API_KEY env var",
            suggested_target="https://api.mycli.example.com",
        )
    return AuthInfo(logged_in=False, mode="unknown",
                    detail="not logged in (export MYCLI_API_KEY)")

MYCLI = Client(
    name="mycli",
    cmd="mycli",
    label="My CLI",
    install_url="https://github.com/me/mycli",
    protocols=(OPENAI,),
    env_overrides=_mycli_env,
    read_configured_upstream=_mycli_configured,
    detect_auth=_mycli_auth,
)
```

Add `MYCLI` to the `_REGISTRY` tuple at the bottom. Add a unit test
in `tests/test_clients.py`. Done — usable as `claude-tap mycli`.

If the CLI's config-file `baseURL` overrides env, set
`env_redirect_reliable=False` and let forward mode handle redirect.
If env vars don't redirect at all (codex case), implement
`cli_args_overrides(proxy_url, env)` to inject the right CLI flags.

### Add a new sink

Implement the `Sink` protocol from `trace.py`:

```python
class WebhookSink:
    def __init__(self, url: str) -> None:
        self.url = url
        self._session = aiohttp.ClientSession()

    async def handle(self, record: dict) -> None:
        await self._session.post(self.url, json=record)

    async def close(self) -> None:
        await self._session.close()
```

Wire it up in `cli.py` next to `JsonlSink` / `StatsSink`:

```python
bus.subscribe(WebhookSink(args.webhook_url))
```

### Add a new protocol

Add a reassembler in `sse.py` if the upstream's SSE shape isn't
already covered. Then in `protocols.py`:

```python
MYAPI = Protocol(
    name="myapi",
    default_target="https://api.myapi.example.com",
    allowed_paths=("/v1/chat",),
    make_reassembler=MyAPIReassembler,
    extract_usage=lambda body: Usage(...),
)
```

Add to `_REGISTRY` and reference from any client that speaks it.

### Add a viewer feature

* New section: add a render function, call `section(title, body, …)`
  in `renderDetail`.
* New section header control: pass `headerControls` to `section()`.
* New i18n key: add to `I18N.en` (other locales fall back). The
  `translate-i18n` skill will fill the rest.
* New filter / global state: declare at module top, reset in
  `bootstrapLive` / `loadFile` as appropriate.

---

## Conventions and pitfalls

These are real lessons from past mistakes — read them.

### Stay focused on what was asked

The user's #1 frustration with previous changes was scope creep —
fixing things that weren't asked for ("乱改") and adding lots of
explanatory comments. Default to the smallest change that solves the
asked-for problem. If you find adjacent issues, list them and ask.

### Don't rewrite a working component without a reason

A previous round added an `output_item.done` accumulator to fix
imagined codex tool-display issues — turned out the actual issue was
the request body's `tools[]` rendering, completely unrelated. Verify
**which** thing is broken before patching the wrong layer.

### Use libraries when sensible

The user explicitly asked us to stop reinventing wheels. Bundled
`marked.js` is the example: a 38KB inline replaces ~50 lines of fragile
regex. If you find another opportunity (diff, code highlight, JSON
schema, …) consider a small library before hand-rolling.

### Comments: only for non-obvious WHY

* Don't restate what code does (`// loop over items` above
  `for (const i of items)`).
* Don't add "what we just did" comments after a change.
* Do document non-obvious decisions: "codex leaves response.output
  empty, accumulate from output_item.done", "section needs no
  overflow:hidden so its sticky child works."

### Test surface

Before declaring "done":

* `uv run pytest tests/ -x --timeout=60` — the full suite should pass.
* `uv run ruff check .` — clean.
* `uv run ruff format --check .` — clean.
* If touching the viewer: launch `claude-tap live` and walk through
  with Playwright across **all three protocols** (Anthropic, Gemini,
  OpenAI Codex multi-turn). Take screenshots. The CSS gotchas
  (sticky / overflow / z-index) only show up in real browsers.

The repo has fixture sessions you can copy from any of these:

* `/tmp/cttest_real/traces/` — Anthropic (claude)
* `/tmp/cttest_real/traces_gemini/` — Gemini
* `/tmp/cttest_tool2/.traces/` — Codex with tool call
* `/tmp/cttest_render/.traces/` — Codex multi-turn

(These are scratch dirs from past test runs; recreate as needed.)

### Async lifetime

* Every `async def` running a long task must be cancellable. Don't
  swallow `asyncio.CancelledError`. Re-raise it from `except` blocks
  unless you're at the very top.
* `asyncio.CancelledError` is `BaseException` in 3.11+, NOT
  `Exception`. `except Exception` won't catch it. If you need both:
  `except (asyncio.CancelledError, Exception)`.

### Don't force-push to upstream main

Check `git remote -v` before pushing. In this workspace, the user's fork is
`fork` (`WEIFENG2333/claude-tap`) and the upstream project may also be present
as `origin`. Push feature branches to the fork unless the task explicitly says
otherwise.

### CSS sticky needs scrollable ancestor without overflow:hidden

If `position: sticky` doesn't work, look for `overflow: hidden` on a
parent. The fix for `.section-header` was to remove `overflow: hidden`
from `.section`. The scroll context for sticky in the detail panel is
`.detail` itself.

### Tests over fixtures

Don't commit test traces. The e2e tests build fixture data inline
(`tests/conftest.py: trace_dir` + `sample_anthropic_record`) and
spin up real aiohttp / asyncio servers in the test process.

---

## Development

```bash
uv sync --extra dev
uv run ruff check claude_tap tests
uv run ruff format --check claude_tap tests
uv run pytest tests/
```

Pre-commit hook (recommended):

```bash
git config core.hooksPath .githooks
```

Run the proxy locally during dev:

```bash
# spawn a CLI under it
uv run claude-tap claude

# proxy-only (point your own client at it)
uv run claude-tap proxy -p 8080

# render an existing trace as HTML
uv run claude-tap export ./.traces/.../trace_*.jsonl --format html
```

---

## Security notes

* `claude-tap` sees **everything** the child CLI sends, including OAuth
  tokens and API keys. Sensitive headers are redacted to the first 12
  characters at write time, but request bodies and trace output are
  never sanitized — don't share traces from production sessions
  without scrubbing.
* The local CA's private key (`ca-key.pem`, mode 0600) signs leaf
  certs the child trusts. Anyone who reads it can MITM the child
  process. Don't check it into git, don't share it.
* Forward mode requires the child to trust our CA. If a CLI ever
  validates an unrelated trust path (cert pinning, OS-only trust),
  the proxied connection will be refused — by design.

---

## Repo policy

For commit / PR / review rules, see [`AGENTS.md`](AGENTS.md).
TL;DR: every commit must pass `ruff check`, `ruff format --check`, and
`pytest tests/ -x --timeout=60`. One concern per commit. English in
code/comments/docs. Push branches and open PRs via `gh pr create`.

---

## Quick "where is X" reference

| I want to…                          | Look at…                                                |
|-------------------------------------|---------------------------------------------------------|
| add a new CLI                       | `claude_tap/clients.py` + `tests/test_clients.py`       |
| add a new upstream protocol         | `claude_tap/protocols.py` + `claude_tap/sse.py`         |
| change CLI flag parsing             | `claude_tap/cli.py: build_parser`                       |
| change target/mode resolution       | `claude_tap/cli.py: resolve_target_and_mode`            |
| change which records flow where     | `claude_tap/trace.py` (sinks)                           |
| change reverse-mode HTTP behavior   | `claude_tap/reverse_proxy.py`                           |
| change forward-mode TLS behavior    | `claude_tap/forward_proxy.py`                           |
| change record shape                 | `claude_tap/pipeline.py: build_*_record`                |
| change SSE/WS reassembly            | `claude_tap/sse.py: *Reassembler`                       |
| change live SSE protocol            | `claude_tap/live_viewer.py: _handle_stream`             |
| change static HTML render           | `claude_tap/viewer.py: render_html`                     |
| change UI                           | `claude_tap/viewer.html`                                |
| change dev install instructions     | `README.md`                                             |
| change agent / contributor docs     | this file                                               |
