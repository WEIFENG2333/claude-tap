"""Transport-agnostic proxy primitives.

Both the reverse-mode (aiohttp) and forward-mode (raw asyncio + TLS) servers
build the same record shape from the same header / body / streaming logic.
Anything that does not depend on *how* bytes arrive lives here.

The proxy holds a tuple of ``Protocol`` (potentially more than one for
multi-protocol clients like opencode) and dispatches each incoming request
to the protocol whose ``allowed_paths`` matches.
"""

from __future__ import annotations

import gzip
import json
import zlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Mapping

import aiohttp

from claude_tap.protocols import Protocol, select_for_path
from claude_tap.trace import EventBus

try:
    import backports.zstd as zstd
except Exception:  # pragma: no cover - optional import guard
    zstd = None

HOP_BY_HOP = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailers",
        "transfer-encoding",
        "upgrade",
    }
)

REDACT_HEADERS = frozenset({"x-api-key", "authorization"})


@dataclass
class ProxyContext:
    protocols: tuple[Protocol, ...]
    target: str
    bus: EventBus
    session: aiohttp.ClientSession
    capture_only: bool = False
    turn_counter: int = field(default=0)

    def next_turn(self) -> int:
        self.turn_counter += 1
        return self.turn_counter

    def protocol_for(self, path: str) -> Protocol | None:
        return select_for_path(path, self.protocols)


def filter_headers(headers: Mapping[str, str], *, redact: bool = False) -> dict[str, str]:
    out: dict[str, str] = {}
    for k, v in headers.items():
        if k.lower() in HOP_BY_HOP:
            continue
        if redact and k.lower() in REDACT_HEADERS:
            out[k] = (v[:12] + "...") if len(v) > 12 else "***"
        else:
            out[k] = v
    return out


def build_upstream_url(target: str, path_qs: str, protocol: Protocol) -> str:
    rewritten = protocol.rewrite_upstream_path(path_qs, target)
    return target.rstrip("/") + "/" + rewritten.lstrip("/")


def parse_json_body(body: bytes) -> object:
    if not body:
        return None
    try:
        return json.loads(body)
    except (json.JSONDecodeError, ValueError):
        return body.decode("utf-8", errors="replace")


def maybe_decompress(body: bytes, content_encoding: str) -> bytes:
    if not body:
        return body
    decoded = body
    encodings = [e.strip().lower() for e in (content_encoding or "").split(",") if e.strip()]
    try:
        for enc in reversed(encodings):
            if enc == "gzip":
                decoded = gzip.decompress(decoded)
            elif enc == "deflate":
                decoded = zlib.decompress(decoded)
            elif enc in ("zstd", "zst") and zstd is not None:
                decoded = zstd.decompress(decoded)
            else:
                return body
    except Exception:
        return body
    return decoded


def reassemble_event_stream_body(protocol: Protocol, body: bytes) -> tuple[object, list[dict]]:
    reassembler = protocol.make_reassembler()
    reassembler.feed_bytes(body)
    snapshot = reassembler.reconstruct()
    return snapshot, reassembler.events


def capture_only_response(protocol: Protocol, path: str, req_body: object) -> dict:
    model = req_body.get("model", "claude-tap-capture") if isinstance(req_body, dict) else "claude-tap-capture"
    if protocol.name == "openai" and path.split("?", 1)[0].rstrip("/").endswith("/models"):
        return {
            "object": "list",
            "data": [
                {
                    "id": "grok-build",
                    "object": "model",
                    "created": 0,
                    "owned_by": "claude-tap",
                    "apiBackend": "responses",
                    "_meta": {"agentType": "grok-build"},
                }
            ],
        }
    if protocol.name == "anthropic":
        return {
            "id": "msg_claude_tap_capture",
            "type": "message",
            "role": "assistant",
            "model": model,
            "content": [{"type": "text", "text": "captured"}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 0, "output_tokens": 0},
        }
    if protocol.name in ("gemini", "antigravity"):
        response = {
            "candidates": [
                {
                    "content": {"role": "model", "parts": [{"text": "captured"}]},
                    "finishReason": "STOP",
                    "index": 0,
                }
            ],
            "usageMetadata": {"promptTokenCount": 0, "candidatesTokenCount": 0, "totalTokenCount": 0},
        }
        if protocol.name == "antigravity":
            return {"response": response, "traceId": "claude-tap-capture"}
        return response
    if protocol.name == "openai" and "chat/completions" in path:
        return {
            "id": "chatcmpl_claude_tap_capture",
            "object": "chat.completion",
            "created": 0,
            "model": model,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "captured"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        }
    return {
        "id": "resp_claude_tap_capture",
        "object": "response",
        "created_at": 0,
        "status": "completed",
        "model": model,
        "output": [
            {
                "type": "message",
                "id": "msg_claude_tap_capture",
                "status": "completed",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "captured", "annotations": []}],
            }
        ],
        "usage": {
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "input_tokens_details": {"cached_tokens": 0},
            "output_tokens_details": {"reasoning_tokens": 0},
        },
    }


def capture_only_stream_response(
    protocol: Protocol, path: str, req_body: object
) -> tuple[dict, list[dict], bytes] | None:
    resp_body = capture_only_response(protocol, path, req_body)
    if protocol.name == "anthropic":
        model = resp_body.get("model", "claude-tap-capture")
        events = [
            {
                "event": "message_start",
                "data": {
                    "type": "message_start",
                    "message": {
                        "id": "msg_claude_tap_capture",
                        "type": "message",
                        "role": "assistant",
                        "model": model,
                        "content": [],
                        "stop_reason": None,
                        "stop_sequence": None,
                        "usage": {"input_tokens": 0, "output_tokens": 0},
                    },
                },
            },
            {
                "event": "content_block_start",
                "data": {
                    "type": "content_block_start",
                    "index": 0,
                    "content_block": {"type": "text", "text": ""},
                },
            },
            {
                "event": "content_block_delta",
                "data": {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "text_delta", "text": "captured"},
                },
            },
            {"event": "content_block_stop", "data": {"type": "content_block_stop", "index": 0}},
            {
                "event": "message_delta",
                "data": {
                    "type": "message_delta",
                    "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                    "usage": {"output_tokens": 0},
                },
            },
            {"event": "message_stop", "data": {"type": "message_stop"}},
        ]
        return resp_body, events, _sse_bytes(events)
    if protocol.name in ("gemini", "antigravity"):
        events = [{"event": "message", "data": resp_body}]
        return resp_body, events, _sse_bytes(events)
    if protocol.name == "openai" and "chat/completions" in path:
        model = resp_body.get("model", "claude-tap-capture")
        chunk_id = resp_body.get("id", "chatcmpl_claude_tap_capture")
        chunks = [
            {
                "id": chunk_id,
                "object": "chat.completion.chunk",
                "created": 0,
                "model": model,
                "choices": [{"index": 0, "delta": {"role": "assistant", "content": "captured"}, "finish_reason": None}],
            },
            {
                "id": chunk_id,
                "object": "chat.completion.chunk",
                "created": 0,
                "model": model,
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            },
            "[DONE]",
        ]
        events = [{"event": "message", "data": chunk} for chunk in chunks]
        return resp_body, events, _sse_bytes(events)
    if protocol.name == "openai" and path.split("?", 1)[0].rstrip("/").endswith("/responses"):
        in_progress = {**resp_body, "status": "in_progress", "output": []}
        output_item = resp_body["output"][0]
        events = [
            {
                "event": "response.created",
                "data": {"type": "response.created", "sequence_number": 0, "response": in_progress},
            },
            {
                "event": "response.output_item.added",
                "data": {
                    "type": "response.output_item.added",
                    "sequence_number": 1,
                    "output_index": 0,
                    "item": {**output_item, "status": "in_progress", "content": []},
                },
            },
            {
                "event": "response.output_text.delta",
                "data": {
                    "type": "response.output_text.delta",
                    "sequence_number": 2,
                    "item_id": "msg_claude_tap_capture",
                    "output_index": 0,
                    "content_index": 0,
                    "delta": "captured",
                },
            },
            {
                "event": "response.output_item.done",
                "data": {
                    "type": "response.output_item.done",
                    "sequence_number": 3,
                    "output_index": 0,
                    "item": output_item,
                },
            },
            {
                "event": "response.completed",
                "data": {"type": "response.completed", "sequence_number": 4, "response": resp_body},
            },
        ]
        return resp_body, events, _sse_bytes(events)
    return None


def _sse_bytes(events: list[dict]) -> bytes:
    lines: list[str] = []
    for event in events:
        event_type = event.get("event")
        if event_type and event_type != "message":
            lines.append(f"event: {event_type}")
        data = event.get("data")
        if isinstance(data, str):
            lines.append(f"data: {data}")
        else:
            lines.append(f"data: {json.dumps(data, separators=(',', ':'))}")
        lines.append("")
    return ("\n".join(lines) + "\n").encode("utf-8")


def build_http_record(
    *,
    request_id: str,
    turn: int,
    duration_ms: int,
    method: str,
    path: str,
    req_headers: Mapping[str, str],
    req_body: object,
    status: int,
    resp_headers: Mapping[str, str],
    resp_body: object,
    sse_events: list[dict] | None = None,
    upstream_base_url: str | None = None,
) -> dict:
    record: dict = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "request_id": request_id,
        "turn": turn,
        "duration_ms": duration_ms,
        "request": {
            "method": method,
            "path": path,
            "headers": filter_headers(req_headers, redact=True),
            "body": req_body,
        },
        "response": {
            "status": status,
            "headers": filter_headers(resp_headers),
            "body": resp_body,
        },
    }
    if sse_events:
        record["response"]["sse_events"] = sse_events
    if upstream_base_url:
        record["upstream_base_url"] = upstream_base_url
    return record


def build_ws_record(
    *,
    request_id: str,
    turn: int,
    duration_ms: int,
    path: str,
    req_headers: Mapping[str, str],
    client_messages: list[str],
    server_messages: list[str],
    upstream_base_url: str,
    error: str | None = None,
) -> dict:
    req_body: object = None
    for msg in client_messages:
        try:
            parsed = json.loads(msg)
            if req_body is None:
                req_body = parsed
        except (json.JSONDecodeError, ValueError):
            pass

    ws_events: list[dict] = []
    resp_body: object = None
    output_items: list[dict] = []
    for msg in server_messages:
        try:
            parsed = json.loads(msg)
            ws_events.append(parsed)
            if not isinstance(parsed, dict):
                continue
            evt = parsed.get("type")
            if evt == "response.output_item.done":
                item = parsed.get("item")
                if isinstance(item, dict):
                    output_items.append(item)
            elif evt in ("response.completed", "response.done"):
                resp_body = parsed.get("response", parsed)
        except (json.JSONDecodeError, ValueError):
            ws_events.append({"raw": msg})

    if resp_body is None:
        for ev in ws_events:
            if isinstance(ev, dict) and ev.get("type") == "response.created":
                resp_body = ev.get("response", ev)
                break

    if isinstance(resp_body, dict) and output_items and not resp_body.get("output"):
        resp_body["output"] = output_items

    record: dict = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "request_id": request_id,
        "turn": turn,
        "duration_ms": duration_ms,
        "transport": "websocket",
        "request": {
            "method": "WEBSOCKET",
            "path": path,
            "headers": filter_headers(req_headers, redact=True),
            "body": req_body,
        },
        "response": {
            "status": 502 if error else 101,
            "headers": {},
            "body": resp_body,
        },
    }
    if ws_events:
        record["response"]["ws_events"] = ws_events
    if error:
        record["response"]["error"] = error
    if upstream_base_url:
        record["upstream_base_url"] = upstream_base_url
    return record
