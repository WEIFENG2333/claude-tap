"""Uniform tool-icon coverage shared by every viewer surface."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from claude_tap.viewer import render_html

playwright = pytest.importorskip("playwright.sync_api")


def test_tool_icons_use_one_quiet_wrench_on_every_viewer_surface(tmp_path: Path) -> None:
    record = {
        "timestamp": "2026-08-23T14:00:00+08:00",
        "request_id": "req_semantic_icons",
        "turn": 1,
        "duration_ms": 42,
        "request": {
            "method": "POST",
            "path": "/v1/messages",
            "headers": {},
            "body": {
                "model": "claude-test",
                "messages": [
                    {
                        "role": "assistant",
                        "content": [
                            {
                                "type": "tool_use",
                                "id": "toolu_read",
                                "name": "Read",
                                "input": {"file_path": "/tmp/example.py"},
                            }
                        ],
                    },
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": "toolu_read",
                                "content": "example contents",
                            }
                        ],
                    },
                ],
                "tools": [
                    {
                        "name": "Read",
                        "description": "Read a file.",
                        "input_schema": {"type": "object", "properties": {}},
                    },
                    {
                        "name": "mcp__vendor__custom_operation",
                        "description": "An MCP extension.",
                        "input_schema": {"type": "object", "properties": {}},
                    },
                    {
                        "name": "unknown_vendor_tool",
                        "description": "An unclassified extension.",
                        "input_schema": {"type": "object", "properties": {}},
                    },
                ],
            },
        },
        "response": {
            "status": 200,
            "headers": {},
            "body": {
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_shell",
                        "name": "Bash",
                        "input": {"command": "pwd"},
                    }
                ]
            },
        },
    }
    trace = tmp_path / "trace_semantic_icons.jsonl"
    trace.write_text(json.dumps(record) + "\n", encoding="utf-8")
    output = trace.with_suffix(".html")
    assert render_html(trace, output)

    with playwright.sync_playwright() as runtime:
        try:
            browser = runtime.chromium.launch(headless=True)
        except playwright.Error as exc:
            pytest.skip(f"Playwright Chromium is unavailable: {exc}")
        page = browser.new_page(viewport={"width": 1440, "height": 800})
        page.goto(output.as_uri())

        row = page.locator(".trajectory-row").first
        assert row.locator(".row-tool-icon .ui-icon").count() == 1
        wrench_glyph = row.locator(".row-tool-icon .ui-icon").evaluate("node => node.innerHTML")
        row.click()
        page.locator('#workspace[data-inspector="open"]').wait_for()

        message_icons = page.locator(".message-tool-block .tool-glyph .ui-icon")
        assert message_icons.count() == 3
        assert message_icons.evaluate_all(
            "(nodes, glyph) => nodes.every(node => node.innerHTML === glyph)", wrench_glyph
        )

        page.locator('[data-tab="tools"]').click()
        definitions = page.locator(".tool-definition")
        definitions.first.wait_for()
        mounted_icons = definitions.locator(".tool-glyph .ui-icon")
        assert mounted_icons.count() == 3
        assert mounted_icons.evaluate_all(
            "(nodes, glyph) => nodes.every(node => node.innerHTML === glyph)", wrench_glyph
        )

        browser.close()
