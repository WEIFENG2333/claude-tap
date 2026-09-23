"""Memory-bounded indexes for trace captures and logical sessions."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from claude_tap.viewer import extract_metadata


@dataclass(frozen=True)
class RecordLocation:
    offset: int
    length: int


@dataclass
class CaptureIndex:
    capture_id: str
    path: Path
    device: int
    inode: int
    modified_ns: int
    file_size: int
    scanned_size: int
    metadata: list[dict]
    locations: list[RecordLocation]


def summarize_sessions(metadata: list[dict]) -> list[dict]:
    """Aggregate request metadata into stable, user-facing session rows."""

    groups: dict[str, dict] = {}
    for item in metadata:
        key = str(item.get("session_key") or "capture:standalone")
        summary = groups.get(key)
        if summary is None:
            summary = {
                "key": key,
                "session_id": item.get("session_id") or "",
                "session_source": item.get("session_id_source") or "capture",
                "session_confidence": item.get("session_id_confidence") or "fallback",
                "client": item.get("client") or "unknown",
                "client_version": item.get("client_version") or "",
                "protocol": item.get("protocol") or "unknown",
                "title": "",
                "started_at": item.get("timestamp") or "",
                "ended_at": item.get("timestamp") or "",
                "first_record_index": item.get("record_index", 0),
                "last_record_index": item.get("record_index", 0),
                "record_count": 0,
                "error_count": 0,
                "duration_ms": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "models": [],
                "thread_count": 0,
                "agent_count": 0,
                "request_category": item.get("request_category") or "model",
                "_threads": set(),
                "_agents": set(),
            }
            groups[key] = summary

        summary["record_count"] += 1
        if item.get("request_category") == "model":
            summary["request_category"] = "model"
        summary["last_record_index"] = item.get("record_index", summary["last_record_index"])
        summary["ended_at"] = item.get("timestamp") or summary["ended_at"]
        summary["duration_ms"] += int(item.get("duration_ms") or 0)
        summary["input_tokens"] += sum(
            int(item.get(field) or 0)
            for field in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
        )
        summary["output_tokens"] += int(item.get("output_tokens") or 0)
        if int(item.get("status") or 0) >= 400 or item.get("error_message"):
            summary["error_count"] += 1
        model = str(item.get("model") or "")
        if model and model not in summary["models"]:
            summary["models"].append(model)
        thread_id = str(item.get("thread_id") or "")
        agent_id = str(item.get("agent_id") or "")
        if thread_id:
            summary["_threads"].add(thread_id)
        if agent_id:
            summary["_agents"].add(agent_id)
        if (
            not summary["title"]
            and item.get("user_hint")
            and item.get("task_kind") not in {"title-generation", "compaction"}
        ):
            summary["title"] = item["user_hint"]

    ordered = sorted(groups.values(), key=lambda item: int(item["first_record_index"]))
    for summary in ordered:
        summary["thread_count"] = len(summary.pop("_threads"))
        summary["agent_count"] = len(summary.pop("_agents"))
        if summary["request_category"] == "auxiliary":
            summary["title"] = "Auxiliary traffic"
            summary["session_source"] = "capture.auxiliary"
            summary["session_confidence"] = "not-applicable"
        elif not summary["title"]:
            identifier = str(summary["session_id"] or summary["key"].removeprefix("capture:"))
            summary["title"] = f"{summary['client']} · {identifier[-8:]}"
    return ordered


class TraceCatalog:
    """Indexes JSONL byte offsets and metadata, incrementally for live files."""

    def __init__(self) -> None:
        self._indexes: dict[Path, CaptureIndex] = {}

    def index(self, path: Path, capture_id: str) -> CaptureIndex:
        resolved = path.resolve()
        stat = resolved.stat()
        cached = self._indexes.get(resolved)
        if cached and cached.modified_ns == stat.st_mtime_ns and cached.file_size == stat.st_size:
            return cached

        can_append = bool(
            cached and cached.device == stat.st_dev and cached.inode == stat.st_ino and stat.st_size > cached.file_size
        )
        metadata = list(cached.metadata) if can_append and cached else []
        locations = list(cached.locations) if can_append and cached else []
        start = cached.scanned_size if can_append and cached else 0
        scanned_size = start

        with open(resolved, "rb") as source:
            source.seek(start)
            while True:
                offset = source.tell()
                line = source.readline()
                if not line:
                    scanned_size = source.tell()
                    break
                if not line.endswith(b"\n"):
                    scanned_size = offset
                    break
                scanned_size = source.tell()
                raw = line.strip()
                if not raw:
                    continue
                try:
                    decoded = raw.decode("utf-8")
                except UnicodeDecodeError:
                    continue
                item = extract_metadata(decoded, capture_id=capture_id)
                if item is None:
                    continue
                item["record_index"] = len(metadata)
                metadata.append(item)
                locations.append(RecordLocation(offset=offset, length=len(line)))

        indexed = CaptureIndex(
            capture_id=capture_id,
            path=resolved,
            device=stat.st_dev,
            inode=stat.st_ino,
            modified_ns=stat.st_mtime_ns,
            file_size=stat.st_size,
            scanned_size=scanned_size,
            metadata=metadata,
            locations=locations,
        )
        self._indexes[resolved] = indexed
        return indexed

    def read_record(self, path: Path, capture_id: str, record_index: int) -> dict | None:
        indexed = self.index(path, capture_id)
        if record_index < 0 or record_index >= len(indexed.locations):
            return None
        location = indexed.locations[record_index]
        with open(indexed.path, "rb") as source:
            source.seek(location.offset)
            raw = source.read(location.length).strip()
        try:
            record = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None
        return record if isinstance(record, dict) else None

    def cached_count(self, path: Path) -> int | None:
        try:
            resolved = path.resolve()
            stat = resolved.stat()
        except OSError:
            return None
        cached = self._indexes.get(resolved)
        if cached and cached.modified_ns == stat.st_mtime_ns and cached.file_size == stat.st_size:
            return len(cached.metadata)
        return None
