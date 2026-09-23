"""HTML viewer generation: marker injection, lazy mode, escaping."""

from __future__ import annotations

import base64
import gzip
import json
import re
from pathlib import Path

from claude_tap.catalog import TraceCatalog, summarize_sessions
from claude_tap.viewer import INJECT_MARKER, LAZY_THRESHOLD, extract_metadata, load_viewer_template, render_html


def _viewer_template_text() -> str:
    return load_viewer_template()


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


def test_messages_render_one_natural_block_card_and_bound_only_long_payloads():
    """Conversation cards follow raw block boundaries without universal nested scrolling."""
    template = _viewer_template_text()

    assert "--message-long-max-height: clamp(220px, 34vh, 340px)" in template
    assert "--tool-payload-long-max-height: clamp(180px, 24vh, 240px)" in template
    assert "MESSAGE_LONG_CHARACTER_THRESHOLD = 3_200" in template
    assert "MESSAGE_LONG_LINE_THRESHOLD = 40" in template
    assert "function isLongMessageContent(value)" in template
    assert "--message-card-border: var(--border-l2)" in template
    assert 'class="request-message-group"' in template
    assert 'class="request-message${toolClass}"' in template
    assert 'class="request-message-blocks"' in template
    assert 'class="request-message-header"' in template
    assert 'class="message-role"' in template
    assert 'data-origin="${origin}"' in template
    assert 'data-role="${escapeHtml(role)}"' in template
    assert 'data-block-path="${escapeHtml(' in template
    assert 'data-block-type="${escapeHtml(' in template
    assert 'data-message-scroll tabindex="0"' not in template
    assert "overscroll-behavior-y: auto" in template
    assert "function installNestedScrollRouting()" not in template
    assert "const remainder = delta - (innerEnd - innerStart)" not in template
    assert 'class="message-tool-data code-scroll nested-scroll"' not in template
    assert 'class="message-content code-scroll nested-scroll"' not in template


def test_template_assembles_modular_viewer_assets():
    template = _viewer_template_text()

    assert "class TraceStore" in template
    assert "class FixedVirtualList" in template
    assert "class Inspector" in template
    assert "class TraceApp" in template
    assert "viewer_assets/" not in template


def test_redesigned_viewer_uses_model_calls_and_custom_menus():
    template = _viewer_template_text()

    assert "<select" not in template
    assert "timeline-shell" not in template
    assert "errors-filter" not in template
    assert 'this.activeTab = "conversation"' in template
    assert "this.scrollPositions = new Map()" in template
    assert 'data-sidebar="open"' in template
    assert 'data-inspector="closed"' in template
    assert 'conversation: "Messages"' in template
    assert template.count('system: "System"') == 2
    assert template.count('tools: "Tools"') == 2
    assert 'system: "系统提示词"' not in template
    assert "event-label" not in template
    assert "activity-kind" not in template
    assert 'messageKind: "MESSAGE"' not in template
    assert 'toolsKind: "TOOLS"' not in template
    assert template.count('activity: "Activity"') == 2
    assert ".trajectory-row.failed::before" not in template
    assert 'class="row-tool-icon"' in template


def test_viewer_components_consume_semantic_color_tokens_only():
    styles = (Path(__file__).parents[1] / "claude_tap" / "viewer_assets" / "styles.css").read_text(encoding="utf-8")
    component_styles = styles[styles.index("*,\n*::before") :]

    assert "--surface-selected:" in styles
    assert "--role-assistant-fg:" in styles
    assert "--status-warning-fg:" in styles
    assert "--syntax-string:" in styles
    assert "color: var(--tool-fg)" in component_styles
    assert re.findall(r"(?:rgb|rgba)\([^)]*\)|#[0-9a-fA-F]{3,8}\b", component_styles) == []


def test_request_preview_preserves_protocol_order_and_uses_foldable_json():
    template = _viewer_template_text()

    assert "function requestSequence(body)" in template
    assert "for (const key of Object.keys(raw))" in template
    assert 'type === "tool_result"' in template
    assert "class JsonTree {" in template
    assert "JSON_OBJECT_PREVIEW_LIMIT = 4" in template
    assert "JSON_ARRAY_PREVIEW_LIMIT = 5" in template
    assert "this.renderJson(host, this.record.request?.body ?? {})" in template
    assert "this.host.oncontextmenu" in template
    assert "ArrowRight" in template
    assert "ArrowLeft" in template
    assert "ArrowUp" in template
    assert "ArrowDown" in template


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
    assert "EMBEDDED_TRACE_META" in block
    assert "EMBEDDED_CAPTURE_INFO" in block
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
    assert 'id="trace-compressed"' in html
    assert 'data-codec="gzip-base64"' in html
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
    # Large records are compressed, so user content cannot terminate the host
    # script element at all.
    assert "</script><img>" not in html
    assert 'id="trace-compressed"' in html


def test_large_trace_payload_is_independently_decompressible(trace_dir: Path, sample_anthropic_record: dict):
    jsonl = trace_dir / "trace_140500.jsonl"
    with open(jsonl, "w", encoding="utf-8") as output:
        for index in range(LAZY_THRESHOLD + 1):
            output.write(json.dumps({**sample_anthropic_record, "turn": index + 1}) + "\n")
    html_path = jsonl.with_suffix(".html")
    render_html(jsonl, html_path)

    html = html_path.read_text(encoding="utf-8")
    payload = html.split('data-codec="gzip-base64">\n', 1)[1].split("\n</script>", 1)[0]
    first_record = json.loads(gzip.decompress(base64.b64decode(payload.splitlines()[0])))

    assert first_record["turn"] == 1
    assert first_record["request"]["body"]["model"] == "claude-opus-4-6"


def test_render_small_trace_escapes_closing_script_in_data_and_metadata(trace_dir: Path):
    jsonl = trace_dir / "trace_141000.jsonl"
    record = {
        "turn": 1,
        "request": {
            "method": "POST",
            "path": "/v1/messages",
            "headers": {},
            "body": {"messages": [{"role": "user", "content": "</script><img>"}]},
        },
        "response": {"status": 200, "headers": {}, "body": {}},
    }
    jsonl.write_text(json.dumps(record) + "\n", encoding="utf-8")
    output = jsonl.with_suffix(".html")

    render_html(jsonl, output)

    html = output.read_text(encoding="utf-8")
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
    assert meta["user_hint"] == "hi"


def test_extract_metadata_indexes_assistant_output_tool_arguments_and_results():
    record = {
        "turn": 7,
        "request": {
            "method": "POST",
            "path": "/v1/messages",
            "headers": {},
            "body": {
                "model": "claude-opus",
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": "toolu_previous",
                                "content": "all tests passed",
                            }
                        ],
                    }
                ],
            },
        },
        "response": {
            "status": 200,
            "headers": {},
            "body": {
                "content": [
                    {"type": "text", "text": "I found the failing path."},
                    {
                        "type": "tool_use",
                        "id": "toolu_next",
                        "name": "Bash",
                        "input": {"command": "uv run pytest", "description": "Run tests"},
                    },
                ]
            },
        },
    }

    meta = extract_metadata(json.dumps(record))

    assert meta is not None
    assert meta["assistant_hint"] == "I found the failing path."
    assert meta["tool_calls"] == [
        {
            "id": "toolu_next",
            "name": "Bash",
            "input_preview": '{"command":"uv run pytest","description":"Run tests"}',
        }
    ]
    assert meta["tool_result_ids"] == ["toolu_previous"]
    assert meta["tool_results"][0]["output_preview"] == "all tests passed"


def test_extract_metadata_marks_http_200_sse_error_as_failed():
    record = {
        "request": {
            "method": "POST",
            "path": "/v1/messages",
            "headers": {},
            "body": {"model": "claude-test", "messages": [{"role": "user", "content": "hello"}]},
        },
        "response": {
            "status": 200,
            "headers": {"content-type": "text/event-stream"},
            "body": None,
            "sse_events": [
                {
                    "event": "error",
                    "data": {
                        "type": "error",
                        "error": {"type": "overloaded_error", "message": "Overloaded"},
                    },
                }
            ],
        },
    }

    meta = extract_metadata(json.dumps(record))

    assert meta is not None
    assert meta["status"] == 200
    assert meta["error_message"] == "Overloaded"


def test_extract_metadata_supports_provider_specific_tool_use_and_result_types():
    call_record = {
        "request": {"method": "POST", "path": "/v1/messages", "headers": {}, "body": {"model": "test"}},
        "response": {
            "status": 200,
            "headers": {},
            "body": {
                "content": [
                    {
                        "type": "server_tool_use",
                        "id": "srv_1",
                        "name": "web_search",
                        "input": {"query": "viewer"},
                    }
                ]
            },
        },
    }
    result_record = {
        "request": {
            "method": "POST",
            "path": "/v1/messages",
            "headers": {},
            "body": {
                "model": "test",
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "web_search_tool_result",
                                "tool_use_id": "srv_1",
                                "content": [{"title": "Result"}],
                            }
                        ],
                    }
                ],
            },
        },
        "response": {"status": 200, "headers": {}, "body": {}},
    }

    call_meta = extract_metadata(json.dumps(call_record))
    result_meta = extract_metadata(json.dumps(result_record))

    assert call_meta is not None
    assert call_meta["tool_calls"][0]["name"] == "web_search"
    assert result_meta is not None
    assert result_meta["tool_result_ids"] == ["srv_1"]
    assert "Result" in result_meta["tool_results"][0]["output_preview"]


def test_extract_metadata_normalizes_openai_chat_and_gemini_tool_activity():
    openai_record = {
        "request": {
            "method": "POST",
            "path": "/v1/chat/completions",
            "headers": {},
            "body": {"model": "gpt-test", "messages": [{"role": "user", "content": "Inspect"}]},
        },
        "response": {
            "status": 200,
            "headers": {},
            "body": {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "Checking now.",
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {"name": "read_file", "arguments": '{"path":"README.md"}'},
                                }
                            ],
                        }
                    }
                ]
            },
        },
    }
    gemini_record = {
        "request": {
            "method": "POST",
            "path": "/v1beta/models/gemini-test:generateContent",
            "headers": {},
            "body": {
                "tools": [
                    {
                        "functionDeclarations": [
                            {
                                "name": "search_files",
                                "description": "Search files",
                                "parameters": {"type": "object"},
                            }
                        ]
                    }
                ]
            },
        },
        "response": {
            "status": 200,
            "headers": {},
            "body": {
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {"text": "I will search."},
                                {"functionCall": {"name": "search_files", "args": {"query": "viewer"}}},
                            ]
                        }
                    }
                ]
            },
        },
    }

    openai = extract_metadata(json.dumps(openai_record))
    gemini = extract_metadata(json.dumps(gemini_record))

    assert openai is not None
    assert openai["assistant_hint"] == "Checking now."
    assert openai["tool_calls"][0]["name"] == "read_file"
    assert openai["tool_calls"][0]["input_preview"] == '{"path":"README.md"}'
    assert gemini is not None
    assert gemini["assistant_hint"] == "I will search."
    assert gemini["tool_names"] == ["search_files"]
    assert gemini["tool_calls"][0]["name"] == "search_files"


def test_extract_metadata_normalizes_openai_chat_and_gemini_usage():
    base = {
        "request": {"method": "POST", "path": "/v1/chat/completions", "headers": {}, "body": {}},
        "response": {"status": 200, "headers": {}, "body": {}},
    }
    openai_record = {
        **base,
        "response": {**base["response"], "body": {"usage": {"prompt_tokens": 11, "completion_tokens": 7}}},
    }
    gemini_record = {
        **base,
        "request": {**base["request"], "path": "/v1beta/models/gemini:generateContent"},
        "response": {
            **base["response"],
            "body": {
                "usageMetadata": {
                    "promptTokenCount": 13,
                    "candidatesTokenCount": 5,
                    "cachedContentTokenCount": 3,
                }
            },
        },
    }

    openai = extract_metadata(json.dumps(openai_record))
    gemini = extract_metadata(json.dumps(gemini_record))

    assert openai is not None and (openai["input_tokens"], openai["output_tokens"]) == (11, 7)
    assert gemini is not None and (
        gemini["input_tokens"],
        gemini["output_tokens"],
        gemini["cache_read_input_tokens"],
    ) == (13, 5, 3)
    assert gemini["model"] == "gemini"


def test_extract_metadata_normalizes_logical_session_and_capture_fallback():
    explicit_record = {
        "request": {
            "method": "POST",
            "path": "/v1/messages",
            "headers": {
                "User-Agent": "claude-cli/2.1.234",
                "X-Claude-Code-Session-Id": "session-123",
            },
            "body": {"messages": [{"role": "user", "content": "Explain this failure"}]},
        },
        "response": {"status": 200, "headers": {}, "body": {}},
    }
    fallback_record = {
        "request": {
            "method": "POST",
            "path": "/v1beta/models/gemini:generateContent",
            "headers": {"User-Agent": "GeminiCLI/0.40.1"},
            "body": {"contents": [{"role": "user", "parts": [{"text": "Inspect this repo"}]}]},
        },
        "response": {"status": 200, "headers": {}, "body": {}},
    }

    explicit = extract_metadata(json.dumps(explicit_record), capture_id="2026-08-19/120000")
    fallback = extract_metadata(json.dumps(fallback_record), capture_id="2026-08-19/120000")

    assert explicit is not None
    assert explicit["session_key"] == "session:claude-code:session-123"
    assert explicit["session_id_source"] == "header.x-claude-code-session-id"
    assert explicit["user_hint"] == "Explain this failure"
    assert fallback is not None
    assert fallback["session_key"] == "capture:2026-08-19/120000"
    assert fallback["user_hint"] == "Inspect this repo"


def test_extract_metadata_strips_cli_context_from_session_title_hint():
    record = {
        "request": {
            "method": "POST",
            "path": "/v1/messages",
            "headers": {"User-Agent": "claude-cli/2.1.234"},
            "body": {
                "messages": [
                    {
                        "role": "user",
                        "content": (
                            "<system-reminder>Large generated context</system-reminder>\n\n"
                            "Please explain the failing test"
                        ),
                    }
                ]
            },
        },
        "response": {"status": 200, "headers": {}, "body": {}},
    }

    meta = extract_metadata(json.dumps(record), capture_id="capture")

    assert meta is not None
    assert meta["user_hint"] == "Please explain the failing test"


def test_extract_metadata_unwraps_chat_gateway_metadata():
    record = {
        "request": {
            "method": "POST",
            "path": "/v1/messages",
            "headers": {},
            "body": {
                "messages": [
                    {
                        "role": "user",
                        "content": (
                            'Conversation info (untrusted metadata):\n```json\n{"message_id":"m1"}\n```\n\n'
                            "[message_id: m1]\nuser_123: 搜索深圳天气看看"
                        ),
                    }
                ]
            },
        },
        "response": {"status": 200, "headers": {}, "body": {}},
    }

    meta = extract_metadata(json.dumps(record), capture_id="capture")

    assert meta is not None
    assert meta["user_hint"] == "搜索深圳天气看看"


def test_extract_metadata_uses_actual_user_after_codex_and_gemini_context_messages():
    for context in (
        (
            "<recommended_plugins>Generated plugin catalog</recommended_plugins>\n"
            "# AGENTS.md instructions for /repo\n<INSTRUCTIONS>Generated rules</INSTRUCTIONS>"
        ),
        "<session_context>Generated Gemini context</session_context>",
        "<user_info>Generated Grok context</user_info><rules>Generated rules</rules>",
    ):
        record = {
            "request": {
                "method": "POST",
                "path": "/v1/responses",
                "headers": {},
                "body": {
                    "input": [
                        {"type": "message", "role": "user", "content": context},
                        {"type": "message", "role": "user", "content": "Fix the parser"},
                    ]
                },
            },
            "response": {"status": 200, "headers": {}, "body": {}},
        }
        meta = extract_metadata(json.dumps(record), capture_id="capture")
        assert meta is not None
        assert meta["user_hint"] == "Fix the parser"


def test_title_generation_is_classified_and_not_used_as_session_title(tmp_path: Path):
    path = tmp_path / "trace_120000.jsonl"
    title_record = {
        "timestamp": "2026-08-19T12:00:00+00:00",
        "request": {
            "method": "POST",
            "path": "/v1/messages",
            "headers": {"X-Claude-Code-Session-Id": "s1", "User-Agent": "claude-cli/2.1.234"},
            "body": {
                "messages": [
                    {
                        "role": "user",
                        "content": "<session>Fix the parser</session> Write the title in the predominant language of the session",
                    }
                ]
            },
        },
        "response": {"status": 200, "headers": {}, "body": {}},
    }
    main_record = {
        **title_record,
        "timestamp": "2026-08-19T12:00:01+00:00",
        "request": {
            **title_record["request"],
            "body": {"messages": [{"role": "user", "content": "Fix the parser"}]},
        },
    }
    path.write_text(json.dumps(title_record) + "\n" + json.dumps(main_record) + "\n", encoding="utf-8")

    metadata = TraceCatalog().index(path, "capture").metadata
    summaries = summarize_sessions(metadata)

    assert metadata[0]["task_kind"] == "title-generation"
    assert summaries[0]["title"] == "Fix the parser"


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
                            {
                                "type": "namespace",
                                "name": "collaboration",
                                "tools": [
                                    {
                                        "type": "function",
                                        "name": "spawn_agent",
                                        "parameters": {"type": "object"},
                                    }
                                ],
                            },
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
    assert meta["tool_names"] == ["exec", "wait", "collaboration.spawn_agent"]


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


def test_extract_metadata_separates_auxiliary_traffic_from_session_fallback():
    record = {
        "turn": 1,
        "request": {
            "method": "GET",
            "path": "/api/v1/models",
            "headers": {"User-Agent": "grok-shell/1.0.3"},
            "body": None,
        },
        "response": {"status": 200, "headers": {}, "body": {}},
    }

    meta = extract_metadata(json.dumps(record), capture_id="2026-08-19/120000")

    assert meta is not None
    assert meta["request_category"] == "auxiliary"
    assert meta["session_key"] == "auxiliary:2026-08-19/120000"


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


def test_render_indexes_stream_errors_before_stripping_events(trace_dir: Path):
    jsonl = trace_dir / "trace_120001.jsonl"
    record = {
        "turn": 1,
        "request": {
            "method": "POST",
            "path": "/v1/messages",
            "headers": {},
            "body": {"model": "x", "messages": [{"role": "user", "content": "hello"}]},
        },
        "response": {
            "status": 200,
            "headers": {"content-type": "text/event-stream"},
            "body": None,
            "sse_events": [
                {
                    "event": "error",
                    "data": {
                        "type": "error",
                        "error": {"type": "overloaded_error", "message": "Overloaded"},
                    },
                }
            ],
        },
    }
    jsonl.write_text(json.dumps(record) + "\n", encoding="utf-8")
    out = jsonl.with_suffix(".html")

    assert render_html(jsonl, out)
    block = _injected_block(out.read_text(encoding="utf-8"))

    assert '"error_message":"Overloaded"' in block
    assert '"sse_events"' not in block


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
