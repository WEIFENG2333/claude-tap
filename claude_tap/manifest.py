"""Persistent index of recorded trace sessions, used to cap disk usage.

The index lives next to the trace files at ``<output_dir>/.claude-tap-manifest.json``.
For backwards compatibility we transparently read the legacy
``.cloudtap-manifest.json`` file if the new one is absent.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from claude_tap._version import __version__

MANIFEST_FILE = ".claude-tap-manifest.json"
LEGACY_MANIFEST_FILE = ".cloudtap-manifest.json"


def _path(output_dir: Path) -> Path:
    return output_dir / MANIFEST_FILE


def _legacy_path(output_dir: Path) -> Path:
    return output_dir / LEGACY_MANIFEST_FILE


def load(output_dir: Path) -> dict:
    """Read the manifest, falling back to the legacy filename.

    A blank manifest is created (and saved) on first call. We do *not* run
    orphan migration here — call :func:`migrate_orphans` explicitly when
    that behaviour is needed (typically once at CLI startup against a
    pre-existing ``.traces`` tree).
    """

    output_dir.mkdir(parents=True, exist_ok=True)
    primary = _path(output_dir)
    if primary.exists():
        try:
            data = json.loads(primary.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except (json.JSONDecodeError, OSError):
            pass

    legacy = _legacy_path(output_dir)
    if legacy.exists():
        try:
            data = json.loads(legacy.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                save(output_dir, data)
                try:
                    legacy.unlink()
                except OSError:
                    pass
                return data
        except (json.JSONDecodeError, OSError):
            pass

    fresh = {"_claude_tap": True, "version": __version__, "traces": []}
    save(output_dir, fresh)
    return fresh


def migrate_orphans(output_dir: Path) -> int:
    """Auto-register pre-existing trace files not yet in the manifest.

    Returns the count of newly registered orphan sessions.
    """

    manifest = load(output_dir)
    before = len(manifest.get("traces", []))
    _migrate_orphan_traces(output_dir, manifest)
    added = len(manifest.get("traces", [])) - before
    if added:
        save(output_dir, manifest)
    return added


def save(output_dir: Path, manifest: dict) -> None:
    _path(output_dir).write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def register(output_dir: Path, timestamp: str, files: list[str], *, metadata: dict | None = None) -> dict:
    manifest = load(output_dir)
    entry = {
        "timestamp": timestamp,
        "files": files,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    if metadata:
        entry.update(metadata)
    manifest.setdefault("traces", []).append(entry)
    save(output_dir, manifest)
    return manifest


def cleanup(output_dir: Path, max_traces: int) -> int:
    """Drop the oldest sessions exceeding ``max_traces``. Returns count removed."""
    if max_traces <= 0:
        return 0
    manifest = load(output_dir)
    traces = list(manifest.get("traces", []))
    if len(traces) <= max_traces:
        return 0
    traces.sort(key=lambda t: t.get("timestamp", ""))
    drop = traces[: len(traces) - max_traces]
    keep = traces[len(traces) - max_traces :]

    removed = 0
    for entry in drop:
        parents: set[Path] = set()
        for rel in entry.get("files", []):
            fp = output_dir / rel
            if fp.exists():
                parents.add(fp.parent)
                try:
                    fp.unlink()
                except OSError:
                    pass
        for parent in parents:
            if parent != output_dir and parent.is_dir() and not any(parent.iterdir()):
                try:
                    parent.rmdir()
                except OSError:
                    pass
        removed += 1

    manifest["traces"] = keep
    save(output_dir, manifest)
    return removed


def _migrate_orphan_traces(output_dir: Path, manifest: dict) -> None:
    """Auto-register pre-existing trace files so cleanup respects them."""
    known: set[str] = set()
    for entry in manifest.get("traces", []):
        known.update(entry.get("files", []))

    for jsonl in sorted(output_dir.glob("**/trace_*.jsonl")):
        rel = str(jsonl.relative_to(output_dir))
        if rel in known or jsonl.name in known:
            continue
        stem = jsonl.stem
        ts = stem.replace("trace_", "", 1)
        if jsonl.parent != output_dir:
            ts = jsonl.parent.name.replace("-", "") + "_" + ts
        files = [rel]
        for suffix in (".log", ".html"):
            companion = jsonl.with_suffix(suffix)
            if companion.exists():
                files.append(str(companion.relative_to(output_dir)))
        manifest.setdefault("traces", []).append(
            {
                "timestamp": ts,
                "files": files,
                "created_at": datetime.fromtimestamp(jsonl.stat().st_mtime, tz=timezone.utc).isoformat(),
            }
        )
