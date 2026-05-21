"""Render a trace JSONL file as Markdown / JSON / prompt Markdown / HTML."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from claude_tap.prompt_snapshot import render_prompt_markdown, snapshot_from_records
from claude_tap.viewer import render_html


def export(
    trace_file: Path,
    *,
    output: Path | None,
    fmt: str | None,
    full_events: bool = False,
) -> int:
    if str(trace_file) == "-":
        records = _load_jsonl_lines(sys.stdin)
    else:
        if not trace_file.exists():
            sys.stderr.write(f"error: trace file not found: {trace_file}\n")
            return 1
        with open(trace_file, encoding="utf-8") as f:
            records = _load_jsonl_lines(f)

    if not records:
        sys.stderr.write("error: no valid records found in trace file\n")
        return 1
    records.sort(key=lambda r: r.get("turn", 0))

    fmt = fmt or _infer_format(output)
    if fmt == "html":
        if str(trace_file) == "-":
            sys.stderr.write("error: html export requires a real trace file path, not '-'\n")
            return 2
        html_path = output if output is not None else trace_file.with_suffix(".html")
        if not render_html(trace_file, html_path, strip_events=not full_events):
            sys.stderr.write("error: viewer template missing\n")
            return 2
        sys.stdout.write(f"wrote {html_path}\n")
        return 0

    is_prompt_snapshot = fmt == "prompt-md"
    if is_prompt_snapshot:
        try:
            text = render_prompt_markdown(snapshot_from_records(records))
        except ValueError as exc:
            sys.stderr.write(f"error: {exc}\n")
            return 1
    else:
        text = _to_json(records) if fmt == "json" else _to_markdown(records)
    if output is None or str(output) == "-":
        sys.stdout.write(text)
        if not text.endswith("\n"):
            sys.stdout.write("\n")
    else:
        output.write_text(text, encoding="utf-8")
        if is_prompt_snapshot:
            sys.stdout.write(f"exported prompt snapshot to {output}\n")
        else:
            sys.stdout.write(f"exported {len(records)} turns to {output}\n")
    return 0


def _load_jsonl_lines(stream) -> list[dict]:
    records: list[dict] = []
    for line in stream:
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records


def _infer_format(output: Path | None) -> str:
    if output is None:
        return "markdown"
    name = output.name.lower()
    if name.endswith((".prompt.md", ".prompt.markdown", ".system.md", ".system.markdown")):
        return "prompt-md"
    suffix = output.suffix.lower()
    if suffix == ".json":
        return "json"
    if suffix in (".html", ".htm"):
        return "html"
    return "markdown"


def _to_markdown(records: list[dict]) -> str:
    lines: list[str] = ["# claude-tap trace\n"]

    total_in = total_out = total_cr = total_cw = 0
    models: set[str] = set()
    for r in records:
        usage = (r.get("response") or {}).get("body") or {}
        if isinstance(usage, dict):
            u = usage.get("usage") or {}
            if isinstance(u, dict):
                total_in += int(u.get("input_tokens", 0) or 0)
                total_out += int(u.get("output_tokens", 0) or 0)
                total_cr += int(u.get("cache_read_input_tokens", 0) or 0)
                total_cw += int(u.get("cache_creation_input_tokens", 0) or 0)
        body = (r.get("request") or {}).get("body") or {}
        if isinstance(body, dict) and body.get("model"):
            models.add(body["model"])

    lines.append("## Summary\n")
    lines.append(f"- **Turns**: {len(records)}")
    lines.append(f"- **Models**: {', '.join(sorted(models)) if models else 'unknown'}")
    lines.append(f"- **Input tokens**: {total_in:,}")
    lines.append(f"- **Output tokens**: {total_out:,}")
    if total_cr:
        lines.append(f"- **Cache read tokens**: {total_cr:,}")
    if total_cw:
        lines.append(f"- **Cache create tokens**: {total_cw:,}")
    lines.append("")

    for r in records:
        turn = r.get("turn", "?")
        req_body = (r.get("request") or {}).get("body") or {}
        resp_body = (r.get("response") or {}).get("body") or {}
        model = req_body.get("model", "unknown") if isinstance(req_body, dict) else "unknown"
        duration = r.get("duration_ms", 0)

        lines.append(f"---\n\n## Turn {turn}\n")
        lines.append(f"**Model**: `{model}` | **Duration**: {duration}ms\n")

        msgs = req_body.get("messages", []) if isinstance(req_body, dict) else []
        if msgs:
            last = msgs[-1]
            role = last.get("role", "unknown")
            lines.append(f"### {role.title()}\n")
            content = last.get("content", "")
            if isinstance(content, str):
                lines.append(content + "\n")
            elif isinstance(content, list):
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") == "text":
                        lines.append(block.get("text", "") + "\n")
                    elif block.get("type") == "tool_result":
                        lines.append(f"**Tool Result** (`{block.get('tool_use_id', '')}`)\n")
                        rc = block.get("content", "")
                        if isinstance(rc, str):
                            lines.append(f"```\n{rc[:2000]}\n```\n")
                        elif isinstance(rc, list):
                            for sub in rc:
                                if isinstance(sub, dict) and sub.get("type") == "text":
                                    lines.append(f"```\n{sub.get('text', '')[:2000]}\n```\n")

        if isinstance(resp_body, dict):
            rc = resp_body.get("content", []) or []
            if rc:
                lines.append("### Assistant\n")
                for block in rc:
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") == "text":
                        text = block.get("text", "")
                        if text.strip():
                            lines.append(text + "\n")
                    elif block.get("type") == "tool_use":
                        name = block.get("name", "unknown")
                        inp = block.get("input", {})
                        lines.append(f"**Tool Use**: `{name}`\n")
                        lines.append(f"```json\n{json.dumps(inp, indent=2, ensure_ascii=False)[:3000]}\n```\n")
                    elif block.get("type") == "thinking":
                        thought = block.get("thinking", "")
                        if thought.strip():
                            lines.append(f"<details>\n<summary>Thinking</summary>\n\n{thought[:5000]}\n\n</details>\n")

            usage = resp_body.get("usage", {})
            if isinstance(usage, dict) and usage:
                parts = []
                for label, key in (
                    ("in", "input_tokens"),
                    ("out", "output_tokens"),
                    ("cache_read", "cache_read_input_tokens"),
                    ("cache_create", "cache_creation_input_tokens"),
                ):
                    v = usage.get(key)
                    if v:
                        parts.append(f"{label}={int(v):,}")
                if parts:
                    lines.append(f"*Tokens: {' / '.join(parts)}*\n")

    return "\n".join(lines)


def _to_json(records: list[dict]) -> str:
    cleaned: list[dict] = []
    for r in records:
        req_body = (r.get("request") or {}).get("body") or {}
        resp_body = (r.get("response") or {}).get("body") or {}
        entry = {
            "turn": r.get("turn"),
            "timestamp": r.get("timestamp"),
            "duration_ms": r.get("duration_ms"),
            "model": req_body.get("model") if isinstance(req_body, dict) else None,
            "messages": req_body.get("messages", []) if isinstance(req_body, dict) else [],
            "response": {
                "content": resp_body.get("content", []) if isinstance(resp_body, dict) else [],
                "usage": resp_body.get("usage", {}) if isinstance(resp_body, dict) else {},
                "stop_reason": resp_body.get("stop_reason") if isinstance(resp_body, dict) else None,
            },
        }
        if isinstance(req_body, dict):
            if req_body.get("system"):
                entry["system"] = req_body["system"]
            if req_body.get("tools"):
                entry["tools"] = req_body["tools"]
        cleaned.append(entry)
    return json.dumps(cleaned, indent=2, ensure_ascii=False)
