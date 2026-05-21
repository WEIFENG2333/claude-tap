"""End-to-end CLI checks via subprocess (no internal imports).

These are the only tests that actually exercise the installed entry point,
so they catch packaging-level regressions (entry point name, module
discovery, missing data files).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def _run_cli(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "claude_tap", *args],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=str(cwd) if cwd else None,
    )


def test_cli_help_lists_subcommands(tmp_path: Path):
    p = _run_cli("--help", cwd=tmp_path)
    assert p.returncode == 0
    for cmd in ("run", "proxy", "live", "export", "update", "ca"):
        assert cmd in p.stdout, f"top-level help should mention {cmd}"


def test_cli_version(tmp_path: Path):
    p = _run_cli("--version", cwd=tmp_path)
    assert p.returncode == 0
    assert "claude-tap" in p.stdout


def test_cli_export_markdown_via_module(tmp_path: Path, sample_jsonl: Path):
    p = _run_cli("export", str(sample_jsonl), cwd=tmp_path)
    assert p.returncode == 0
    assert "# claude-tap trace" in p.stdout
    assert "Turn 1" in p.stdout


def test_cli_module_propagates_nonzero_return_code(tmp_path: Path):
    p = _run_cli("export", str(tmp_path / "missing.jsonl"), cwd=tmp_path)
    assert p.returncode == 1
    assert "not found" in p.stderr


def test_cli_export_json(tmp_path: Path, sample_jsonl: Path):
    out = tmp_path / "out.json"
    p = _run_cli("export", str(sample_jsonl), "-o", str(out), cwd=tmp_path)
    assert p.returncode == 0, p.stderr
    parsed = json.loads(out.read_text(encoding="utf-8"))
    assert isinstance(parsed, list)
    assert parsed[0]["model"] == "claude-opus-4-6"


def test_cli_ca_path_prints_existing_path(tmp_path: Path, monkeypatch):
    # Force the CA into a sandboxed directory so this test never touches the
    # user's real ~/.local/share/claude-tap.
    sandbox = tmp_path / "data"
    monkeypatch.setenv("XDG_DATA_HOME", str(sandbox))
    p = _run_cli("ca", "path", cwd=tmp_path)
    assert p.returncode == 0
    out = p.stdout.strip()
    assert out.endswith("ca.pem")
    assert Path(out).exists()
