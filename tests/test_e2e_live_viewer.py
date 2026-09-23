"""LiveViewerServer + new ``/api/*`` protocol."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import aiohttp
import pytest

from claude_tap.live_viewer import LiveSink, LiveViewerServer
from claude_tap.trace import EventBus

pytestmark = pytest.mark.asyncio


def _seed_trace(output_dir: Path, date: str, hhmmss: str, count: int = 2) -> Path:
    """Write a fake trace file under ``output_dir/<date>/trace_<hhmmss>.jsonl``."""
    d = output_dir / date
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"trace_{hhmmss}.jsonl"
    with open(p, "w", encoding="utf-8") as f:
        for i in range(count):
            f.write(
                json.dumps(
                    {
                        "timestamp": f"2026-05-04T12:00:0{i}+00:00",
                        "turn": i + 1,
                        "request_id": f"req_{hhmmss}_{i}",
                        "request": {
                            "method": "POST",
                            "path": "/v1/messages",
                            "headers": {
                                "User-Agent": "claude-cli/2.1.234",
                                "X-Claude-Code-Session-Id": f"session-{hhmmss}",
                            },
                            "body": {
                                "model": "claude-test",
                                "messages": [{"role": "user", "content": f"Prompt {hhmmss}"}],
                            },
                        },
                        "response": {"status": 200, "headers": {}, "body": {}},
                    }
                )
                + "\n"
            )
    return p


async def test_api_version_endpoint(trace_dir: Path):
    server = LiveViewerServer(current_jsonl=None, port=0, host="127.0.0.1", output_dir=trace_dir)
    await server.start()
    try:
        async with aiohttp.ClientSession() as c, c.get(server.url + "/api/version") as r:
            assert r.status == 200
            assert r.headers["Cache-Control"] == "no-store"
            data = await r.json()
            assert "server" in data
            assert "schema" in data
            assert isinstance(data["schema"], int)
    finally:
        await server.stop()


async def test_api_sessions_lists_history_and_marks_current(trace_dir: Path):
    p1 = _seed_trace(trace_dir, "2026-05-04", "120000", count=3)
    p2 = _seed_trace(trace_dir, "2026-05-04", "130000", count=1)
    _seed_trace(trace_dir, "2026-05-03", "100000", count=2)

    # p2 is the live one
    server = LiveViewerServer(current_jsonl=p2, port=0, host="127.0.0.1", output_dir=trace_dir)
    await server.start()
    try:
        async with aiohttp.ClientSession() as c, c.get(server.url + "/api/sessions") as r:
            assert r.status == 200
            data = await r.json()
            assert data["current"] == "2026-05-04/130000"
            ids = [s["id"] for s in data["sessions"]]
            # Newest session first within each date.
            assert "2026-05-04/130000" in ids
            assert "2026-05-04/120000" in ids
            assert "2026-05-03/100000" in ids
            # record counts surfaced
            counts = {s["id"]: s["record_count"] for s in data["sessions"]}
            assert counts["2026-05-04/120000"] == 3
            assert counts["2026-05-04/130000"] == 1
    finally:
        await server.stop()
    _ = p1  # silence unused


async def test_api_session_records_returns_jsonl_contents(trace_dir: Path):
    p = _seed_trace(trace_dir, "2026-05-04", "120000", count=2)
    server = LiveViewerServer(current_jsonl=p, port=0, host="127.0.0.1", output_dir=trace_dir)
    await server.start()
    try:
        async with aiohttp.ClientSession() as c, c.get(server.url + "/api/sessions/2026-05-04/120000") as r:
            assert r.status == 200
            data = await r.json()
            assert data["id"] == "2026-05-04/120000"
            assert len(data["records"]) == 2
            assert data["records"][0]["turn"] == 1
    finally:
        await server.stop()


async def test_api_capture_index_groups_logical_sessions_and_record_detail(trace_dir: Path):
    p = _seed_trace(trace_dir, "2026-05-04", "121500", count=2)
    server = LiveViewerServer(current_jsonl=p, port=0, host="127.0.0.1", output_dir=trace_dir)
    await server.start()
    try:
        async with aiohttp.ClientSession() as client:
            async with client.get(server.url + "/api/captures") as response:
                assert response.status == 200
                captures = await response.json()
                assert captures["current"] == "2026-05-04/121500"
                assert captures["captures"][0]["id"] == "2026-05-04/121500"

            async with client.get(server.url + "/api/captures/2026-05-04/121500/index") as response:
                assert response.status == 200
                index = await response.json()
                assert index["capture"]["record_count"] == 2
                assert len(index["metadata"]) == 2
                assert len(index["sessions"]) == 1
                assert index["sessions"][0]["title"] == "Prompt 121500"
                assert index["sessions"][0]["session_id"] == "session-121500"

            async with client.get(server.url + "/api/captures/2026-05-04/121500/records/1") as response:
                assert response.status == 200
                detail = await response.json()
                assert detail["record_index"] == 1
                assert detail["record"]["turn"] == 2

            async with client.get(server.url + "/api/captures") as response:
                refreshed = await response.json()
                assert refreshed["captures"][0]["record_count"] == 2
    finally:
        await server.stop()


async def test_capture_catalog_does_not_scan_large_unindexed_history(trace_dir: Path):
    date_dir = trace_dir / "2026-05-04"
    date_dir.mkdir(parents=True)
    large = date_dir / "trace_122000.jsonl"
    with open(large, "wb") as output:
        output.write(b"{}\n")
        output.seek(9 * 1024 * 1024)
        output.write(b"\n")
    server = LiveViewerServer(current_jsonl=None, port=0, host="127.0.0.1", output_dir=trace_dir)
    await server.start()
    try:
        async with aiohttp.ClientSession() as client, client.get(server.url + "/api/captures") as response:
            assert response.status == 200
            payload = await response.json()
            capture = next(item for item in payload["captures"] if item["id"] == "2026-05-04/122000")
            assert capture["record_count"] is None
    finally:
        await server.stop()


async def test_api_session_records_rejects_path_traversal(trace_dir: Path):
    server = LiveViewerServer(current_jsonl=None, port=0, host="127.0.0.1", output_dir=trace_dir)
    await server.start()
    try:
        async with aiohttp.ClientSession() as c:
            # Attempt to escape via a date-shaped string + relative segment.
            # The hhmmss regex (^\d{6}$) refuses anything else.
            async with c.get(server.url + "/api/sessions/2026-05-04/abcdef") as r:
                assert r.status == 404
            async with c.get(server.url + "/api/sessions/not-a-date/120000") as r:
                assert r.status == 404
    finally:
        await server.stop()


async def test_api_stream_emits_hello_then_records(trace_dir: Path):
    p = _seed_trace(trace_dir, "2026-05-04", "150000", count=0)
    server = LiveViewerServer(current_jsonl=p, port=0, host="127.0.0.1", output_dir=trace_dir)
    await server.start()
    bus = EventBus()
    bus.subscribe(LiveSink(server))

    try:
        async with aiohttp.ClientSession() as client, client.get(server.url + "/api/stream", timeout=5) as resp:
            assert resp.status == 200
            # Read the hello frame.
            buf = b""
            async for chunk in resp.content.iter_any():
                buf += chunk
                if b"\n\n" in buf:
                    break
            assert b"event: hello" in buf
            assert b'"capture": "2026-05-04/150000"' in buf or b'"capture":"2026-05-04/150000"' in buf

            # Publish a record while the stream is open.
            await asyncio.sleep(0.05)
            await bus.publish({"turn": 99, "marker": "live"})
            buf2 = b""
            async for chunk in resp.content.iter_any():
                buf2 += chunk
                if b"event: record" in buf2 and b"\n\n" in buf2:
                    break
            assert b"event: record" in buf2
            assert b'"type":"record.appended"' in buf2
            assert b'"marker":"live"' in buf2
    finally:
        await server.stop()
        await bus.close_all()


async def test_index_injects_live_globals(trace_dir: Path):
    p = _seed_trace(trace_dir, "2026-05-04", "160000", count=0)
    server = LiveViewerServer(current_jsonl=p, port=0, host="127.0.0.1", output_dir=trace_dir)
    await server.start()
    try:
        async with aiohttp.ClientSession() as c, c.get(server.url + "/") as r:
            assert r.status == 200
            assert r.headers["Cache-Control"] == "no-store"
            html = await r.text()
            assert "LIVE_MODE = true" in html
            assert "CURRENT_CAPTURE_ID" in html
            assert "CURRENT_SESSION_ID" in html
            assert "2026-05-04/160000" in html
            assert "LIVE_SCHEMA" in html
    finally:
        await server.stop()
