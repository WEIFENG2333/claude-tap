"""Build a self-contained HTML viewer from a trace JSONL file.

The viewer template (``viewer.html``) carries a single explicit injection
marker — ``<!-- claude-tap:inject -->`` — into which we paste the data
script(s). Above ``LAZY_THRESHOLD`` records we inline a metadata array plus
individually compressed records inside a ``<script type="text/plain">``
element; the viewer inflates full entries on demand.
"""

from __future__ import annotations

import base64
import gzip
import json
import re
from pathlib import Path

from claude_tap._version import __version__
from claude_tap.identity import context_from_record, session_key
from claude_tap.tool_normalization import expand_tool_namespaces

INJECT_MARKER = "<!-- claude-tap:inject -->"
STYLE_MARKER = "<!-- claude-tap:styles -->"
SCRIPT_MARKER = "<!-- claude-tap:scripts -->"
LAZY_THRESHOLD = 50

_STYLE_ASSETS = ("styles.css",)
_SCRIPT_ASSETS = ("icons.js", "core.js", "data.js", "components.js", "app.js")


def load_viewer_template() -> str:
    """Assemble modular viewer sources into one self-contained document."""

    package_dir = Path(__file__).parent
    template_path = package_dir / "viewer.html"
    if not template_path.exists():
        return ""
    template = template_path.read_text(encoding="utf-8")
    asset_dir = package_dir / "viewer_assets"
    styles = "\n".join((asset_dir / name).read_text(encoding="utf-8") for name in _STYLE_ASSETS)
    scripts = "\n".join((asset_dir / name).read_text(encoding="utf-8") for name in _SCRIPT_ASSETS)
    scripts = scripts.replace("</script", "<\\/script")
    if STYLE_MARKER not in template or SCRIPT_MARKER not in template:
        raise RuntimeError("viewer.html is missing its asset injection markers")
    return template.replace(STYLE_MARKER, f"<style>\n{styles}\n</style>\n{STYLE_MARKER}", 1).replace(
        SCRIPT_MARKER,
        f"<script>\n{scripts}\n</script>\n{SCRIPT_MARKER}",
        1,
    )


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

    template = load_viewer_template()
    if not template:
        return False
    if INJECT_MARKER not in template:
        raise RuntimeError(f"viewer.html is missing the {INJECT_MARKER!r} injection marker")

    records = _read_records(trace_path)
    metadata_records = records
    if strip_events:
        records = [_strip_stream_events(r) for r in records]
    inject = _build_inject_script(
        records,
        trace_path,
        html_path,
        metadata_records=metadata_records,
    )

    out = template.replace(INJECT_MARKER, inject + "\n" + INJECT_MARKER, 1)
    html_path.write_text(out, encoding="utf-8")
    return True


def _build_inject_script(
    records: list[str],
    trace_path: Path,
    html_path: Path,
    *,
    metadata_records: list[str] | None = None,
) -> str:
    jsonl_js = json.dumps(str(trace_path.absolute()))
    html_js = json.dumps(str(html_path.absolute()))
    version_js = json.dumps(__version__)
    capture_id = capture_id_from_path(trace_path)
    capture_js = json.dumps(
        {
            "id": capture_id,
            "record_count": len(records),
            "source_path": str(trace_path.absolute()),
        },
        separators=(",", ":"),
    ).replace("</", "<\\/")
    meta = []
    metadata_source = records if metadata_records is None else metadata_records
    for index, record in enumerate(metadata_source):
        item = extract_metadata(record, capture_id=capture_id)
        if item is not None:
            item["record_index"] = index
            meta.append(item)
    meta_js = json.dumps(meta, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")

    if len(records) > LAZY_THRESHOLD:
        # One gzip member per record keeps large static viewers genuinely
        # lazy: the browser holds compressed text and inflates only the row
        # selected in the inspector.
        compressed_block = "\n".join(
            base64.b64encode(gzip.compress(record.encode("utf-8"), compresslevel=6, mtime=0)).decode("ascii")
            for record in records
        )
        return (
            "<script>\n"
            f"const EMBEDDED_TRACE_META = {meta_js};\n"
            f"const EMBEDDED_CAPTURE_INFO = {capture_js};\n"
            f"const __TRACE_JSONL_PATH__ = {jsonl_js};\n"
            f"const __TRACE_HTML_PATH__ = {html_js};\n"
            f"const __CLAUDE_TAP_VERSION__ = {version_js};\n"
            "</script>\n"
            f'<script type="text/plain" id="trace-compressed" data-codec="gzip-base64">\n{compressed_block}\n</script>'
        )

    inline = ",\n".join(record.replace("</", "<\\/") for record in records)
    return (
        "<script>\n"
        f"const EMBEDDED_TRACE_META = {meta_js};\n"
        f"const EMBEDDED_CAPTURE_INFO = {capture_js};\n"
        f"const EMBEDDED_TRACE_DATA = [\n{inline}\n];\n"
        f"const __TRACE_JSONL_PATH__ = {jsonl_js};\n"
        f"const __TRACE_HTML_PATH__ = {html_js};\n"
        f"const __CLAUDE_TAP_VERSION__ = {version_js};\n"
        "</script>"
    )


# ---------------------------------------------------------------------------
# Sidebar metadata extraction (used by lazy mode)
# ---------------------------------------------------------------------------


def capture_id_from_path(path: Path) -> str:
    """Return the stable capture id used by both static and live viewers."""

    match = re.match(r"^trace_(\d{6})$", path.stem)
    if match and re.match(r"^\d{4}-\d{2}-\d{2}$", path.parent.name):
        return f"{path.parent.name}/{match.group(1)}"
    return path.stem


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


def _is_tool_call_type(item_type: str) -> bool:
    return item_type in {"tool_use", "function_call"} or item_type.endswith(("_call", "_tool_use"))


def _is_tool_result_type(item_type: str) -> bool:
    return item_type == "tool_result" or item_type.endswith(("_call_output", "_tool_result"))


def _extract_request_messages(body: dict) -> list[dict]:
    if not isinstance(body, dict):
        return []
    msgs = body.get("messages")
    if isinstance(msgs, list) and msgs:
        return [m for m in msgs if isinstance(m, dict)]

    contents = body.get("contents")
    if isinstance(contents, list) and contents:
        normalized = []
        for item in contents:
            if not isinstance(item, dict):
                continue
            role = "assistant" if item.get("role") == "model" else item.get("role", "unknown")
            normalized.append({"role": role, "content": item.get("parts") or []})
        return normalized

    inp = body.get("input")
    if not isinstance(inp, list):
        return []

    norm: list[dict] = []
    for item in inp:
        if not isinstance(item, dict):
            continue
        item_type = item.get("type")
        if item_type == "additional_tools" or item.get("role") == "system":
            continue
        role = item.get("role")
        if item_type and _is_tool_result_type(str(item_type)):
            role = "tool"
        elif not isinstance(role, str) or not role:
            role = "assistant" if item_type else "unknown"
        content = item.get("content") if item_type in (None, "message") else item
        norm.append({"role": role, "content": content})
    return norm


def _extract_request_tools(body: dict) -> list[dict]:
    if not isinstance(body, dict):
        return []
    tools: list[dict] = []

    def add_tool(tool: object) -> None:
        if not isinstance(tool, dict):
            return
        declarations = tool.get("function_declarations") or tool.get("functionDeclarations")
        if isinstance(declarations, list):
            for declaration in declarations:
                if isinstance(declaration, dict):
                    tools.append(
                        {
                            "type": "function",
                            "name": declaration.get("name", ""),
                            "description": declaration.get("description", ""),
                            "parameters": declaration.get("parameters") or declaration.get("parametersJsonSchema"),
                        }
                    )
            return
        tools.append(tool)

    for tool in body.get("tools") or []:
        add_tool(tool)
    for item in body.get("input") or []:
        if not isinstance(item, dict) or item.get("type") != "additional_tools":
            continue
        for tool in item.get("tools") or []:
            add_tool(tool)
    return expand_tool_namespaces(tools)


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
                if isinstance(c, dict) and _is_tool_call_type(str(c.get("type") or "")):
                    names.append(c.get("name", ""))
        else:
            item_type = item.get("type")
            if isinstance(item_type, str) and _is_tool_call_type(item_type):
                names.append(item.get("name") or item_type.removesuffix("_call").removesuffix("_tool_use"))
    return names


def _compact_preview(value: object, limit: int = 640) -> str:
    """Return a bounded one-line preview suitable for the trace index."""

    if value is None:
        return ""
    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        except (TypeError, ValueError):
            text = str(value)
    return " ".join(text.split())[:limit]


def _effective_response_body(resp_body: dict, stream_events: list[dict]) -> dict:
    """Prefer the assembled body, falling back to a completed SSE response."""

    if any(resp_body.get(key) for key in ("content", "output", "choices", "candidates", "text")):
        return resp_body
    for event in reversed(stream_events):
        if _event_type(event) != "response.completed":
            continue
        data = _event_payload(event)
        response = data.get("response") if isinstance(data, dict) else None
        if isinstance(response, dict):
            return response
    return resp_body


def _response_error_message(resp_body: dict, stream_events: list[dict]) -> str:
    """Return an upstream error message, including errors delivered over SSE."""

    def from_payload(payload: object) -> str:
        if not isinstance(payload, dict):
            return ""
        error = payload.get("error")
        if isinstance(error, dict):
            message = error.get("message") or error.get("type")
            if message:
                return str(message)
        elif isinstance(error, str) and error:
            return error
        response = payload.get("response")
        if isinstance(response, dict):
            nested = from_payload(response)
            if nested:
                return nested
        if str(payload.get("type") or "").lower().endswith("error") and payload.get("message"):
            return str(payload["message"])
        return ""

    message = from_payload(resp_body)
    if message:
        return message
    for event in reversed(stream_events):
        message = from_payload(_event_payload(event))
        if message:
            return message
    return ""


def _response_activity(resp_body: dict, stream_events: list[dict]) -> tuple[str, list[dict]]:
    """Extract assistant prose and tool calls across supported provider shapes."""

    body = _effective_response_body(resp_body, stream_events)
    texts: list[str] = []
    calls: list[dict] = []

    def visit(item: object) -> None:
        if isinstance(item, str):
            if item.strip():
                texts.append(item)
            return
        if not isinstance(item, dict):
            return

        function_call = item.get("functionCall")
        if isinstance(function_call, dict):
            calls.append(
                {
                    "id": str(function_call.get("id") or ""),
                    "name": str(function_call.get("name") or "function"),
                    "input_preview": _compact_preview(function_call.get("args")),
                }
            )
            return

        item_type = str(item.get("type") or "")
        if item_type == "message":
            content = item.get("content")
            if isinstance(content, list):
                for child in content:
                    visit(child)
            else:
                visit(content)
            return
        if _is_tool_call_type(item_type):
            function = item.get("function") if isinstance(item.get("function"), dict) else {}
            fallback_name = item_type.removesuffix("_call").removesuffix("_tool_use")
            name = item.get("name") or function.get("name") or fallback_name or "tool"
            value = item.get("input")
            if value is None:
                value = item.get("arguments", function.get("arguments", item.get("args")))
            calls.append(
                {
                    "id": str(item.get("id") or item.get("call_id") or ""),
                    "name": str(name),
                    "input_preview": _compact_preview(value),
                }
            )
            return
        if item_type not in {"thinking", "reasoning", "redacted_thinking"}:
            text = item.get("text") or item.get("output_text")
            if isinstance(text, str) and text.strip():
                texts.append(text)

    content = body.get("content")
    if isinstance(content, list):
        for block in content:
            visit(block)
    else:
        visit(content)
    output = body.get("output")
    if isinstance(output, list):
        for block in output:
            visit(block)
    else:
        visit(output)
    for choice in body.get("choices") or []:
        if not isinstance(choice, dict):
            continue
        message = choice.get("message") or choice.get("delta") or {}
        if isinstance(message, dict):
            visit(message.get("content"))
            for call in message.get("tool_calls") or []:
                if isinstance(call, dict):
                    visit({**call, "type": "function_call"})
    for candidate in body.get("candidates") or []:
        if not isinstance(candidate, dict):
            continue
        content = candidate.get("content") or {}
        for part in content.get("parts") or [] if isinstance(content, dict) else []:
            visit(part)
    if isinstance(body.get("text"), str):
        visit(body["text"])

    if not calls and not texts:
        for event in stream_events:
            if _event_type(event) != "response.output_item.done":
                continue
            data = _event_payload(event)
            if isinstance(data, dict):
                visit(data.get("item"))

    assistant_hint = _compact_preview("\n".join(texts), limit=520)
    return assistant_hint, calls[:32]


def _request_tool_results(body: dict) -> list[dict]:
    """Extract bounded tool-result previews from the next model request."""

    if not isinstance(body, dict):
        return []
    results: list[dict] = []
    call_names: dict[str, str] = {}

    def remember_call(item: object) -> None:
        if not isinstance(item, dict):
            return
        item_type = str(item.get("type") or "")
        if _is_tool_call_type(item_type):
            call_id = str(item.get("id") or item.get("call_id") or "")
            if call_id:
                fallback_name = item_type.removesuffix("_call").removesuffix("_tool_use")
                call_names[call_id] = str(item.get("name") or fallback_name or "tool")

    def add_result(item: dict, role: str = "") -> None:
        item_type = str(item.get("type") or "")
        is_result = _is_tool_result_type(item_type) or role == "tool"
        if not is_result:
            return
        call_id = str(item.get("tool_use_id") or item.get("tool_call_id") or item.get("call_id") or "")
        value = item.get("content")
        if value is None:
            value = item.get("output", item.get("response"))
        results.append(
            {
                "id": call_id,
                "name": str(item.get("name") or call_names.get(call_id) or ""),
                "output_preview": _compact_preview(value, limit=800),
                "is_error": bool(item.get("is_error") or item.get("error")),
            }
        )

    raw_items: list[dict] = []
    raw_items.extend(item for item in body.get("messages") or [] if isinstance(item, dict))
    raw_items.extend(item for item in body.get("input") or [] if isinstance(item, dict))
    for item in raw_items:
        remember_call(item)
        content = item.get("content")
        if isinstance(content, list):
            for block in content:
                remember_call(block)
        elif isinstance(content, dict):
            remember_call(content)

    for item in raw_items:
        role = str(item.get("role") or "")
        add_result(item, role)
        content = item.get("content")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    add_result(block, role)
        elif isinstance(content, dict):
            add_result(content, role)

    for content in body.get("contents") or []:
        if not isinstance(content, dict):
            continue
        for part in content.get("parts") or []:
            response = part.get("functionResponse") if isinstance(part, dict) else None
            if isinstance(response, dict):
                results.append(
                    {
                        "id": str(response.get("id") or ""),
                        "name": str(response.get("name") or ""),
                        "output_preview": _compact_preview(response.get("response"), limit=800),
                        "is_error": bool(response.get("error")),
                    }
                )
    return results[:64]


def _content_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        block_type = content.get("type")
        if block_type in (None, "text", "input_text", "output_text"):
            return str(content.get("text") or "")
        return ""
    if not isinstance(content, list):
        return ""
    parts = [_content_text(item) for item in content]
    return "\n".join(part for part in parts if part)


_CONTEXT_TAGS = (
    "system-reminder",
    "session_context",
    "recommended_plugins",
    "user_info",
    "environment_context",
)


def _clean_user_text(text: str) -> str:
    cleaned = text
    for tag in _CONTEXT_TAGS:
        cleaned = re.sub(
            rf"<{tag}(?:\s[^>]*)?>.*?</{tag}>",
            " ",
            cleaned,
            flags=re.IGNORECASE | re.DOTALL,
        )
    for tag in ("user_query", "session"):
        match = re.search(rf"<{tag}(?:\s[^>]*)?>(.*?)</{tag}>", cleaned, flags=re.IGNORECASE | re.DOTALL)
        if match:
            cleaned = match.group(1)
            break
    if "conversation info (untrusted metadata):" in cleaned.lower():
        message_parts = re.split(r"\[message_id:[^\]]+\]", cleaned, flags=re.IGNORECASE)
        tail = message_parts[-1].strip()
        sender_message = re.match(r"[^\n:]{1,180}:\s*(.+)", tail, flags=re.DOTALL)
        if sender_message:
            cleaned = sender_message.group(1)
    return " ".join(cleaned.split()).strip()


def _user_hint(messages: list[dict]) -> str:
    always_ignored_prefixes = (
        "<environment_context",
        "<session_context",
        "<recommended_plugins",
        "<user_info",
        "# agents.md instructions",
        "you have ",
    )
    fallback = ""
    for message in messages:
        if str(message.get("role") or "").lower() != "user":
            continue
        raw_text = _content_text(message.get("content"))
        text = _clean_user_text(raw_text)
        if not text:
            continue
        fallback = fallback or text
        raw_lower = raw_text.strip().lower()
        if "<user_query" not in raw_lower and raw_lower.startswith(always_ignored_prefixes):
            continue
        if raw_lower.startswith("<system-reminder") and text == " ".join(raw_text.split()).strip():
            continue
        return text[:240]
    return fallback[:240]


def _task_kind(messages: list[dict], system_text: str, request_kind: object) -> str:
    snippets = [system_text[:24_000]]
    for message in messages[:16]:
        snippets.append(_content_text(message.get("content"))[:8_000])
    sample = "\n".join(snippets)[:96_000].lower()
    title_markers = (
        "generate the session title",
        "create a concise title for an ai coding-assistant session",
        "write the title in the predominant language of the session",
        "generate a concise, sentence-case title",
    )
    if any(marker in sample for marker in title_markers):
        return "title-generation"
    compaction_markers = (
        "summarize the conversation so far",
        "compact the conversation",
        "conversation summary for continuation",
    )
    if any(marker in sample for marker in compaction_markers):
        return "compaction"
    return str(request_kind or "turn")


def _request_category(method: str, path: str) -> str:
    if method.upper() in {"POST", "WEBSOCKET"}:
        clean = path.split("?", 1)[0].rstrip("/").lower()
        model_suffixes = (
            "/messages",
            "/responses",
            "/chat/completions",
            ":generatecontent",
            ":streamgeneratecontent",
        )
        if clean.endswith(model_suffixes):
            return "model"
    return "auxiliary"


def _request_model(body: dict, path: str) -> str:
    explicit = body.get("model") if isinstance(body, dict) else ""
    if explicit:
        return str(explicit)
    match = re.search(r"/models/([^/:?]+):(?:stream)?generateContent", path, flags=re.IGNORECASE)
    return match.group(1) if match else ""


def extract_metadata(record_json: str, *, capture_id: str = "") -> dict | None:
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
    if not usage and isinstance(resp_body, dict):
        gemini_usage = resp_body.get("usageMetadata")
        if isinstance(gemini_usage, dict):
            usage = {
                "input_tokens": gemini_usage.get("promptTokenCount", 0),
                "output_tokens": gemini_usage.get("candidatesTokenCount", 0),
                "cache_read_input_tokens": gemini_usage.get("cachedContentTokenCount", 0),
            }
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
    context = context_from_record(r)
    task_kind = _task_kind(msgs, sys_text, context.get("request_kind"))
    request_category = _request_category(str(req.get("method") or ""), str(req.get("path") or ""))
    logical_session_key = session_key(context, capture_id)
    if request_category == "auxiliary" and not context.get("session_id"):
        logical_session_key = f"auxiliary:{capture_id or 'standalone'}"
    tools = _extract_request_tools(body)
    tool_names = [_request_tool_name(tool) for tool in tools]

    assistant_hint, tool_calls = _response_activity(resp_body, stream_events)
    response_tool_names = [call["name"] for call in tool_calls if call.get("name")]
    tool_results = _request_tool_results(body)

    error_msg = _response_error_message(resp_body, stream_events)

    metadata = {
        "turn": r.get("turn"),
        "request_id": r.get("request_id", ""),
        "timestamp": r.get("timestamp", ""),
        "duration_ms": r.get("duration_ms", 0),
        "method": req.get("method", ""),
        "path": req.get("path", ""),
        "model": _request_model(body, str(req.get("path") or "")),
        "status": resp.get("status", 0),
        "error_message": error_msg,
        "input_tokens": usage.get("input_tokens", usage.get("prompt_tokens", 0)) if isinstance(usage, dict) else 0,
        "output_tokens": usage.get("output_tokens", usage.get("completion_tokens", 0))
        if isinstance(usage, dict)
        else 0,
        "cache_read_input_tokens": usage.get("cache_read_input_tokens", 0) if isinstance(usage, dict) else 0,
        "cache_creation_input_tokens": usage.get("cache_creation_input_tokens", 0) if isinstance(usage, dict) else 0,
        "has_system": bool(sys_text),
        "message_count": len(msgs),
        "user_hint": _user_hint(msgs),
        "assistant_hint": assistant_hint,
        "sys_hint": sys_text[:200],
        "tool_names": tool_names,
        "response_tool_names": response_tool_names,
        "tool_calls": tool_calls,
        "tool_results": tool_results,
        "tool_result_ids": [result["id"] for result in tool_results if result.get("id")],
        "capture_id": capture_id,
        "session_key": logical_session_key,
        "request_category": request_category,
        "task_kind": task_kind,
    }
    for field_name in (
        "adapter",
        "client",
        "client_version",
        "protocol",
        "session_id",
        "session_id_source",
        "session_id_confidence",
        "thread_id",
        "parent_thread_id",
        "agent_id",
        "parent_agent_id",
        "turn_id",
        "request_kind",
        "window_id",
        "previous_response_id",
    ):
        value = context.get(field_name)
        if value not in (None, ""):
            metadata[field_name] = value
    return metadata
