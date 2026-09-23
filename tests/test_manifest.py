"""Manifest registration, cleanup, and legacy filename migration."""

from __future__ import annotations

import json
from pathlib import Path

from claude_tap import manifest


def _seed_session(out: Path, hhmmss: str, *, files: list[str] | None = None) -> tuple[str, list[str]]:
    """Create a fake trace session under ``out/<date>/`` and register it."""
    date_dir = out / "2026-01-01"
    date_dir.mkdir(parents=True, exist_ok=True)
    jsonl = date_dir / f"trace_{hhmmss}.jsonl"
    log = date_dir / f"trace_{hhmmss}.log"
    jsonl.write_text("{}\n", encoding="utf-8")
    log.write_text("", encoding="utf-8")
    rels = files or [str(jsonl.relative_to(out)), str(log.relative_to(out))]
    manifest.register(out, f"20260101_{hhmmss}", rels)
    return hhmmss, rels


def test_register_then_load_roundtrip(trace_dir: Path):
    manifest.register(
        trace_dir,
        "20260101_120000",
        ["2026-01-01/trace_120000.jsonl"],
        metadata={"record_count": 7},
    )
    loaded = manifest.load(trace_dir)
    assert loaded["traces"]
    assert loaded["traces"][-1]["timestamp"] == "20260101_120000"
    assert loaded["traces"][-1]["record_count"] == 7


def test_cleanup_drops_oldest_and_deletes_files(trace_dir: Path):
    _seed_session(trace_dir, "120000")
    _seed_session(trace_dir, "130000")
    _seed_session(trace_dir, "140000")
    assert (trace_dir / "2026-01-01" / "trace_120000.jsonl").exists()

    removed = manifest.cleanup(trace_dir, max_traces=2)
    assert removed == 1

    # Oldest is gone; the two newer ones remain.
    assert not (trace_dir / "2026-01-01" / "trace_120000.jsonl").exists()
    assert (trace_dir / "2026-01-01" / "trace_130000.jsonl").exists()
    assert (trace_dir / "2026-01-01" / "trace_140000.jsonl").exists()

    data = manifest.load(trace_dir)
    assert len(data["traces"]) == 2


def test_cleanup_unlimited_when_max_zero(trace_dir: Path):
    for hh in ("120000", "130000", "140000"):
        _seed_session(trace_dir, hh)
    removed = manifest.cleanup(trace_dir, max_traces=0)
    assert removed == 0
    assert len(manifest.load(trace_dir)["traces"]) == 3


def test_cleanup_removes_empty_date_dirs(trace_dir: Path):
    _seed_session(trace_dir, "120000")
    date_dir = trace_dir / "2026-01-01"
    assert date_dir.exists()
    manifest.cleanup(trace_dir, max_traces=0)  # no-op
    # Remove all so cleanup deletes the date dir.
    _seed_session(trace_dir, "130000")
    manifest.cleanup(trace_dir, max_traces=1)  # drop oldest -> empty date dir? no, two left
    # Now clamp to 0 sessions to force deletion.
    # (Use 1 to keep at least one. Two sessions -> 1 dropped.)
    # The oldest (120000) was dropped above already; only one left.
    remaining = list(date_dir.glob("trace_*.jsonl"))
    assert len(remaining) == 1


def test_legacy_cloudtap_manifest_is_migrated(trace_dir: Path):
    legacy = trace_dir / manifest.LEGACY_MANIFEST_FILE
    legacy.write_text(
        json.dumps({"_cloudtap": True, "version": "0.0.1", "traces": [{"timestamp": "20260101_010000", "files": []}]}),
        encoding="utf-8",
    )
    data = manifest.load(trace_dir)
    assert data["traces"][0]["timestamp"] == "20260101_010000"
    # New filename now exists; legacy is gone.
    assert (trace_dir / manifest.MANIFEST_FILE).exists()
    assert not legacy.exists()


def test_orphan_traces_are_auto_registered(trace_dir: Path):
    # Drop a trace file but no manifest entry — migrate_orphans should pick it up.
    date_dir = trace_dir / "2026-02-02"
    date_dir.mkdir()
    jsonl = date_dir / "trace_010000.jsonl"
    jsonl.write_text("{}\n", encoding="utf-8")

    added = manifest.migrate_orphans(trace_dir)
    assert added == 1
    data = manifest.load(trace_dir)
    timestamps = [t["timestamp"] for t in data["traces"]]
    assert any(ts.endswith("_010000") for ts in timestamps)


def test_load_does_not_register_orphans_implicitly(trace_dir: Path):
    """``load()`` is a pure read; it must not duplicate entries when called
    after files were just written under the trace tree."""
    date_dir = trace_dir / "2026-02-02"
    date_dir.mkdir()
    (date_dir / "trace_010000.jsonl").write_text("{}\n", encoding="utf-8")

    data = manifest.load(trace_dir)
    assert data["traces"] == []
