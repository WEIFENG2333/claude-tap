from __future__ import annotations

import json
from pathlib import Path

from claude_tap.prompt_snapshot import infer_provider, render_prompt_markdown, snapshot_from_records


def _record(path: str, body: dict, *, turn: int = 1) -> dict:
    return {
        "timestamp": "2026-05-21T10:00:00+00:00",
        "request_id": f"req_{turn}",
        "turn": turn,
        "duration_ms": 1,
        "request": {"method": "POST", "path": path, "headers": {}, "body": body},
        "response": {"status": 200, "headers": {}, "body": {}},
        "upstream_base_url": "https://upstream.example.com",
    }


def test_anthropic_snapshot_selects_tool_bearing_request():
    light = _record(
        "/v1/messages?beta=true",
        {
            "model": "claude-haiku",
            "system": [{"type": "text", "text": "probe system"}],
            "messages": [{"role": "user", "content": "probe"}],
        },
        turn=1,
    )
    full = _record(
        "/v1/messages?beta=true",
        {
            "model": "claude-opus",
            "system": [{"type": "text", "text": "main system"}, {"type": "text", "text": "second block"}],
            "messages": [{"role": "user", "content": [{"type": "text", "text": "hello"}]}],
            "tools": [
                {
                    "name": "Bash",
                    "description": "Run shell commands",
                    "input_schema": {"type": "object", "properties": {"cmd": {"type": "string"}}},
                }
            ],
        },
        turn=2,
    )

    snapshot = snapshot_from_records([light, full])

    assert snapshot.provider == "anthropic"
    assert snapshot.model == "claude-opus"
    assert snapshot.turn == 2
    assert snapshot.system_prompt == "main system\n\nsecond block"
    assert snapshot.user_message == "hello"
    assert len(snapshot.tools) == 1
    assert snapshot.tools[0].name == "Bash"
    assert snapshot.tools[0].schema["properties"]["cmd"]["type"] == "string"


def test_openai_responses_snapshot_extracts_instructions_roles_and_tools():
    record = _record(
        "/v1/responses",
        {
            "model": "gpt-5.4",
            "instructions": "top instructions",
            "input": [
                {"role": "developer", "content": [{"type": "input_text", "text": "developer rules"}]},
                {"role": "system", "content": "system from input"},
                {"role": "user", "content": [{"type": "input_text", "text": "do the thing"}]},
            ],
            "tools": [
                {
                    "type": "function",
                    "name": "update_plan",
                    "description": "Update a plan",
                    "parameters": {"type": "object", "properties": {"plan": {"type": "array"}}},
                }
            ],
        },
    )

    snapshot = snapshot_from_records([record])

    assert snapshot.provider == "openai"
    assert snapshot.system_prompt == "top instructions\n\nsystem from input"
    assert snapshot.developer_prompt == "developer rules"
    assert snapshot.user_message == "do the thing"
    assert snapshot.tools[0].name == "update_plan"
    assert snapshot.tools[0].schema["properties"]["plan"]["type"] == "array"


def test_openai_responses_snapshot_extracts_additional_tools_from_input():
    probe = _record(
        "/v1/responses",
        {
            "model": "gpt-5.4",
            "input": [
                {"role": "developer", "content": "probe rules"},
                {"role": "user", "content": "probe"},
            ],
        },
        turn=1,
    )
    record = _record(
        "/v1/responses",
        {
            "model": "gpt-5.4",
            "input": [
                {
                    "type": "additional_tools",
                    "role": "developer",
                    "tools": [
                        {
                            "type": "custom",
                            "name": "exec",
                            "description": "Run tool calls",
                            "format": {"type": "grammar", "syntax": "lark"},
                        },
                        {
                            "type": "function",
                            "name": "wait",
                            "description": "Wait for a running call",
                            "parameters": {"type": "object", "properties": {"cell_id": {"type": "string"}}},
                        },
                        {
                            "type": "namespace",
                            "name": "collaboration",
                            "description": "Collaborate with other agents",
                            "tools": [{"type": "function", "name": "spawn_agent"}],
                        },
                    ],
                },
                {"role": "developer", "content": [{"type": "input_text", "text": "developer rules"}]},
                {"role": "user", "content": [{"type": "input_text", "text": "do the thing"}]},
            ],
        },
        turn=2,
    )

    snapshot = snapshot_from_records([probe, record])
    markdown = render_prompt_markdown(snapshot)

    assert snapshot.turn == 2
    assert snapshot.developer_prompt == "developer rules"
    assert snapshot.user_message == "do the thing"
    assert [tool.name for tool in snapshot.tools] == ["exec", "wait", "collaboration"]
    assert snapshot.tools[1].schema["properties"]["cell_id"]["type"] == "string"
    assert "## collaboration" in markdown
    assert "_No tools captured._" not in markdown


def test_openai_chat_completions_snapshot_extracts_messages_and_function_tool():
    record = _record(
        "/v1/chat/completions",
        {
            "model": "gpt-4o-mini",
            "messages": [
                {"role": "system", "content": "chat system"},
                {"role": "developer", "content": [{"type": "text", "text": "chat developer"}]},
                {"role": "user", "content": "chat user"},
            ],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "lookup",
                        "description": "Lookup data",
                        "parameters": {"type": "object", "properties": {"id": {"type": "string"}}},
                    },
                }
            ],
        },
    )

    snapshot = snapshot_from_records([record])

    assert snapshot.provider == "openai"
    assert snapshot.system_prompt == "chat system"
    assert snapshot.developer_prompt == "chat developer"
    assert snapshot.user_message == "chat user"
    assert snapshot.tools[0].name == "lookup"
    assert snapshot.tools[0].schema["properties"]["id"]["type"] == "string"


def test_prefixed_chat_completions_path_is_openai():
    record = _record(
        "/coding/v1/chat/completions",
        {"model": "kimi-for-coding", "messages": [{"role": "system", "content": "kimi system"}]},
    )

    snapshot = snapshot_from_records([record])

    assert snapshot.provider == "openai"
    assert snapshot.system_prompt == "kimi system"


def test_gemini_snapshot_extracts_system_contents_and_function_declarations():
    record = _record(
        "/v1beta/models/gemini-2.5-pro:streamGenerateContent?alt=sse",
        {
            "system_instruction": {"parts": [{"text": "gemini system"}]},
            "contents": [
                {"role": "user", "parts": [{"text": "hello "}, {"text": "gemini"}]},
                {"role": "model", "parts": [{"text": "ignored"}]},
            ],
            "tools": [
                {
                    "function_declarations": [
                        {
                            "name": "search",
                            "description": "Search things",
                            "parameters": {"type": "object", "properties": {"q": {"type": "string"}}},
                        }
                    ]
                }
            ],
        },
    )

    snapshot = snapshot_from_records([record])

    assert snapshot.provider == "gemini"
    assert snapshot.model == "gemini-2.5-pro"
    assert snapshot.system_prompt == "gemini system"
    assert snapshot.user_message == "hello\n\ngemini"
    assert snapshot.tools[0].name == "search"


def test_gemini_snapshot_accepts_cli_camel_case_fields():
    record = _record(
        "/v1beta/models/gemini-3.1-pro-preview:streamGenerateContent?alt=sse",
        {
            "systemInstruction": {"parts": [{"text": "gemini cli system"}]},
            "contents": [{"role": "user", "parts": [{"text": "cli prompt"}]}],
            "tools": [
                {
                    "functionDeclarations": [
                        {
                            "name": "read_file",
                            "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
                        }
                    ]
                }
            ],
        },
    )

    snapshot = snapshot_from_records([record])

    assert snapshot.system_prompt == "gemini cli system"
    assert snapshot.user_message == "cli prompt"
    assert snapshot.tools[0].name == "read_file"
    assert snapshot.tools[0].schema["properties"]["path"]["type"] == "string"


def test_antigravity_nested_request_exports_as_gemini_prompt():
    record = _record(
        "/v1internal:streamGenerateContent?alt=sse",
        {
            "model": "MODEL_GOOGLE_GEMINI_2_5_FLASH",
            "request": {
                "systemInstruction": {"parts": [{"text": "antigravity system"}]},
                "contents": [{"role": "user", "parts": [{"text": "inspect the repo"}]}],
                "tools": [
                    {
                        "functionDeclarations": [
                            {
                                "name": "read_file",
                                "description": "Read a file",
                                "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
                            }
                        ]
                    }
                ],
            },
        },
    )

    snapshot = snapshot_from_records([record])

    assert snapshot.provider == "gemini"
    assert snapshot.model == "MODEL_GOOGLE_GEMINI_2_5_FLASH"
    assert snapshot.system_prompt == "antigravity system"
    assert snapshot.user_message == "inspect the repo"
    assert snapshot.tools[0].name == "read_file"


def test_provider_inference_can_fall_back_to_body_shape():
    assert infer_provider(_record("/custom", {"system": "s", "messages": []})) == "anthropic"
    assert infer_provider(_record("/custom", {"instructions": "i", "input": []})) == "openai"
    assert infer_provider(_record("/custom", {"system_instruction": {}, "contents": []})) == "gemini"
    assert infer_provider(_record("/custom", {"systemInstruction": {}, "contents": []})) == "gemini"
    assert infer_provider(_record("/v1internal:streamGenerateContent", {"request": {"contents": []}})) == "gemini"


def test_prompt_markdown_is_comparison_oriented_and_includes_raw_schema():
    snapshot = snapshot_from_records(
        [
            _record(
                "/v1/messages",
                {
                    "model": "claude",
                    "system": "# sys\ncontent",
                    "messages": [{"role": "user", "content": "hi"}],
                    "tools": [
                        {
                            "name": "Read",
                            "description": "# Read files",
                            "input_schema": {"type": "object"},
                        }
                    ],
                },
            )
        ]
    )

    out = render_prompt_markdown(snapshot)

    assert "# Prompt Snapshot" not in out
    assert "Request ID" not in out
    assert "Captured" not in out
    assert "# System Prompt" in out
    assert "## sys" in out
    assert "## Read" in out
    assert "### Read files" in out
    assert '"type": "object"' in out


def test_prompt_md_export_format(tmp_path: Path):
    from claude_tap.export import export

    trace = tmp_path / "trace.jsonl"
    trace.write_text(
        json.dumps(
            _record(
                "/v1/messages",
                {
                    "model": "claude",
                    "system": "system text",
                    "messages": [{"role": "user", "content": "hello"}],
                },
            )
        )
        + "\n",
        encoding="utf-8",
    )
    out = tmp_path / "snapshot.prompt.md"

    rc = export(trace, output=out, fmt=None)

    assert rc == 0
    text = out.read_text(encoding="utf-8")
    assert "# Prompt Snapshot" not in text
    assert "# System Prompt" in text
    assert "system text" in text
