"""Build a self-contained HTML viewer from a trace JSONL file.

The viewer template (``viewer.html``) carries a single explicit injection
marker — ``<!-- claude-tap:inject -->`` — into which we paste the data
script(s). Above ``LAZY_THRESHOLD`` records we only inline a metadata array
plus the raw JSONL inside a ``<script type="text/plain">`` element; the
viewer materialises full entries on demand.
"""

from __future__ import annotations

import json
from pathlib import Path

from claude_tap._version import __version__

INJECT_MARKER = "<!-- claude-tap:inject -->"
LAZY_THRESHOLD = 50


def _read_records(jsonl_path: Path) -> list[str]:
    if not jsonl_path.exists():
        return []
    out: list[str] = []
    with open(jsonl_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(line)
    return out


def _strip_stream_events(record_json: str) -> str:
    """Drop ``response.sse_events`` and ``response.ws_events`` from a record.

    The reassembled snapshot in ``response.body`` already contains the final
    message. The per-chunk event list is rarely useful for the viewer and can
    multiply the trace size by 5-10x. We strip it by default; pass
    ``strip_events=False`` to ``render_html`` to keep it.
    """

    try:
        r = json.loads(record_json)
    except (json.JSONDecodeError, ValueError):
        return record_json
    resp = r.get("response")
    if isinstance(resp, dict):
        resp.pop("sse_events", None)
        resp.pop("ws_events", None)
    return json.dumps(r, ensure_ascii=False, separators=(",", ":"))


def render_html(trace_path: Path, html_path: Path, *, strip_events: bool = True) -> bool:
    """Render ``html_path`` from ``trace_path`` and the bundled template.

    By default we strip per-chunk streaming events from each record before
    embedding (saves ~5-10x on disk for traces with long SSE responses).
    Pass ``strip_events=False`` to keep them.

    Returns True if a viewer was written, False if the template is missing.
    """

    template_path = Path(__file__).parent / "viewer.html"
    if not template_path.exists():
        return False
    template = template_path.read_text(encoding="utf-8")
    if INJECT_MARKER not in template:
        raise RuntimeError(f"viewer.html is missing the {INJECT_MARKER!r} injection marker")

    records = _read_records(trace_path)
    if strip_events:
        records = [_strip_stream_events(r) for r in records]
    inject = _build_inject_script(records, trace_path, html_path)

    out = template.replace(INJECT_MARKER, inject + "\n" + INJECT_MARKER, 1)
    html_path.write_text(out, encoding="utf-8")
    return True


def _build_inject_script(records: list[str], trace_path: Path, html_path: Path) -> str:
    jsonl_js = json.dumps(str(trace_path.absolute()))
    html_js = json.dumps(str(html_path.absolute()))
    version_js = json.dumps(__version__)

    if len(records) > LAZY_THRESHOLD:
        meta = [m for m in (extract_metadata(r) for r in records) if m is not None]
        meta_js = json.dumps(meta, separators=(",", ":"))
        # ``</`` would prematurely terminate the script element. ``\/`` is a
        # valid JSON escape for ``/``, so this stays parseable.
        raw_block = "\n".join(rec.replace("</", "<\\/") for rec in records)
        return (
            "<script>\n"
            f"const EMBEDDED_TRACE_META = {meta_js};\n"
            f"const __TRACE_JSONL_PATH__ = {jsonl_js};\n"
            f"const __TRACE_HTML_PATH__ = {html_js};\n"
            f"const __CLAUDE_TAP_VERSION__ = {version_js};\n"
            "</script>\n"
            f'<script type="text/plain" id="trace-raw">\n{raw_block}\n</script>'
        )

    inline = ",\n".join(records)
    return (
        "<script>\n"
        f"const EMBEDDED_TRACE_DATA = [\n{inline}\n];\n"
        f"const __TRACE_JSONL_PATH__ = {jsonl_js};\n"
        f"const __TRACE_HTML_PATH__ = {html_js};\n"
        f"const __CLAUDE_TAP_VERSION__ = {version_js};\n"
        "</script>"
    )


# ---------------------------------------------------------------------------
# Sidebar metadata extraction (used by lazy mode)
# ---------------------------------------------------------------------------


def _iter_response_events(resp: dict) -> list[dict]:
    if not isinstance(resp, dict):
        return []
    sse = resp.get("sse_events")
    if isinstance(sse, list) and sse:
        return sse
    ws = resp.get("ws_events")
    return ws if isinstance(ws, list) else []


def _event_type(event: dict) -> str:
    if not isinstance(event, dict):
        return ""
    val = event.get("event") or event.get("type")
    return val if isinstance(val, str) else ""


def _event_payload(event: dict) -> dict | None:
    if not isinstance(event, dict):
        return None
    payload = event.get("data", event)
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (json.JSONDecodeError, TypeError):
            return None
    return payload if isinstance(payload, dict) else None


def _extract_request_messages(body: dict) -> list[dict]:
    if not isinstance(body, dict):
        return []
    msgs = body.get("messages")
    if isinstance(msgs, list) and msgs:
        return [m for m in msgs if isinstance(m, dict)]

    inp = body.get("input")
    if not isinstance(inp, list):
        return []

    norm: list[dict] = []
    for item in inp:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "additional_tools":
            continue
        if item.get("type") not in (None, "message") and "role" not in item:
            continue
        role = item.get("role")
        if not isinstance(role, str) or not role:
            continue
        norm.append({"role": role, "content": item.get("content")})
    return norm


def _extract_request_tools(body: dict) -> list[dict]:
    if not isinstance(body, dict):
        return []
    tools = [tool for tool in body.get("tools") or [] if isinstance(tool, dict)]
    for item in body.get("input") or []:
        if not isinstance(item, dict) or item.get("type") != "additional_tools":
            continue
        tools.extend(tool for tool in item.get("tools") or [] if isinstance(tool, dict))
    return tools


def _request_tool_name(tool: dict) -> str:
    function = tool.get("function")
    if isinstance(function, dict) and function.get("name"):
        return str(function["name"])
    return str(tool.get("name") or tool.get("type") or "")


def _extract_response_tool_names(output: list) -> list[str]:
    names: list[str] = []
    if not isinstance(output, list):
        return names
    for item in output:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "message":
            for c in item.get("content") or []:
                if isinstance(c, dict) and c.get("type") == "tool_use":
                    names.append(c.get("name", ""))
        elif item.get("type") == "function_call":
            names.append(item.get("name", ""))
    return names


def extract_metadata(record_json: str) -> dict | None:
    try:
        r = json.loads(record_json)
    except (json.JSONDecodeError, TypeError):
        return None

    req_raw = r.get("request") or {}
    req = req_raw if isinstance(req_raw, dict) else {}
    body_raw = req.get("body") or {}
    body = body_raw if isinstance(body_raw, dict) else {}
    resp_raw = r.get("response") or {}
    resp = resp_raw if isinstance(resp_raw, dict) else {}
    resp_body_raw = resp.get("body") or {}
    resp_body = resp_body_raw if isinstance(resp_body_raw, dict) else {}
    stream_events = _iter_response_events(resp)

    usage = resp_body.get("usage") if isinstance(resp_body, dict) else None
    usage = usage or {}
    if not usage:
        for ev in reversed(stream_events):
            if _event_type(ev) != "response.completed":
                continue
            data = _event_payload(ev)
            if isinstance(data, dict):
                usage = (data.get("response") or {}).get("usage") or {}
                if usage:
                    break

    sys_text = ""
    if isinstance(body.get("system"), str):
        sys_text = body["system"]
    elif isinstance(body.get("system"), list):
        parts = []
        for s in body["system"]:
            if isinstance(s, str):
                parts.append(s)
            elif isinstance(s, dict):
                parts.append(s.get("text", ""))
        sys_text = "\n".join(parts)
    elif isinstance(body.get("instructions"), str):
        sys_text = body["instructions"]

    msgs = _extract_request_messages(body)
    tools = _extract_request_tools(body)
    tool_names = [_request_tool_name(tool) for tool in tools]

    response_tool_names: list[str] = []
    rc = resp_body.get("content") or [] if isinstance(resp_body, dict) else []
    if rc:
        for block in rc:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                response_tool_names.append(block.get("name", ""))
    elif isinstance(resp_body, dict):
        response_tool_names.extend(_extract_response_tool_names(resp_body.get("output") or []))
    if not response_tool_names:
        for ev in reversed(stream_events):
            if _event_type(ev) != "response.completed":
                continue
            data = _event_payload(ev)
            if isinstance(data, dict):
                response_tool_names.extend(
                    _extract_response_tool_names((data.get("response") or {}).get("output") or [])
                )
                break

    error_msg = ""
    if isinstance(resp_body, dict):
        err = resp_body.get("error")
        if isinstance(err, dict):
            error_msg = err.get("message", "")

    return {
        "turn": r.get("turn"),
        "request_id": r.get("request_id", ""),
        "timestamp": r.get("timestamp", ""),
        "duration_ms": r.get("duration_ms", 0),
        "method": req.get("method", ""),
        "path": req.get("path", ""),
        "model": body.get("model", "") if isinstance(body, dict) else "",
        "status": resp.get("status", 0),
        "error_message": error_msg,
        "input_tokens": usage.get("input_tokens", 0) if isinstance(usage, dict) else 0,
        "output_tokens": usage.get("output_tokens", 0) if isinstance(usage, dict) else 0,
        "cache_read_input_tokens": usage.get("cache_read_input_tokens", 0) if isinstance(usage, dict) else 0,
        "cache_creation_input_tokens": usage.get("cache_creation_input_tokens", 0) if isinstance(usage, dict) else 0,
        "has_system": bool(sys_text),
        "message_count": len(msgs),
        "sys_hint": sys_text[:200],
        "tool_names": tool_names,
        "response_tool_names": response_tool_names,
    }
