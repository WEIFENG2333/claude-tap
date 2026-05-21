"""End-to-end reverse-proxy test against a local mock upstream.

The flow exercised here is exactly what production runs: the proxy receives
an HTTP request, forwards it to a configurable upstream, records the
result through the EventBus, and the JsonlSink writes it to disk.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import aiohttp
import pytest
from aiohttp import web

from claude_tap.pipeline import ProxyContext
from claude_tap.protocols import ANTHROPIC
from claude_tap.reverse_proxy import build_app
from claude_tap.trace import EventBus, JsonlSink, StatsSink

pytestmark = pytest.mark.asyncio


async def _start(host: str, port: int, app: web.Application) -> tuple[web.AppRunner, int]:
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    actual = site._server.sockets[0].getsockname()[1]
    return runner, actual


async def _mock_anthropic_app() -> tuple[web.Application, list[dict]]:
    """A minimal upstream that records every request it sees."""
    received: list[dict] = []

    async def handler(request: web.Request) -> web.Response:
        body = await request.read()
        try:
            payload = json.loads(body)
        except Exception:
            payload = None
        received.append({"path": request.path, "headers": dict(request.headers), "body": payload})
        return web.json_response(
            {
                "id": "msg_e2e",
                "type": "message",
                "model": "claude-test",
                "content": [{"type": "text", "text": "ok"}],
                "usage": {"input_tokens": 5, "output_tokens": 2},
            }
        )

    app = web.Application(client_max_size=0)
    app.router.add_route("POST", "/v1/messages", handler)
    return app, received


async def test_reverse_proxy_records_buffered_request(trace_dir: Path):
    upstream_app, upstream_log = await _mock_anthropic_app()
    upstream_runner, upstream_port = await _start("127.0.0.1", 0, upstream_app)
    target = f"http://127.0.0.1:{upstream_port}"

    bus = EventBus()
    jsonl_path = trace_dir / "trace.jsonl"
    sink = JsonlSink(jsonl_path)
    stats = StatsSink((ANTHROPIC,))
    bus.subscribe(sink)
    bus.subscribe(stats)

    session = aiohttp.ClientSession(auto_decompress=False, trust_env=True)
    ctx = ProxyContext(protocols=(ANTHROPIC,), target=target, bus=bus, session=session)
    proxy_runner, proxy_port = await _start("127.0.0.1", 0, build_app(ctx))

    try:
        async with aiohttp.ClientSession() as client:
            async with client.post(
                f"http://127.0.0.1:{proxy_port}/v1/messages",
                json={"model": "claude-test", "messages": [{"role": "user", "content": "hi"}]},
                headers={"x-api-key": "sk-fake-1234567890"},
            ) as resp:
                assert resp.status == 200
                data = await resp.json()
                assert data["id"] == "msg_e2e"
    finally:
        await session.close()
        await proxy_runner.cleanup()
        await upstream_runner.cleanup()
        await bus.close_all()

    # Upstream really received the proxied call.
    assert len(upstream_log) == 1
    assert upstream_log[0]["path"] == "/v1/messages"
    assert upstream_log[0]["body"]["model"] == "claude-test"

    # Trace was written with redacted secrets.
    lines = jsonl_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["request"]["method"] == "POST"
    assert record["response"]["status"] == 200
    assert "..." in record["request"]["headers"]["x-api-key"]

    summary = stats.summary()
    assert summary["api_calls"] == 1
    assert summary["input_tokens"] == 5
    assert summary["output_tokens"] == 2


async def test_reverse_proxy_blocks_unknown_path(trace_dir: Path):
    bus = EventBus()
    sink = JsonlSink(trace_dir / "trace.jsonl")
    bus.subscribe(sink)
    session = aiohttp.ClientSession(auto_decompress=False)
    ctx = ProxyContext(
        protocols=(ANTHROPIC,),
        target="http://127.0.0.1:1",  # unreachable, intentional
        bus=bus,
        session=session,
    )
    proxy_runner, proxy_port = await _start("127.0.0.1", 0, build_app(ctx))

    try:
        async with aiohttp.ClientSession() as client:
            async with client.get(f"http://127.0.0.1:{proxy_port}/etc/passwd") as resp:
                assert resp.status == 404
    finally:
        await session.close()
        await proxy_runner.cleanup()
        await bus.close_all()

    # Blocked requests must not produce a trace entry.
    written = (trace_dir / "trace.jsonl").read_text(encoding="utf-8")
    assert written.strip() == ""


async def test_reverse_proxy_records_request_when_upstream_fails(trace_dir: Path):
    bus = EventBus()
    jsonl_path = trace_dir / "trace.jsonl"
    stats = StatsSink((ANTHROPIC,))
    bus.subscribe(JsonlSink(jsonl_path))
    bus.subscribe(stats)

    session = aiohttp.ClientSession(auto_decompress=False)
    ctx = ProxyContext(
        protocols=(ANTHROPIC,),
        target="http://127.0.0.1:1",  # unreachable, intentional
        bus=bus,
        session=session,
    )
    proxy_runner, proxy_port = await _start("127.0.0.1", 0, build_app(ctx))

    try:
        async with aiohttp.ClientSession() as client:
            async with client.post(
                f"http://127.0.0.1:{proxy_port}/v1/messages",
                json={"model": "claude-test", "messages": [{"role": "user", "content": "preserve me"}]},
            ) as resp:
                assert resp.status == 502
    finally:
        await session.close()
        await proxy_runner.cleanup()
        await bus.close_all()

    record = json.loads(jsonl_path.read_text(encoding="utf-8").strip())
    assert record["request"]["path"] == "/v1/messages"
    assert record["request"]["body"]["messages"][0]["content"] == "preserve me"
    assert record["response"]["status"] == 502
    assert "upstream" in record["response"]["body"]
    assert stats.summary()["api_calls"] == 1


async def test_reverse_proxy_streams_and_reassembles(trace_dir: Path):
    """SSE response is forwarded chunk-by-chunk *and* reassembled into a
    snapshot recorded into the trace."""

    async def stream_handler(request: web.Request) -> web.StreamResponse:
        resp = web.StreamResponse(
            status=200,
            headers={"Content-Type": "text/event-stream", "Cache-Control": "no-cache"},
        )
        await resp.prepare(request)
        chunks = [
            b'event: message_start\ndata: {"type":"message_start","message":{"id":"m_e2e","model":"x","content":[]}}\n\n',
            b'event: content_block_start\ndata: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}\n\n',
            b'event: content_block_delta\ndata: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"hello"}}\n\n',
            b'event: content_block_stop\ndata: {"type":"content_block_stop","index":0}\n\n',
            b'event: message_delta\ndata: {"type":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{"output_tokens":1}}\n\n',
            b'event: message_stop\ndata: {"type":"message_stop"}\n\n',
        ]
        for c in chunks:
            await resp.write(c)
            await asyncio.sleep(0)  # interleave event loop
        await resp.write_eof()
        return resp

    upstream_app = web.Application(client_max_size=0)
    upstream_app.router.add_route("POST", "/v1/messages", stream_handler)
    upstream_runner, upstream_port = await _start("127.0.0.1", 0, upstream_app)

    bus = EventBus()
    jsonl_path = trace_dir / "trace.jsonl"
    bus.subscribe(JsonlSink(jsonl_path))

    session = aiohttp.ClientSession(auto_decompress=False, trust_env=True)
    ctx = ProxyContext(
        protocols=(ANTHROPIC,),
        target=f"http://127.0.0.1:{upstream_port}",
        bus=bus,
        session=session,
    )
    proxy_runner, proxy_port = await _start("127.0.0.1", 0, build_app(ctx))

    try:
        async with aiohttp.ClientSession() as client:
            async with client.post(
                f"http://127.0.0.1:{proxy_port}/v1/messages",
                json={"model": "x", "stream": True, "messages": [{"role": "user", "content": "hi"}]},
            ) as resp:
                assert resp.status == 200
                streamed = await resp.read()
                assert b"hello" in streamed
    finally:
        await session.close()
        await proxy_runner.cleanup()
        await upstream_runner.cleanup()
        await bus.close_all()

    record = json.loads(jsonl_path.read_text(encoding="utf-8").strip())
    snap = record["response"]["body"]
    assert snap["id"] == "m_e2e"
    assert snap["content"][0]["text"] == "hello"
    assert snap["stop_reason"] == "end_turn"
    # SSE event stream is preserved alongside the snapshot.
    assert any(ev["event"] == "message_start" for ev in record["response"]["sse_events"])
