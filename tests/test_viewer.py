"""HTML viewer generation: marker injection, lazy mode, escaping."""

from __future__ import annotations

import json
from pathlib import Path

from claude_tap.viewer import INJECT_MARKER, LAZY_THRESHOLD, extract_metadata, render_html


def _viewer_template_text() -> str:
    return (Path("claude_tap") / "viewer.html").read_text(encoding="utf-8")


def _injected_block(html: str) -> str:
    """Return the substring just before the injection marker — that is the
    script(s) the renderer added (small-mode: 1 script; large-mode: 2)."""
    idx = html.index(INJECT_MARKER)
    # Walk back to where the previous block of injected scripts began.
    # Two empty scripts at most; just scan back to the line before that block.
    head = html[:idx]
    # Heuristic: the injected block sits between the original previous tag and
    # the marker. Splitting on the literal HTML close tag preceding it is
    # fragile, so we just take everything after the last occurrence of
    # ``</body>`` if present, else the last 4kB before the marker.
    return head[-4096:]


def test_template_contains_inject_marker():
    """Without this marker the renderer cannot inject data."""
    assert INJECT_MARKER in _viewer_template_text()


def test_render_small_trace_inlines_data(trace_dir: Path, sample_anthropic_record: dict):
    jsonl = trace_dir / "trace_120000.jsonl"
    with open(jsonl, "w", encoding="utf-8") as f:
        f.write(json.dumps(sample_anthropic_record) + "\n")
    out = jsonl.with_suffix(".html")
    assert render_html(jsonl, out)

    html = out.read_text(encoding="utf-8")
    assert INJECT_MARKER in html
    block = _injected_block(html)
    assert "EMBEDDED_TRACE_DATA" in block
    # Small mode does not produce a META array — the runtime template mentions
    # the name as a feature-detection check, but our injected block must not.
    assert "EMBEDDED_TRACE_META" not in block
    assert "claude-opus-4-6" in block


def test_render_large_trace_uses_lazy_mode(trace_dir: Path, sample_anthropic_record: dict):
    jsonl = trace_dir / "trace_130000.jsonl"
    with open(jsonl, "w", encoding="utf-8") as f:
        for i in range(LAZY_THRESHOLD + 5):
            r = {**sample_anthropic_record, "turn": i + 1, "request_id": f"req_{i}"}
            f.write(json.dumps(r) + "\n")
    out = jsonl.with_suffix(".html")
    render_html(jsonl, out)

    html = out.read_text(encoding="utf-8")
    assert "EMBEDDED_TRACE_META" in html
    assert 'id="trace-raw"' in html
    # The metadata array length should match the on-disk record count.
    # Quick sanity check: the marker still survives.
    assert INJECT_MARKER in html


def test_render_escapes_closing_script_in_raw_block(trace_dir: Path):
    """A record containing ``</script>`` text must not break out of the
    text/plain script tag holding the raw JSONL."""
    jsonl = trace_dir / "trace_140000.jsonl"
    record = {
        "turn": 1,
        "request": {"method": "POST", "path": "/v1/messages", "headers": {}, "body": {"model": "x"}},
        "response": {"status": 200, "headers": {}, "body": {"text": "evil </script><img>"}},
    }
    with open(jsonl, "w", encoding="utf-8") as f:
        for i in range(LAZY_THRESHOLD + 1):  # force lazy mode
            f.write(json.dumps({**record, "turn": i + 1}) + "\n")
    out = jsonl.with_suffix(".html")
    render_html(jsonl, out)
    html = out.read_text(encoding="utf-8")
    # The dangerous sequence must be escaped to <\/script> inside the raw block.
    assert "</script><img>" not in html
    assert "<\\/script><img>" in html


def test_extract_metadata_pulls_tokens_and_model():
    record = {
        "turn": 7,
        "request_id": "req_z",
        "duration_ms": 50,
        "request": {
            "method": "POST",
            "path": "/v1/messages",
            "headers": {},
            "body": {"model": "claude-opus", "messages": [{"role": "user", "content": "hi"}], "tools": []},
        },
        "response": {
            "status": 200,
            "headers": {},
            "body": {
                "content": [{"type": "tool_use", "name": "Read"}],
                "usage": {"input_tokens": 1, "output_tokens": 2, "cache_read_input_tokens": 3},
            },
        },
    }
    meta = extract_metadata(json.dumps(record))
    assert meta is not None
    assert meta["turn"] == 7
    assert meta["model"] == "claude-opus"
    assert meta["input_tokens"] == 1
    assert meta["output_tokens"] == 2
    assert meta["cache_read_input_tokens"] == 3
    assert meta["response_tool_names"] == ["Read"]


def test_extract_metadata_pulls_additional_tools_from_responses_input():
    record = {
        "turn": 8,
        "request_id": "req_codex",
        "request": {
            "method": "POST",
            "path": "/v1/responses",
            "headers": {},
            "body": {
                "model": "gpt-5.4",
                "input": [
                    {
                        "type": "additional_tools",
                        "role": "developer",
                        "tools": [
                            {"type": "custom", "name": "exec"},
                            {"type": "function", "name": "wait"},
                            {"type": "namespace", "name": "collaboration", "tools": []},
                        ],
                    },
                    {"type": "message", "role": "user", "content": "hello"},
                ],
            },
        },
        "response": {"status": 200, "headers": {}, "body": {}},
    }

    meta = extract_metadata(json.dumps(record))

    assert meta is not None
    assert meta["message_count"] == 1
    assert meta["tool_names"] == ["exec", "wait", "collaboration"]


def test_extract_metadata_counts_custom_tool_items_and_response_calls():
    record = {
        "turn": 9,
        "request_id": "req_custom",
        "request": {
            "method": "POST",
            "path": "/v1/responses",
            "headers": {},
            "body": {
                "model": "gpt-5.6-sol",
                "input": [
                    {"type": "message", "role": "user", "content": "inspect"},
                    {
                        "type": "custom_tool_call",
                        "status": "completed",
                        "call_id": "call_exec",
                        "name": "exec",
                        "input": "text(await tools.exec_command({cmd: 'pwd'}));",
                    },
                    {
                        "type": "custom_tool_call_output",
                        "call_id": "call_exec",
                        "output": [{"type": "input_text", "text": "/tmp/project"}],
                    },
                    {"type": "future_item", "payload": {"kept": True}},
                ],
            },
        },
        "response": {
            "status": 200,
            "headers": {},
            "body": {
                "output": [
                    {
                        "type": "custom_tool_call",
                        "status": "completed",
                        "call_id": "call_next",
                        "name": "wait",
                        "input": "{}",
                    }
                ]
            },
        },
    }

    meta = extract_metadata(json.dumps(record))

    assert meta is not None
    assert meta["message_count"] == 4
    assert meta["response_tool_names"] == ["wait"]


def test_extract_metadata_reads_custom_tool_call_from_streamed_item():
    record = {
        "turn": 10,
        "request_id": "req_streamed_custom",
        "request": {
            "method": "POST",
            "path": "/v1/responses",
            "headers": {},
            "body": {"model": "gpt-5.6-sol", "input": []},
        },
        "response": {
            "status": 200,
            "headers": {},
            "body": {"output": []},
            "sse_events": [
                {
                    "event": "response.output_item.done",
                    "data": {
                        "type": "response.output_item.done",
                        "item": {"type": "custom_tool_call", "name": "exec", "input": "pwd"},
                    },
                }
            ],
        },
    }

    meta = extract_metadata(json.dumps(record))

    assert meta is not None
    assert meta["response_tool_names"] == ["exec"]


def test_extract_metadata_handles_garbage():
    assert extract_metadata("not json") is None


def test_extract_metadata_handles_non_json_request_body():
    record = {
        "turn": 1,
        "request": {"method": "POST", "path": "/backend-api/codex/responses", "headers": {}, "body": "zstd bytes"},
        "response": {"status": 200, "headers": {}, "body": "event: response.created\n"},
    }
    meta = extract_metadata(json.dumps(record))
    assert meta is not None
    assert meta["path"] == "/backend-api/codex/responses"
    assert meta["model"] == ""


def test_render_strips_sse_events_by_default(trace_dir: Path):
    """The HTML viewer doesn't need per-chunk events; default to stripping
    them so a long trace doesn't blow up the file."""
    jsonl = trace_dir / "trace_120000.jsonl"
    record = {
        "turn": 1,
        "request": {"method": "POST", "path": "/v1/messages", "headers": {}, "body": {"model": "x"}},
        "response": {
            "status": 200,
            "headers": {},
            "body": {"id": "msg_x", "content": [{"type": "text", "text": "hi"}]},
            "sse_events": [{"event": "x", "data": "HUGE_PAYLOAD_" * 200}],
            "ws_events": [{"type": "y"}],
        },
    }
    with open(jsonl, "w", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")
    out = jsonl.with_suffix(".html")
    render_html(jsonl, out)  # default: strip
    html = out.read_text(encoding="utf-8")
    # The injected data block must not contain the stripped event payload.
    block = _injected_block(html)
    assert "HUGE_PAYLOAD_" not in block
    # The reassembled snapshot stays.
    assert "msg_x" in block


def test_render_keeps_sse_events_when_requested(trace_dir: Path):
    jsonl = trace_dir / "trace_130000.jsonl"
    record = {
        "turn": 1,
        "request": {"method": "POST", "path": "/v1/messages", "headers": {}, "body": {"model": "x"}},
        "response": {
            "status": 200,
            "headers": {},
            "body": {"id": "msg_y"},
            "sse_events": [{"event": "marker", "data": "DISTINCT_TOKEN_42"}],
        },
    }
    with open(jsonl, "w", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")
    out = jsonl.with_suffix(".html")
    render_html(jsonl, out, strip_events=False)
    block = _injected_block(out.read_text(encoding="utf-8"))
    assert "DISTINCT_TOKEN_42" in block
