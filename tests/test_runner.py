from __future__ import annotations

import asyncio
import json

import pytest

from claude_tap.cli import _export_prompt_from_trace
from claude_tap.clients import GEMINI_CLI, OPENCODE
from claude_tap.runner import run_client


@pytest.mark.asyncio
async def test_reverse_mode_child_env_does_not_inherit_outer_proxy(monkeypatch):
    captured: dict = {}

    class FakeProc:
        returncode = None
        pid = 12345

        async def wait(self) -> int:
            self.returncode = 0
            return 0

        def terminate(self) -> None:
            self.returncode = 143

        def kill(self) -> None:
            self.returncode = 137

    async def fake_create_subprocess_exec(*cmd, **kwargs):
        captured["cmd"] = cmd
        captured["env"] = kwargs["env"]
        return FakeProc()

    monkeypatch.setattr("claude_tap.runner.shutil.which", lambda _cmd: "/usr/bin/gemini")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setenv("HTTP_PROXY", "http://outer-proxy.example:8080")
    monkeypatch.setenv("HTTPS_PROXY", "http://outer-proxy.example:8080")
    monkeypatch.setenv("ALL_PROXY", "http://outer-proxy.example:8080")

    rc = await run_client(
        client=GEMINI_CLI,
        proxy_port=1234,
        proxy_host="127.0.0.1",
        forward_args=["-p", "hello"],
        proxy_mode="reverse",
        yolo=False,
    )

    assert rc == 0
    env = captured["env"]
    assert env["GOOGLE_GEMINI_BASE_URL"] == "http://127.0.0.1:1234"
    assert env["NO_PROXY"] == "127.0.0.1,localhost"
    assert env["no_proxy"] == "127.0.0.1,localhost"
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        assert key not in env


@pytest.mark.asyncio
async def test_yolo_args_can_be_inserted_after_client_subcommand(monkeypatch):
    captured: dict = {}

    class FakeProc:
        returncode = None
        pid = 12345

        async def wait(self) -> int:
            self.returncode = 0
            return 0

        def terminate(self) -> None:
            self.returncode = 143

        def kill(self) -> None:
            self.returncode = 137

    async def fake_create_subprocess_exec(*cmd, **kwargs):
        captured["cmd"] = cmd
        return FakeProc()

    monkeypatch.setattr("claude_tap.runner.shutil.which", lambda _cmd: "/usr/bin/opencode")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    rc = await run_client(
        client=OPENCODE,
        proxy_port=1234,
        proxy_host="127.0.0.1",
        forward_args=["run", "-m", "openai/gpt-4o-mini", "hello"],
        proxy_mode="forward",
        yolo=True,
    )

    assert rc == 0
    assert captured["cmd"][:3] == ("opencode", "run", "--dangerously-skip-permissions")


def test_export_prompt_from_trace_creates_parent_directory(tmp_path):
    trace = tmp_path / "trace.jsonl"
    trace.write_text(
        json.dumps(
            {
                "timestamp": "2026-05-21T10:00:00+00:00",
                "request_id": "req_1",
                "turn": 1,
                "request": {
                    "method": "POST",
                    "path": "/v1/messages",
                    "headers": {},
                    "body": {
                        "model": "claude-test",
                        "system": "system text",
                        "messages": [{"role": "user", "content": "hello"}],
                    },
                },
                "response": {"status": 502, "headers": {}, "body": {"error": "upstream failed"}},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    out = tmp_path / "nested" / "prompt.md"

    rc = _export_prompt_from_trace(trace, str(out))

    assert rc == 0
    assert "# Prompt Snapshot" in out.read_text(encoding="utf-8")
