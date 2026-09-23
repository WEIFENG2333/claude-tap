# Maintainer guide

`claude-tap` launches an AI client behind a local proxy, records API traffic,
and renders the resulting JSONL trace in a self-contained HTML viewer.

## Architecture

```text
client -> reverse or forward proxy -> upstream API
              |
              v
          record builder -> event bus -> JSONL / live viewer / statistics
```

- `clients.py` describes how products are configured and launched.
- `protocols.py` matches API paths, rewrites upstream paths, decodes streams,
  and extracts usage.
- `reverse_proxy.py` handles redirected HTTP, SSE, and WebSocket traffic.
- `forward_proxy.py` handles CONNECT and TLS interception for clients that
  cannot be reliably redirected.
- `pipeline.py` converts both transports into the same trace record format.
- `identity.py` normalizes vendor session, thread, agent, and turn signals.
- `catalog.py` maintains append-friendly JSONL byte-offset and metadata indexes.
- `viewer.py`, `viewer.html`, `viewer_assets/`, and `live_viewer.py` render saved
  and live traces through the same self-contained frontend.

A `Client` describes a product; a `Protocol` describes its upstream wire
format. Reuse protocols across clients and keep transport code independent
from response decoding.

Each JSONL line represents a completed HTTP or WebSocket exchange. Sensitive
authorization headers are redacted, but request and response bodies are not.
Treat all traces as sensitive.

New records carry a schema-versioned `trace_context`. Old records are
normalized at read time. Identity adapters must use explicit, allowlisted wire
fields; never recursively search request bodies for `sessionId`, because tool
payloads contain unrelated process and terminal session ids.

The live viewer API distinguishes capture ids from logical session keys. Keep
the metadata-index and single-record endpoints memory bounded. Frontend source
files stay modular under `viewer_assets/`; `load_viewer_template()` assembles
them into one document so exported traces remain portable.

`docs/VIEWER_DESIGN.md` documents the viewer's information hierarchy,
responsive concession rules, performance contract, and UX audit. Keep those
contracts in sync when changing the viewer.

## Development

```bash
uv sync --extra dev
uv run ruff check .
uv run ruff format --check .
uv run pytest tests/ -x --timeout=60
```

Keep changes focused and add regression coverage for fixes. Routing changes
need fake-upstream E2E coverage and should be checked against a real client
when credentials are available. Viewer interaction changes belong in
`tests/test_viewer.py` or `tests/test_viewer_scroll_browser.py`.

Generated traces, recordings, screenshots, caches, build output, and local
environments do not belong in git. Python 3.11 through 3.13 are supported.

## Adding a client

First observe a real run. Identify the executable, authentication variants,
API hosts and paths, transport, base URL or proxy settings, compression, and
bootstrap requests.

Then:

1. Add the client definition and configuration tests in `clients.py` and
   `tests/test_clients.py`.
2. Reuse a protocol or add narrowly scoped protocol behavior with tests.
3. Add fake-upstream E2E coverage for new routing or transport behavior.
4. Verify that a real generation request is recorded and rendered, rather
   than only model, identity, or bootstrap calls.
5. Update the client tables in both README files.

Verify the final upstream URL for every authentication/target combination and
never silently replace a user's configured upstream.
