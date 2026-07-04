"""End-to-end forward-proxy test against a local TLS upstream.

Forward mode is the transparency-preserving path: the client sends to
``HTTPS_PROXY``; we read the CONNECT host, terminate TLS with a CA-signed leaf
cert, and forward to *that* host — never to a fixed ``--target``.

This test pins down two contracts:

1. The proxy forwards based on the CONNECT host (so the user's
   ``base_url`` choice is preserved verbatim) — even when ``ProxyContext``
   carries a different ``target``.
2. The trace's ``upstream_base_url`` reflects the host actually hit, not the
   stale ``ProxyContext.target``.
"""

from __future__ import annotations

import asyncio
import gzip
import json
import socket
import ssl
from pathlib import Path

import aiohttp
import pytest
from aiohttp import web

from claude_tap.certs import CertificateAuthority, ensure_ca
from claude_tap.forward_proxy import ForwardProxyServer
from claude_tap.pipeline import ProxyContext
from claude_tap.protocols import ANTHROPIC, CODEX_APP, OPENAI
from claude_tap.trace import EventBus, JsonlSink

pytestmark = pytest.mark.asyncio


async def _start_https_upstream(ca: CertificateAuthority, hostname: str) -> tuple[web.AppRunner, int, list[dict]]:
    """Start a TLS upstream that records every request it sees."""
    received: list[dict] = []

    async def handler(request: web.Request) -> web.Response:
        body = await request.read()
        try:
            payload = json.loads(body)
        except Exception:
            payload = None
        received.append({"path": request.path, "host": request.host, "body": payload})
        return web.json_response(
            {
                "id": "msg_fwd",
                "type": "message",
                "model": "claude-test",
                "content": [{"type": "text", "text": "ok"}],
                "usage": {"input_tokens": 4, "output_tokens": 2},
            }
        )

    app = web.Application(client_max_size=0)
    app.router.add_route("POST", "/v1/messages", handler)

    cert_pem, key_pem = ca.get_host_pem(hostname)
    cert_path = Path(f"/tmp/_fwdtest_{hostname}.pem")
    key_path = Path(f"/tmp/_fwdtest_{hostname}.key")
    cert_path.write_bytes(cert_pem)
    key_path.write_bytes(key_pem)

    ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ssl_ctx.load_cert_chain(str(cert_path), str(key_path))

    runner = web.AppRunner(app)
    await runner.setup()
    # Bind on 127.0.0.1 — we'll trick the proxy into using ``hostname`` via
    # a custom resolver that maps it back to 127.0.0.1.
    site = web.TCPSite(runner, "127.0.0.1", 0, ssl_context=ssl_ctx)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]

    cert_path.unlink(missing_ok=True)
    key_path.unlink(missing_ok=True)

    return runner, port, received


async def _start_proxy(ctx: ProxyContext, ca: CertificateAuthority) -> tuple[ForwardProxyServer, int]:
    server = ForwardProxyServer("127.0.0.1", 0, ca, ctx)
    port = await server.start()
    return server, port


async def test_forward_proxy_uses_connect_host_not_ctx_target(trace_dir: Path):
    """The proxy must forward to the CONNECT host, not to ``ctx.target``.

    We pin a *different* (and unreachable) ``ctx.target`` to prove the proxy
    ignores it in forward mode.
    """
    cert_path, key_path = ensure_ca()
    ca = CertificateAuthority(cert_path, key_path)

    fake_host = "api.example.test"
    upstream_runner, upstream_port, upstream_log = await _start_https_upstream(ca, fake_host)

    bus = EventBus()
    jsonl_path = trace_dir / "trace.jsonl"
    bus.subscribe(JsonlSink(jsonl_path))

    # Custom resolver: map ``api.example.test`` to the upstream port on
    # 127.0.0.1, so the proxy's outbound HTTPS call lands on our test server.
    class FakeResolver(aiohttp.abc.AbstractResolver):
        async def resolve(self, host: str, port: int = 0, family: int = socket.AF_INET):
            if host == fake_host:
                return [
                    {
                        "hostname": host,
                        "host": "127.0.0.1",
                        "port": upstream_port,
                        "family": socket.AF_INET,
                        "proto": 0,
                        "flags": 0,
                    }
                ]
            return []

        async def close(self) -> None:
            pass

    # Trust our local CA for the outbound HTTPS call.
    out_ssl = ssl.create_default_context(cafile=str(cert_path))
    connector = aiohttp.TCPConnector(resolver=FakeResolver(), ssl=out_ssl)
    session = aiohttp.ClientSession(connector=connector, auto_decompress=False)

    # Intentionally pin ctx.target to an unreachable URL that does NOT match
    # the CONNECT host: forward mode must ignore this.
    ctx = ProxyContext(
        protocols=(ANTHROPIC,),
        target="https://wrong-target.invalid",
        bus=bus,
        session=session,
    )
    proxy, proxy_port = await _start_proxy(ctx, ca)

    try:
        # Client trusts our CA and routes through HTTPS_PROXY=proxy_port.
        client_ssl = ssl.create_default_context(cafile=str(cert_path))
        client_connector = aiohttp.TCPConnector(ssl=client_ssl)
        async with aiohttp.ClientSession(connector=client_connector) as client:
            async with client.post(
                f"https://{fake_host}/v1/messages",
                json={"model": "claude-test", "messages": [{"role": "user", "content": "hi"}]},
                headers={"x-api-key": "sk-fake-1234567890"},
                proxy=f"http://127.0.0.1:{proxy_port}",
            ) as resp:
                assert resp.status == 200
                data = await resp.json()
                assert data["id"] == "msg_fwd"
    finally:
        await session.close()
        await proxy.stop()
        await upstream_runner.cleanup()
        await bus.close_all()

    # Upstream really received the call -- proves we forwarded to the CONNECT
    # host, not to ctx.target.
    assert len(upstream_log) == 1
    assert upstream_log[0]["path"] == "/v1/messages"
    assert upstream_log[0]["body"]["model"] == "claude-test"

    # Trace records the actual upstream hit (CONNECT host), not ctx.target.
    lines = jsonl_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["upstream_base_url"] == f"https://{fake_host}:443"
    assert "wrong-target" not in record["upstream_base_url"]
    assert "..." in record["request"]["headers"]["x-api-key"]


async def test_forward_proxy_blocks_unknown_path(trace_dir: Path):
    """Paths outside the protocol allowlist must 404 inside the tunnel and
    leave no trace record."""
    cert_path, _ = ensure_ca()
    ca = CertificateAuthority(cert_path, ensure_ca()[1])

    bus = EventBus()
    jsonl_path = trace_dir / "trace.jsonl"
    bus.subscribe(JsonlSink(jsonl_path))

    session = aiohttp.ClientSession(auto_decompress=False)
    ctx = ProxyContext(
        protocols=(ANTHROPIC,),
        target="https://api.anthropic.com",
        bus=bus,
        session=session,
    )
    proxy, proxy_port = await _start_proxy(ctx, ca)

    try:
        client_ssl = ssl.create_default_context(cafile=str(cert_path))
        client_connector = aiohttp.TCPConnector(ssl=client_ssl)
        async with aiohttp.ClientSession(connector=client_connector) as client:
            try:
                async with client.get(
                    "https://api.anthropic.com/etc/passwd",
                    proxy=f"http://127.0.0.1:{proxy_port}",
                ) as resp:
                    assert resp.status == 404
            except aiohttp.ClientError:
                # Some aiohttp versions surface a non-2xx through the tunnel
                # as a connection error after the 404; either way no record
                # should be written -- that's the contract we care about.
                pass
    finally:
        await session.close()
        await proxy.stop()
        await bus.close_all()

    assert jsonl_path.read_text(encoding="utf-8").strip() == ""


async def test_forward_proxy_capture_only_records_without_upstream(trace_dir: Path):
    cert_path, key_path = ensure_ca()
    ca = CertificateAuthority(cert_path, key_path)

    class FailingSession:
        async def request(self, *args, **kwargs):
            raise AssertionError("capture-only must not call upstream")

    bus = EventBus()
    jsonl_path = trace_dir / "trace.jsonl"
    bus.subscribe(JsonlSink(jsonl_path))
    ctx = ProxyContext(
        protocols=(ANTHROPIC,),
        target="https://api.anthropic.com",
        bus=bus,
        session=FailingSession(),  # type: ignore[arg-type]
        capture_only=True,
    )
    proxy, proxy_port = await _start_proxy(ctx, ca)

    try:
        client_ssl = ssl.create_default_context(cafile=str(cert_path))
        client_connector = aiohttp.TCPConnector(ssl=client_ssl)
        async with aiohttp.ClientSession(connector=client_connector) as client:
            async with client.post(
                "https://api.anthropic.com/v1/messages",
                json={"model": "claude-test", "system": "system text", "messages": []},
                proxy=f"http://127.0.0.1:{proxy_port}",
            ) as resp:
                assert resp.status == 200
                data = await resp.json()
                assert data["id"] == "msg_claude_tap_capture"
    finally:
        await proxy.stop()
        await bus.close_all()

    lines = jsonl_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["request"]["body"]["system"] == "system text"
    assert record["response"]["body"]["id"] == "msg_claude_tap_capture"


async def test_forward_proxy_capture_only_streams_chat_completions(trace_dir: Path):
    cert_path, key_path = ensure_ca()
    ca = CertificateAuthority(cert_path, key_path)

    class FailingSession:
        async def request(self, *args, **kwargs):
            raise AssertionError("capture-only must not call upstream")

    bus = EventBus()
    jsonl_path = trace_dir / "trace.jsonl"
    bus.subscribe(JsonlSink(jsonl_path))
    ctx = ProxyContext(
        protocols=(OPENAI,),
        target="https://api.openai.com",
        bus=bus,
        session=FailingSession(),  # type: ignore[arg-type]
        capture_only=True,
    )
    proxy, proxy_port = await _start_proxy(ctx, ca)

    try:
        client_ssl = ssl.create_default_context(cafile=str(cert_path))
        client_connector = aiohttp.TCPConnector(ssl=client_ssl)
        async with aiohttp.ClientSession(connector=client_connector) as client:
            async with client.post(
                "https://api.openai.com/v1/chat/completions",
                json={"model": "gpt-test", "stream": True, "messages": [{"role": "system", "content": "system text"}]},
                proxy=f"http://127.0.0.1:{proxy_port}",
            ) as resp:
                assert resp.status == 200
                assert resp.headers["Content-Type"].startswith("text/event-stream")
                text = await resp.text()
                assert "chat.completion.chunk" in text
                assert "captured" in text
    finally:
        await proxy.stop()
        await bus.close_all()

    lines = jsonl_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["request"]["body"]["stream"] is True
    assert record["response"]["body"]["object"] == "chat.completion"
    assert record["response"]["sse_events"][-1]["data"] == "[DONE]"


async def test_forward_proxy_codexapp_relays_noise_and_records_responses(trace_dir: Path):
    cert_path, key_path = ensure_ca()
    ca = CertificateAuthority(cert_path, key_path)
    fake_host = "chatgpt.example.test"
    received: list[dict] = []

    async def handler(request: web.Request) -> web.StreamResponse:
        body = await request.read()
        received.append({"path": request.path, "body": body, "encoding": request.headers.get("Content-Encoding", "")})
        if request.path != "/backend-api/codex/responses":
            return web.json_response({"ok": True})

        resp = web.StreamResponse(
            status=200,
            headers={"Content-Type": "text/event-stream", "Cache-Control": "no-cache"},
        )
        await resp.prepare(request)
        for chunk in [
            b'event: response.created\ndata: {"type":"response.created","response":{"id":"resp_app","output":[]}}\n\n',
            b'event: response.output_item.done\ndata: {"type":"response.output_item.done","item":{"type":"function_call","name":"shell"}}\n\n',
            b'event: response.completed\ndata: {"type":"response.completed","response":{"id":"resp_app","output":[],"usage":{"input_tokens":7,"output_tokens":3}}}\n\n',
        ]:
            await resp.write(chunk)
        await resp.write_eof()
        return resp

    upstream_app = web.Application(client_max_size=0)
    upstream_app.router.add_route("*", "/{path_info:.*}", handler)

    cert_pem, key_pem = ca.get_host_pem(fake_host)
    cp = Path(f"/tmp/_fwdtest_{fake_host}.pem")
    kp = Path(f"/tmp/_fwdtest_{fake_host}.key")
    cp.write_bytes(cert_pem)
    kp.write_bytes(key_pem)
    ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ssl_ctx.load_cert_chain(str(cp), str(kp))
    cp.unlink(missing_ok=True)
    kp.unlink(missing_ok=True)

    runner = web.AppRunner(upstream_app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0, ssl_context=ssl_ctx)
    await site.start()
    upstream_port = site._server.sockets[0].getsockname()[1]

    class FakeResolver(aiohttp.abc.AbstractResolver):
        async def resolve(self, host: str, port: int = 0, family: int = socket.AF_INET):
            if host == fake_host:
                return [
                    {
                        "hostname": host,
                        "host": "127.0.0.1",
                        "port": upstream_port,
                        "family": socket.AF_INET,
                        "proto": 0,
                        "flags": 0,
                    }
                ]
            return []

        async def close(self) -> None:
            pass

    out_ssl = ssl.create_default_context(cafile=str(cert_path))
    connector = aiohttp.TCPConnector(resolver=FakeResolver(), ssl=out_ssl)
    session = aiohttp.ClientSession(connector=connector, auto_decompress=False)

    bus = EventBus()
    jsonl_path = trace_dir / "trace.jsonl"
    bus.subscribe(JsonlSink(jsonl_path))
    ctx = ProxyContext(protocols=(CODEX_APP,), target="https://chatgpt.com", bus=bus, session=session)
    proxy, proxy_port = await _start_proxy(ctx, ca)

    try:
        client_ssl = ssl.create_default_context(cafile=str(cert_path))
        client_connector = aiohttp.TCPConnector(ssl=client_ssl)
        async with aiohttp.ClientSession(connector=client_connector) as client:
            async with client.get(
                f"https://{fake_host}/backend-api/wham/remote/control/server",
                proxy=f"http://127.0.0.1:{proxy_port}",
            ) as resp:
                assert resp.status == 200
                assert await resp.json() == {"ok": True}

            payload = gzip.compress(b'{"model":"gpt-5.4-mini","stream":true,"input":[{"role":"user","content":"hi"}]}')
            async with client.post(
                f"https://{fake_host}/backend-api/codex/responses",
                data=payload,
                headers={"Content-Encoding": "gzip", "Content-Type": "application/json"},
                proxy=f"http://127.0.0.1:{proxy_port}",
            ) as resp:
                assert resp.status == 200
                assert "response.completed" in await resp.text()
    finally:
        await session.close()
        await proxy.stop()
        await runner.cleanup()
        await bus.close_all()

    assert [r["path"] for r in received] == [
        "/backend-api/wham/remote/control/server",
        "/backend-api/codex/responses",
    ]

    lines = jsonl_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["request"]["path"] == "/backend-api/codex/responses"
    assert record["request"]["body"]["model"] == "gpt-5.4-mini"
    assert record["response"]["body"]["id"] == "resp_app"
    assert record["response"]["body"]["output"][0]["name"] == "shell"
    assert len(record["response"]["sse_events"]) == 3


async def test_forward_proxy_streams_and_reassembles(trace_dir: Path):
    """SSE responses are forwarded chunk-by-chunk *and* reassembled into a
    snapshot recorded into the trace."""
    cert_path, key_path = ensure_ca()
    ca = CertificateAuthority(cert_path, key_path)

    fake_host = "stream.example.test"

    async def stream_handler(request: web.Request) -> web.StreamResponse:
        resp = web.StreamResponse(
            status=200,
            headers={"Content-Type": "text/event-stream", "Cache-Control": "no-cache"},
        )
        await resp.prepare(request)
        for c in [
            b'event: message_start\ndata: {"type":"message_start","message":{"id":"m_fwd","model":"x","content":[]}}\n\n',
            b'event: content_block_start\ndata: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}\n\n',
            b'event: content_block_delta\ndata: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"hi"}}\n\n',
            b'event: content_block_stop\ndata: {"type":"content_block_stop","index":0}\n\n',
            b'event: message_delta\ndata: {"type":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{"output_tokens":1}}\n\n',
            b'event: message_stop\ndata: {"type":"message_stop"}\n\n',
        ]:
            await resp.write(c)
            await asyncio.sleep(0)
        await resp.write_eof()
        return resp

    upstream_app = web.Application(client_max_size=0)
    upstream_app.router.add_route("POST", "/v1/messages", stream_handler)

    cert_pem, key_pem = ca.get_host_pem(fake_host)
    cp = Path(f"/tmp/_fwdtest_{fake_host}.pem")
    kp = Path(f"/tmp/_fwdtest_{fake_host}.key")
    cp.write_bytes(cert_pem)
    kp.write_bytes(key_pem)
    ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ssl_ctx.load_cert_chain(str(cp), str(kp))
    cp.unlink(missing_ok=True)
    kp.unlink(missing_ok=True)

    upstream_runner = web.AppRunner(upstream_app)
    await upstream_runner.setup()
    site = web.TCPSite(upstream_runner, "127.0.0.1", 0, ssl_context=ssl_ctx)
    await site.start()
    upstream_port = site._server.sockets[0].getsockname()[1]

    bus = EventBus()
    jsonl_path = trace_dir / "trace.jsonl"
    bus.subscribe(JsonlSink(jsonl_path))

    class FakeResolver(aiohttp.abc.AbstractResolver):
        async def resolve(self, host: str, port: int = 0, family: int = socket.AF_INET):
            if host == fake_host:
                return [
                    {
                        "hostname": host,
                        "host": "127.0.0.1",
                        "port": upstream_port,
                        "family": socket.AF_INET,
                        "proto": 0,
                        "flags": 0,
                    }
                ]
            return []

        async def close(self) -> None:
            pass

    out_ssl = ssl.create_default_context(cafile=str(cert_path))
    connector = aiohttp.TCPConnector(resolver=FakeResolver(), ssl=out_ssl)
    session = aiohttp.ClientSession(connector=connector, auto_decompress=False)
    ctx = ProxyContext(
        protocols=(ANTHROPIC,),
        target="https://wrong.invalid",
        bus=bus,
        session=session,
    )
    proxy, proxy_port = await _start_proxy(ctx, ca)

    try:
        client_ssl = ssl.create_default_context(cafile=str(cert_path))
        client_connector = aiohttp.TCPConnector(ssl=client_ssl)
        async with aiohttp.ClientSession(connector=client_connector) as client:
            async with client.post(
                f"https://{fake_host}/v1/messages",
                json={"model": "x", "stream": True, "messages": [{"role": "user", "content": "hi"}]},
                proxy=f"http://127.0.0.1:{proxy_port}",
            ) as resp:
                assert resp.status == 200
                streamed = await resp.read()
                assert b"hi" in streamed
    finally:
        await session.close()
        await proxy.stop()
        await upstream_runner.cleanup()
        await bus.close_all()

    record = json.loads(jsonl_path.read_text(encoding="utf-8").strip())
    snap = record["response"]["body"]
    assert snap["id"] == "m_fwd"
    assert snap["content"][0]["text"] == "hi"
    assert snap["stop_reason"] == "end_turn"
    assert any(ev["event"] == "message_start" for ev in record["response"]["sse_events"])
    assert record["upstream_base_url"] == f"https://{fake_host}:443"
