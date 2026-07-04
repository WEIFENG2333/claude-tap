"""HTTP reverse proxy implementation (aiohttp web app).

The proxy is protocol-agnostic: every incoming request is routed to the
:class:`Protocol` whose ``allowed_paths`` matches the request path. The
matching protocol then provides streaming detection and the SSE
reassembler. A single proxy instance therefore supports multiple upstream
protocols at once (used by multi-protocol clients like opencode).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid

import aiohttp
from aiohttp import web
from aiohttp.helpers import get_env_proxy_for_url
from yarl import URL

from claude_tap.pipeline import (
    HOP_BY_HOP,
    ProxyContext,
    build_upstream_url,
    capture_only_response,
    capture_only_stream_response,
    filter_headers,
    maybe_decompress,
    parse_json_body,
    reassemble_event_stream_body,
)
from claude_tap.pipeline import (
    build_http_record as _build_http_record,
)
from claude_tap.pipeline import (
    build_ws_record as _build_ws_record,
)
from claude_tap.protocols import Protocol

log = logging.getLogger("claude_tap")

CTX_KEY: web.AppKey[ProxyContext] = web.AppKey("ctx", ProxyContext)

_WS_HANDSHAKE_HEADERS = frozenset(
    {
        "sec-websocket-key",
        "sec-websocket-version",
        "sec-websocket-extensions",
        "sec-websocket-protocol",
        "sec-websocket-accept",
    }
)


def build_app(ctx: ProxyContext) -> web.Application:
    app = web.Application(client_max_size=0)
    app[CTX_KEY] = ctx
    app.router.add_route("*", "/{path_info:.*}", _dispatch)
    return app


async def _dispatch(request: web.Request) -> web.StreamResponse:
    ctx: ProxyContext = request.app[CTX_KEY]

    protocol = ctx.protocol_for(request.path)
    if protocol is None:
        log.debug("blocked non-API path: %s %s", request.method, request.path)
        return web.Response(status=404, text="Not Found")

    if request.headers.get("Upgrade", "").lower() == "websocket":
        return await _handle_websocket(request, ctx, protocol)

    return await _handle_http(request, ctx, protocol)


async def _handle_http(
    request: web.Request,
    ctx: ProxyContext,
    protocol: Protocol,
) -> web.StreamResponse:
    upstream_url = build_upstream_url(ctx.target, request.path_qs, protocol)
    body = await request.read()

    fwd_headers = filter_headers(request.headers)
    fwd_headers.pop("Host", None)
    if request.headers.get("Content-Encoding", "").lower() in ("zstd", "gzip", "deflate", "br"):
        for key in list(fwd_headers):
            if key.lower() in ("content-encoding", "content-length"):
                del fwd_headers[key]
    fwd_headers["Accept-Encoding"] = "identity"

    request_id = f"req_{uuid.uuid4().hex[:12]}"
    t0 = time.monotonic()
    should_capture = protocol.captures(request.method, request.path_qs)
    turn = ctx.next_turn() if should_capture else 0
    decoded_req_body = maybe_decompress(body, request.headers.get("Content-Encoding", ""))
    req_body = parse_json_body(decoded_req_body)
    streaming = protocol.is_streaming(request.path_qs, req_body)
    model = req_body.get("model", "") if isinstance(req_body, dict) else ""
    if should_capture:
        log.info(
            "[Turn %d] -> %s %s (%s) model=%s stream=%s upstream=%s",
            turn,
            request.method,
            request.path,
            protocol.name,
            model,
            streaming,
            upstream_url,
        )
    else:
        log.debug("relay-only -> %s %s (%s) upstream=%s", request.method, request.path, protocol.name, upstream_url)

    if ctx.capture_only and should_capture:
        stream_response = capture_only_stream_response(protocol, request.path_qs, req_body) if streaming else None
        if stream_response:
            resp_body, sse_events, body_bytes = stream_response
            resp_headers = {"Content-Type": "text/event-stream"}
        else:
            resp_body = capture_only_response(protocol, request.path_qs, req_body)
            sse_events = None
            body_bytes = b""
            resp_headers = {"Content-Type": "application/json"}
        duration_ms = int((time.monotonic() - t0) * 1000)
        record = _build_http_record(
            request_id=request_id,
            turn=turn,
            duration_ms=duration_ms,
            method=request.method,
            path=request.path_qs,
            req_headers=request.headers,
            req_body=req_body,
            status=200,
            resp_headers=resp_headers,
            resp_body=resp_body,
            sse_events=sse_events,
            upstream_base_url=ctx.target,
        )
        await ctx.bus.publish(record)
        log.info("[Turn %d] capture-only response returned; upstream skipped", turn)
        if stream_response:
            return web.Response(body=body_bytes, content_type="text/event-stream")
        return web.json_response(resp_body)

    try:
        upstream_resp = await ctx.session.request(
            method=request.method,
            url=upstream_url,
            headers=fwd_headers,
            data=body,
            timeout=aiohttp.ClientTimeout(total=600, sock_read=300),
        )
    except Exception as exc:
        if should_capture:
            duration_ms = int((time.monotonic() - t0) * 1000)
            record = _build_http_record(
                request_id=request_id,
                turn=turn,
                duration_ms=duration_ms,
                method=request.method,
                path=request.path_qs,
                req_headers=request.headers,
                req_body=req_body,
                status=502,
                resp_headers={},
                resp_body={"error": str(exc), "upstream": upstream_url},
                upstream_base_url=ctx.target,
            )
            await ctx.bus.publish(record)
        log.error("[Turn %d] upstream error %s: %s", turn, upstream_url, exc)
        return web.Response(status=502, text=str(exc))

    if streaming and upstream_resp.status == 200:
        return await _proxy_stream(
            request, upstream_resp, ctx, protocol, request_id, turn, t0, req_body, should_capture
        )
    return await _proxy_buffered(request, upstream_resp, ctx, protocol, request_id, turn, t0, req_body, should_capture)


async def _proxy_stream(
    request: web.Request,
    upstream_resp: aiohttp.ClientResponse,
    ctx: ProxyContext,
    protocol: Protocol,
    request_id: str,
    turn: int,
    t0: float,
    req_body: object,
    should_capture: bool,
) -> web.StreamResponse:
    resp = web.StreamResponse(
        status=upstream_resp.status,
        headers={k: v for k, v in upstream_resp.headers.items() if k.lower() not in HOP_BY_HOP},
    )
    await resp.prepare(request)

    reassembler = protocol.make_reassembler() if should_capture else None

    try:
        async for chunk in upstream_resp.content.iter_any():
            await resp.write(chunk)
            if reassembler is not None:
                reassembler.feed_bytes(chunk)
    except (ConnectionError, asyncio.CancelledError):
        pass
    try:
        await resp.write_eof()
    except Exception:
        pass

    if reassembler is not None:
        duration_ms = int((time.monotonic() - t0) * 1000)
        snapshot = reassembler.reconstruct()
        record = _build_http_record(
            request_id=request_id,
            turn=turn,
            duration_ms=duration_ms,
            method=request.method,
            path=request.path_qs,
            req_headers=request.headers,
            req_body=req_body,
            status=upstream_resp.status,
            resp_headers=upstream_resp.headers,
            resp_body=snapshot,
            sse_events=reassembler.events,
            upstream_base_url=ctx.target,
        )
        await ctx.bus.publish(record)
    return resp


async def _proxy_buffered(
    request: web.Request,
    upstream_resp: aiohttp.ClientResponse,
    ctx: ProxyContext,
    protocol: Protocol,
    request_id: str,
    turn: int,
    t0: float,
    req_body: object,
    should_capture: bool,
) -> web.Response:
    resp_bytes = await upstream_resp.read()
    if should_capture:
        duration_ms = int((time.monotonic() - t0) * 1000)
        decoded = maybe_decompress(resp_bytes, upstream_resp.headers.get("Content-Encoding", ""))
        content_type = upstream_resp.headers.get("Content-Type", "")
        if "text/event-stream" in content_type.lower():
            resp_body, sse_events = reassemble_event_stream_body(protocol, decoded)
        else:
            resp_body = parse_json_body(decoded)
            sse_events = None

        record = _build_http_record(
            request_id=request_id,
            turn=turn,
            duration_ms=duration_ms,
            method=request.method,
            path=request.path_qs,
            req_headers=request.headers,
            req_body=req_body,
            status=upstream_resp.status,
            resp_headers=upstream_resp.headers,
            resp_body=resp_body,
            sse_events=sse_events,
            upstream_base_url=ctx.target,
        )
        await ctx.bus.publish(record)

    return web.Response(
        status=upstream_resp.status,
        headers={k: v for k, v in upstream_resp.headers.items() if k.lower() not in HOP_BY_HOP},
        body=resp_bytes,
    )


# ---------------------------------------------------------------------------
# WebSocket relay
# ---------------------------------------------------------------------------


def _resolve_ws_proxy(ws_url: str) -> tuple[URL, aiohttp.BasicAuth | None] | None:
    if ws_url.startswith("wss://"):
        lookup = URL("https://" + ws_url[6:])
    elif ws_url.startswith("ws://"):
        lookup = URL("http://" + ws_url[5:])
    else:
        return None
    try:
        return get_env_proxy_for_url(lookup)
    except LookupError:
        return None


async def _handle_websocket(
    request: web.Request,
    ctx: ProxyContext,
    protocol: Protocol,
) -> web.StreamResponse:
    upstream_http = build_upstream_url(ctx.target, request.path_qs, protocol)
    if upstream_http.startswith("https://"):
        upstream_ws = "wss://" + upstream_http[8:]
    elif upstream_http.startswith("http://"):
        upstream_ws = "ws://" + upstream_http[7:]
    else:
        upstream_ws = upstream_http

    fwd_headers = filter_headers(request.headers)
    fwd_headers.pop("Host", None)
    for h in list(fwd_headers):
        if h.lower() in _WS_HANDSHAKE_HEADERS:
            del fwd_headers[h]

    sub_protocols: tuple[str, ...] = ()
    proto_hdr = request.headers.get("Sec-WebSocket-Protocol")
    if proto_hdr:
        sub_protocols = tuple(p.strip() for p in proto_hdr.split(","))

    request_id = f"req_{uuid.uuid4().hex[:12]}"
    t0 = time.monotonic()
    should_capture = protocol.captures(request.method, request.path_qs)
    turn = ctx.next_turn() if should_capture else 0

    connect_kwargs: dict[str, object] = {}
    proxy_settings = _resolve_ws_proxy(upstream_ws) if ctx.session.trust_env else None
    if proxy_settings:
        proxy_url, proxy_auth = proxy_settings
        connect_kwargs["proxy"] = proxy_url
        if proxy_auth is not None:
            connect_kwargs["proxy_auth"] = proxy_auth

    if should_capture:
        log.info("[Turn %d] -> WS UPGRADE %s upstream=%s", turn, request.path_qs, upstream_ws)
    else:
        log.debug("relay-only -> WS UPGRADE %s upstream=%s", request.path_qs, upstream_ws)

    try:
        upstream = await ctx.session.ws_connect(
            upstream_ws,
            headers=fwd_headers,
            protocols=sub_protocols,
            **connect_kwargs,
        )
    except Exception as exc:
        if should_capture:
            duration_ms = int((time.monotonic() - t0) * 1000)
            record = _build_ws_record(
                request_id=request_id,
                turn=turn,
                duration_ms=duration_ms,
                path=request.path_qs,
                req_headers=request.headers,
                client_messages=[],
                server_messages=[],
                upstream_base_url=ctx.target,
                error=str(exc),
            )
            await ctx.bus.publish(record)
        return web.Response(status=502, text=str(exc))

    client_ws = web.WebSocketResponse(protocols=sub_protocols)
    await client_ws.prepare(request)

    # WebSocket connections are typically long-lived (codex keeps one open
    # across many turns). We publish one record per "turn" — i.e. per
    # completed response — so the live viewer updates as the user sees the
    # agent reply, instead of one giant record at disconnect.
    client_msgs: list[str] = []
    server_msgs: list[str] = []
    turn_started_at = t0
    turn_request_id = request_id
    turn_index = turn

    async def _publish_turn(error: str | None = None) -> None:
        nonlocal client_msgs, server_msgs, turn_started_at, turn_request_id, turn_index
        if not should_capture:
            return
        if not client_msgs and not server_msgs and not error:
            return
        # Atomically swap the buffers BEFORE the awaited publish. Otherwise
        # ``_c2u`` could append to the old list during the await and we'd
        # drop those messages when we reset afterwards.
        client_snapshot = client_msgs
        server_snapshot = server_msgs
        client_msgs = []
        server_msgs = []
        turn_duration = int((time.monotonic() - turn_started_at) * 1000)
        record_request_id = turn_request_id
        record_turn = turn_index
        turn_started_at = time.monotonic()
        turn_request_id = f"req_{uuid.uuid4().hex[:12]}"
        turn_index = ctx.next_turn()
        record = _build_ws_record(
            request_id=record_request_id,
            turn=record_turn,
            duration_ms=turn_duration,
            path=request.path_qs,
            req_headers=request.headers,
            client_messages=client_snapshot,
            server_messages=server_snapshot,
            upstream_base_url=ctx.target,
            error=error,
        )
        await ctx.bus.publish(record)

    async def _c2u() -> None:
        try:
            async for msg in client_ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    if should_capture:
                        client_msgs.append(msg.data)
                    await upstream.send_str(msg.data)
                elif msg.type == aiohttp.WSMsgType.BINARY:
                    await upstream.send_bytes(msg.data)
                elif msg.type in (
                    aiohttp.WSMsgType.CLOSE,
                    aiohttp.WSMsgType.CLOSING,
                    aiohttp.WSMsgType.CLOSED,
                    aiohttp.WSMsgType.ERROR,
                ):
                    break
        except (ConnectionError, asyncio.CancelledError):
            pass

    async def _u2c() -> None:
        try:
            async for msg in upstream:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    if should_capture:
                        server_msgs.append(msg.data)
                    await client_ws.send_str(msg.data)
                    if should_capture and _is_turn_terminal_event(msg.data):
                        await _publish_turn()
                elif msg.type == aiohttp.WSMsgType.BINARY:
                    await client_ws.send_bytes(msg.data)
                elif msg.type in (
                    aiohttp.WSMsgType.CLOSE,
                    aiohttp.WSMsgType.CLOSING,
                    aiohttp.WSMsgType.CLOSED,
                    aiohttp.WSMsgType.ERROR,
                ):
                    break
        except (ConnectionError, asyncio.CancelledError):
            pass

    tasks = [asyncio.create_task(_c2u()), asyncio.create_task(_u2c())]
    _done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    for t in pending:
        t.cancel()
        try:
            await t
        except (asyncio.CancelledError, Exception):
            # CancelledError is a BaseException in 3.11+, so it must be
            # listed explicitly; the broad ``Exception`` catch covers
            # whatever the cancelled body was doing when it stopped.
            pass
    if not upstream.closed:
        await upstream.close()
    if not client_ws.closed:
        await client_ws.close()

    # Publish whatever's still buffered at disconnect (incomplete final turn
    # or a session that disconnected mid-stream).
    await _publish_turn()
    return client_ws


def _is_turn_terminal_event(msg_data: str) -> bool:
    """True if this WebSocket frame marks the end of one OpenAI/codex
    response. Used by ``_handle_websocket`` to flush a record per turn so
    the live viewer updates in real time rather than waiting for the
    entire (often long-lived) WS connection to close."""
    if not msg_data or "response.completed" not in msg_data and "response.done" not in msg_data:
        # Cheap text test before paying for json parse.
        return False
    try:
        d = json.loads(msg_data)
    except (json.JSONDecodeError, ValueError):
        return False
    if isinstance(d, dict):
        t = d.get("type")
        return t in ("response.completed", "response.done")
    return False
