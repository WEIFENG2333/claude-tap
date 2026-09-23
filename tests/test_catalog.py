from __future__ import annotations

import json
from pathlib import Path

from claude_tap.catalog import TraceCatalog, summarize_sessions


def _record(session_id: str | None, message: str, *, turn: int = 1, status: int = 200) -> dict:
    headers = {"User-Agent": "claude-cli/2.1.234"}
    if session_id:
        headers["X-Claude-Code-Session-Id"] = session_id
    return {
        "timestamp": f"2026-08-19T12:00:0{turn}+00:00",
        "request_id": f"req_{turn}",
        "turn": turn,
        "duration_ms": turn * 10,
        "request": {
            "method": "POST",
            "path": "/v1/messages",
            "headers": headers,
            "body": {"model": "claude-test", "messages": [{"role": "user", "content": message}]},
        },
        "response": {
            "status": status,
            "headers": {},
            "body": {"usage": {"input_tokens": turn, "output_tokens": turn + 1}},
        },
    }


def _append(path: Path, record: dict) -> None:
    with open(path, "a", encoding="utf-8") as output:
        output.write(json.dumps(record) + "\n")


def test_catalog_indexes_and_reads_one_record_without_loading_api_page(tmp_path: Path):
    path = tmp_path / "trace_120000.jsonl"
    _append(path, _record("s1", "First prompt", turn=1))
    _append(path, _record("s2", "Second prompt", turn=2))
    catalog = TraceCatalog()

    indexed = catalog.index(path, "2026-08-19/120000")

    assert len(indexed.metadata) == 2
    assert indexed.metadata[0]["session_key"] == "session:claude-code:s1"
    assert catalog.read_record(path, "2026-08-19/120000", 1)["request_id"] == "req_2"
    assert catalog.read_record(path, "2026-08-19/120000", 99) is None


def test_catalog_extends_existing_index_when_live_file_grows(tmp_path: Path):
    path = tmp_path / "trace_120000.jsonl"
    _append(path, _record("s1", "First prompt", turn=1))
    catalog = TraceCatalog()
    first = catalog.index(path, "capture")

    _append(path, _record("s1", "Follow-up", turn=2))
    second = catalog.index(path, "capture")

    assert len(first.metadata) == 1
    assert len(second.metadata) == 2
    assert second.metadata[1]["record_index"] == 1


def test_session_summary_groups_explicit_ids_and_capture_fallback(tmp_path: Path):
    path = tmp_path / "trace_120000.jsonl"
    _append(path, _record("s1", "Investigate latency", turn=1))
    _append(path, _record("s1", "Follow-up", turn=2, status=500))
    _append(path, _record("s2", "Refactor parser", turn=3))
    catalog = TraceCatalog()
    metadata = catalog.index(path, "2026-08-19/120000").metadata
    metadata[0]["cache_read_input_tokens"] = 5
    metadata[1]["cache_creation_input_tokens"] = 7

    summaries = summarize_sessions(metadata)

    assert [item["session_id"] for item in summaries] == ["s1", "s2"]
    assert summaries[0]["title"] == "Investigate latency"
    assert summaries[0]["record_count"] == 2
    assert summaries[0]["error_count"] == 1
    assert summaries[0]["input_tokens"] == 15
