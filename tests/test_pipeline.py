"""Pipeline primitives that are protocol-agnostic: header filtering,
decompression, JSON parsing, record construction, upstream URL building.
"""

from __future__ import annotations

import gzip
import zlib

import pytest

from claude_tap import pipeline
from claude_tap.pipeline import (
    HOP_BY_HOP,
    build_http_record,
    build_upstream_url,
    build_ws_record,
    capture_only_response,
    capture_only_stream_response,
    filter_headers,
    maybe_decompress,
    parse_json_body,
    reassemble_event_stream_body,
)
from claude_tap.protocols import ANTHROPIC, OPENAI

# --- build_upstream_url (delegates to protocol.rewrite_upstream_path) -----


def test_upstream_url_anthropic_keeps_path():
    assert (
        build_upstream_url("https://api.anthropic.com", "/v1/messages?beta=true", ANTHROPIC)
        == "https://api.anthropic.com/v1/messages?beta=true"
    )


def test_upstream_url_codex_oauth_strips_v1():
    assert (
        build_upstream_url("https://chatgpt.com/backend-api/codex", "/v1/responses", OPENAI)
        == "https://chatgpt.com/backend-api/codex/responses"
    )


def test_upstream_url_codex_default_keeps_v1():
    assert (
        build_upstream_url("https://api.openai.com", "/v1/responses", OPENAI) == "https://api.openai.com/v1/responses"
    )


# --- filter_headers --------------------------------------------------------


def test_filter_headers_removes_hop_by_hop():
    raw = {"Connection": "close", "Keep-Alive": "1", "X-Custom": "v"}
    out = filter_headers(raw)
    assert "Connection" not in out
    assert "Keep-Alive" not in out
    assert out["X-Custom"] == "v"


def test_filter_headers_redacts_secrets():
    raw = {"x-api-key": "sk-ant-12345678901234", "authorization": "Bearer ABCDEFGHIJKLMNOP"}
    out = filter_headers(raw, redact=True)
    assert out["x-api-key"].startswith("sk-ant-12345")
    assert out["x-api-key"].endswith("...")
    assert out["authorization"].endswith("...")
    assert filter_headers({"x-api-key": "short"}, redact=True)["x-api-key"] == "***"


def test_hop_by_hop_set_is_lower_case():
    """Regression guard: matching is done after .lower()."""
    assert "connection" in HOP_BY_HOP
    assert "Connection" not in HOP_BY_HOP


# --- parse_json_body / decompress -----------------------------------------


def test_parse_json_body_valid():
    assert parse_json_body(b'{"x":1}') == {"x": 1}


def test_parse_json_body_invalid_returns_string():
    assert parse_json_body(b"not json") == "not json"


def test_parse_json_body_empty_returns_none():
    assert parse_json_body(b"") is None


def test_decompress_gzip():
    assert maybe_decompress(gzip.compress(b"hello world"), "gzip") == b"hello world"


def test_decompress_deflate():
    assert maybe_decompress(zlib.compress(b"abcd"), "deflate") == b"abcd"


def test_decompress_zstd():
    if pipeline.zstd is None:
        pytest.skip("backports-zstd is not installed in this test environment")
    zstd = pipeline.zstd
    assert maybe_decompress(zstd.compress(b'{"model":"gpt-test"}'), "zstd") == b'{"model":"gpt-test"}'


def test_decompress_identity_passthrough():
    assert maybe_decompress(b"raw", "") == b"raw"
    assert maybe_decompress(b"raw", "br") == b"raw"


def test_decompress_corrupt_returns_input():
    """Corrupt gzip must not raise — we fall back to raw bytes."""
    assert maybe_decompress(b"not a gzip", "gzip") == b"not a gzip"


def test_reassemble_event_stream_body_openai_responses():
    body = (
        b'event: response.created\ndata: {"type":"response.created","response":{"id":"resp_1","output":[]}}\n\n'
        b'event: response.output_item.done\ndata: {"type":"response.output_item.done","item":{"type":"function_call","name":"Read"}}\n\n'
        b'event: response.completed\ndata: {"type":"response.completed","response":{"id":"resp_1","output":[],"usage":{"input_tokens":3,"output_tokens":2}}}\n\n'
    )
    snapshot, events = reassemble_event_stream_body(OPENAI, body)
    assert len(events) == 3
    assert snapshot["id"] == "resp_1"
    assert snapshot["usage"]["input_tokens"] == 3
    assert snapshot["output"][0]["name"] == "Read"


def test_capture_only_openai_models_returns_grok_compatible_catalog():
    response = capture_only_response(OPENAI, "/v1/models", None)
    assert response["object"] == "list"
    assert response["data"][0]["id"] == "grok-build"
    assert response["data"][0]["apiBackend"] == "responses"
    assert response["data"][0]["_meta"]["agentType"] == "grok-build"


def test_capture_only_stream_response_returns_responses_api_events():
    response = capture_only_stream_response(OPENAI, "/v1/responses", {"model": "grok-build"})

    assert response is not None
    body, events, raw = response
    assert body["status"] == "completed"
    assert body["output"][0]["content"][0]["annotations"] == []
    assert body["usage"]["input_tokens_details"]["cached_tokens"] == 0
    assert body["usage"]["output_tokens_details"]["reasoning_tokens"] == 0
    assert [event["data"]["type"] for event in events] == [
        "response.created",
        "response.output_text.delta",
        "response.completed",
    ]
    assert b'"type":"response.completed"' in raw


# --- build_record ----------------------------------------------------------


def test_build_http_record_basic_shape():
    r = build_http_record(
        request_id="req_x",
        turn=3,
        duration_ms=123,
        method="POST",
        path="/v1/messages",
        req_headers={"x-api-key": "sk-1234567890ab"},
        req_body={"model": "claude"},
        status=200,
        resp_headers={"content-type": "application/json"},
        resp_body={"ok": True},
        upstream_base_url="https://api.anthropic.com",
    )
    assert r["turn"] == 3
    assert r["request"]["method"] == "POST"
    assert r["request"]["body"] == {"model": "claude"}
    assert "..." in r["request"]["headers"]["x-api-key"]
    assert r["response"]["status"] == 200
    assert "sse_events" not in r["response"]
    assert r["upstream_base_url"] == "https://api.anthropic.com"


def test_build_ws_record_extracts_response_completed():
    r = build_ws_record(
        request_id="req_ws",
        turn=1,
        duration_ms=99,
        path="/v1/responses",
        req_headers={"authorization": "Bearer something"},
        client_messages=['{"type":"user","content":"hi"}'],
        server_messages=[
            '{"type":"response.created","response":{"id":"resp_1"}}',
            '{"type":"response.completed","response":{"id":"resp_1","output":[]}}',
        ],
        upstream_base_url="wss://api.openai.com",
    )
    assert r["transport"] == "websocket"
    assert r["response"]["status"] == 101
    assert r["response"]["body"] == {"id": "resp_1", "output": []}
    assert len(r["response"]["ws_events"]) == 2


def test_build_ws_record_fills_empty_output_from_output_item_done():
    """Codex leaves ``response.completed.response.output`` empty and
    streams items via ``response.output_item.done``; merge them in."""
    r = build_ws_record(
        request_id="req",
        turn=1,
        duration_ms=1,
        path="/v1/responses",
        req_headers={},
        client_messages=[],
        server_messages=[
            '{"type":"response.created","response":{"id":"r","output":[]}}',
            '{"type":"response.output_item.done","item":{"type":"function_call","name":"update_plan"}}',
            '{"type":"response.completed","response":{"id":"r","output":[]}}',
        ],
        upstream_base_url="wss://x",
    )
    out = r["response"]["body"]["output"]
    assert len(out) == 1
    assert out[0]["name"] == "update_plan"


def test_build_ws_record_does_not_overwrite_populated_output():
    r = build_ws_record(
        request_id="req",
        turn=1,
        duration_ms=1,
        path="/v1/responses",
        req_headers={},
        client_messages=[],
        server_messages=[
            '{"type":"response.output_item.done","item":{"type":"function_call","name":"streamed"}}',
            '{"type":"response.completed","response":{"id":"r","output":[{"type":"function_call","name":"final"}]}}',
        ],
        upstream_base_url="wss://x",
    )
    out = r["response"]["body"]["output"]
    assert len(out) == 1
    assert out[0]["name"] == "final"


# --- _is_turn_terminal_event (drives per-turn live publish) ---------------


def test_turn_terminal_event_recognizes_response_completed():
    from claude_tap.reverse_proxy import _is_turn_terminal_event

    assert _is_turn_terminal_event('{"type":"response.completed","response":{}}')
    assert _is_turn_terminal_event('{"type":"response.done","response":{}}')


def test_turn_terminal_event_ignores_intermediate_events():
    from claude_tap.reverse_proxy import _is_turn_terminal_event

    assert not _is_turn_terminal_event('{"type":"response.created"}')
    assert not _is_turn_terminal_event('{"type":"response.output_text.delta"}')
    assert not _is_turn_terminal_event('{"type":"response.in_progress"}')


def test_turn_terminal_event_handles_garbage():
    from claude_tap.reverse_proxy import _is_turn_terminal_event

    assert not _is_turn_terminal_event("")
    assert not _is_turn_terminal_event("not json")
    # ``response.completed`` substring without proper json shape: no match.
    assert not _is_turn_terminal_event('"some string with response.completed in it"')
