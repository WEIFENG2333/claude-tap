"""Normalize session, thread, agent, and turn identity across AI clients.

Identity is deliberately extracted from a small allowlist of wire fields.  A
recursive search for keys such as ``sessionId`` is unsafe: tool payloads often
contain unrelated terminal, browser, or process session identifiers.

Product-specific adapters run before the generic adapter.  The resulting
``trace_context`` is stable viewer-facing data, so the UI never needs to know
which vendor header carried a session id.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Callable, Mapping

TRACE_CONTEXT_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class IdentityInput:
    headers: dict[str, str]
    body: dict
    path: str
    protocol: str


@dataclass(frozen=True)
class Signal:
    value: str
    source: str
    confidence: str = "high"


@dataclass
class AdapterResult:
    client: str
    client_version: str = ""
    signals: dict[str, Signal] = field(default_factory=dict)


@dataclass(frozen=True)
class IdentityAdapter:
    name: str
    matches: Callable[[IdentityInput], bool]
    extract: Callable[[IdentityInput], AdapterResult]


def _clean(value: object) -> str:
    if isinstance(value, bool) or value is None:
        return ""
    if isinstance(value, (str, int)):
        text = str(value).strip()
        if text and text.lower() not in {"null", "none", "undefined"}:
            return text[:512]
    return ""


def _header(data: IdentityInput, name: str) -> str:
    return _clean(data.headers.get(name.lower()))


def _body_value(body: dict, *path: str) -> str:
    current: object = body
    for key in path:
        if not isinstance(current, dict):
            return ""
        current = current.get(key)
    return _clean(current)


def _json_object(value: object) -> dict:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str) or not value.strip():
        return {}
    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _version(user_agent: str, product: str) -> str:
    match = re.search(rf"(?:^|[ /]){re.escape(product)}/([^\s;()]+)", user_agent, re.IGNORECASE)
    return match.group(1) if match else ""


def _signal(value: object, source: str, confidence: str = "high") -> Signal | None:
    cleaned = _clean(value)
    return Signal(cleaned, source, confidence) if cleaned else None


def _put(result: AdapterResult, field_name: str, signal: Signal | None) -> None:
    if signal is not None and field_name not in result.signals:
        result.signals[field_name] = signal


def _metadata(data: IdentityInput, header_name: str, body_name: str | None = None) -> dict:
    header_metadata = _json_object(_header(data, header_name))
    if header_metadata:
        return header_metadata
    if body_name:
        raw = data.body.get(body_name)
        body_metadata = _json_object(raw)
        if body_metadata:
            return body_metadata
    return {}


def _ua(data: IdentityInput) -> str:
    return _header(data, "user-agent")


def _extract_claude(data: IdentityInput) -> AdapterResult:
    result = AdapterResult("claude-code", _version(_ua(data), "claude-cli"))
    _put(
        result,
        "session_id",
        _signal(_header(data, "x-claude-code-session-id"), "header.x-claude-code-session-id"),
    )
    _put(result, "agent_id", _signal(_header(data, "x-claude-code-agent-id"), "header.x-claude-code-agent-id"))
    _put(
        result,
        "parent_agent_id",
        _signal(_header(data, "x-claude-code-parent-agent-id"), "header.x-claude-code-parent-agent-id"),
    )
    return result


def _codex_metadata(data: IdentityInput) -> tuple[dict, str]:
    metadata = _metadata(data, "x-codex-turn-metadata")
    if metadata:
        return metadata, "header.x-codex-turn-metadata"
    client_metadata = data.body.get("client_metadata")
    if not isinstance(client_metadata, dict):
        return {}, ""
    nested = _json_object(client_metadata.get("x-codex-turn-metadata"))
    if nested:
        return nested, "body.client_metadata.x-codex-turn-metadata"
    return client_metadata, "body.client_metadata"


def _extract_codex(data: IdentityInput) -> AdapterResult:
    result = AdapterResult("codex", _version(_ua(data), "codex_exec"))
    metadata, metadata_source = _codex_metadata(data)
    _put(
        result,
        "session_id",
        _signal(metadata.get("session_id"), f"{metadata_source}.session_id")
        or _signal(_header(data, "session-id"), "header.session-id"),
    )
    _put(
        result,
        "thread_id",
        _signal(metadata.get("thread_id"), f"{metadata_source}.thread_id")
        or _signal(_header(data, "thread-id"), "header.thread-id"),
    )
    _put(result, "turn_id", _signal(metadata.get("turn_id"), f"{metadata_source}.turn_id"))
    _put(result, "window_id", _signal(metadata.get("window_id"), f"{metadata_source}.window_id"))
    _put(
        result,
        "request_kind",
        _signal(metadata.get("request_kind"), f"{metadata_source}.request_kind"),
    )
    parent_thread = _header(data, "x-codex-parent-thread-id") or _clean(metadata.get("parent_thread_id"))
    _put(result, "parent_thread_id", _signal(parent_thread, "header.x-codex-parent-thread-id"))
    return result


def _extract_deepseek(data: IdentityInput) -> AdapterResult:
    result = AdapterResult("deepseek-harness", _version(_ua(data), "deepseek-harness"))
    _put(
        result,
        "session_id",
        _signal(_header(data, "x-deepseek-harness-session-id"), "header.x-deepseek-harness-session-id"),
    )
    return result


def _extract_grok(data: IdentityInput) -> AdapterResult:
    result = AdapterResult("grok", _version(_ua(data), "grok") or _version(_ua(data), "grok-shell"))
    _put(result, "session_id", _signal(_header(data, "x-grok-session-id"), "header.x-grok-session-id"))
    _put(result, "agent_id", _signal(_header(data, "x-grok-agent-id"), "header.x-grok-agent-id"))
    _put(result, "turn_id", _signal(_header(data, "x-grok-turn-idx"), "header.x-grok-turn-idx"))
    return result


def _extract_hermes(data: IdentityInput) -> AdapterResult:
    result = AdapterResult("hermes", _version(_ua(data), "Hermes Agent"))
    _put(result, "session_id", _signal(_header(data, "session_id"), "header.session_id"))
    return result


def _extract_pi(data: IdentityInput) -> AdapterResult:
    result = AdapterResult("pi", _version(_ua(data), "pi-coding-agent"))
    _put(
        result,
        "session_id",
        _signal(_header(data, "session_id"), "header.session_id")
        or _signal(_header(data, "x-session-affinity"), "header.x-session-affinity", "medium"),
    )
    return result


def _extract_opencode(data: IdentityInput) -> AdapterResult:
    result = AdapterResult("opencode", _version(_ua(data), "opencode"))
    _put(
        result,
        "session_id",
        _signal(_header(data, "x-session-id"), "header.x-session-id")
        or _signal(_header(data, "session-id"), "header.session-id"),
    )
    _put(
        result,
        "parent_thread_id",
        _signal(_header(data, "x-parent-session-id"), "header.x-parent-session-id"),
    )
    return result


def _extract_gemini(data: IdentityInput) -> AdapterResult:
    return AdapterResult("gemini-cli", _version(_ua(data), "GeminiCLI"))


def _extract_generic(data: IdentityInput) -> AdapterResult:
    result = AdapterResult("unknown")
    session_headers = (
        "x-session-id",
        "session-id",
        "session_id",
        "x-conversation-id",
        "conversation-id",
    )
    for name in session_headers:
        value = _header(data, name)
        if value:
            _put(result, "session_id", _signal(value, f"header.{name}", "medium"))
            break

    body_session_paths = (
        ("session_id",),
        ("sessionId",),
        ("conversation_id",),
        ("metadata", "session_id"),
        ("client_metadata", "session_id"),
    )
    if "session_id" not in result.signals:
        for path in body_session_paths:
            value = _body_value(data.body, *path)
            if value:
                _put(result, "session_id", _signal(value, "body." + ".".join(path), "medium"))
                break

    conversation = data.body.get("conversation")
    if isinstance(conversation, dict):
        conversation = conversation.get("id")
    conversation_signal = _signal(conversation, "body.conversation", "high")
    _put(result, "thread_id", conversation_signal)
    if "session_id" not in result.signals:
        _put(result, "session_id", conversation_signal)

    thread_header = _header(data, "x-thread-id")
    thread_body = _body_value(data.body, "thread_id")
    _put(
        result,
        "thread_id",
        _signal(thread_header, "header.x-thread-id", "medium") or _signal(thread_body, "body.thread_id", "medium"),
    )
    parent_session = _header(data, "x-parent-session-id")
    parent_thread = _header(data, "x-parent-thread-id")
    _put(
        result,
        "parent_thread_id",
        _signal(parent_session, "header.x-parent-session-id", "medium")
        or _signal(parent_thread, "header.x-parent-thread-id", "medium"),
    )
    _put(
        result,
        "previous_response_id",
        _signal(_body_value(data.body, "previous_response_id"), "body.previous_response_id"),
    )
    return result


def _has_header(name: str) -> Callable[[IdentityInput], bool]:
    return lambda data: bool(_header(data, name))


ADAPTERS: tuple[IdentityAdapter, ...] = (
    IdentityAdapter(
        "claude-code",
        lambda data: bool(_header(data, "x-claude-code-session-id")) or "claude-cli/" in _ua(data).lower(),
        _extract_claude,
    ),
    IdentityAdapter(
        "codex",
        lambda data: (
            bool(_header(data, "x-codex-turn-metadata"))
            or _header(data, "originator").lower() == "codex_exec"
            or "codex_exec/" in _ua(data).lower()
        ),
        _extract_codex,
    ),
    IdentityAdapter("deepseek-harness", _has_header("x-deepseek-harness-session-id"), _extract_deepseek),
    IdentityAdapter(
        "grok",
        lambda data: bool(_header(data, "x-grok-session-id")) or "grok-shell/" in _ua(data).lower(),
        _extract_grok,
    ),
    IdentityAdapter("hermes", lambda data: "hermes agent" in _ua(data).lower(), _extract_hermes),
    IdentityAdapter("pi", lambda data: "pi-coding-agent" in _ua(data).lower(), _extract_pi),
    IdentityAdapter(
        "opencode",
        lambda data: "opencode" in _ua(data).lower() or bool(_header(data, "x-parent-session-id")),
        _extract_opencode,
    ),
    IdentityAdapter("gemini-cli", lambda data: "geminicli/" in _ua(data).lower(), _extract_gemini),
)


def _infer_protocol(path: str) -> str:
    clean = path.split("?", 1)[0].lower()
    if ":generatecontent" in clean or ":streamgeneratecontent" in clean:
        return "gemini"
    if clean.endswith("/messages") or "/messages/" in clean:
        return "anthropic"
    if clean.endswith("/responses") or clean.endswith("/chat/completions"):
        return "openai"
    return "unknown"


def extract_trace_context(
    headers: Mapping[str, str] | None,
    body: object,
    path: str,
    *,
    protocol: str | None = None,
) -> dict:
    """Return normalized, viewer-facing identity for one request.

    Empty identity fields are omitted.  ``previous_response_id`` is retained
    as a linkage hint but is never promoted to a session id by itself.
    """

    normalized_headers = {str(key).lower(): str(value) for key, value in (headers or {}).items()}
    data = IdentityInput(
        headers=normalized_headers,
        body=body if isinstance(body, dict) else {},
        path=path,
        protocol=protocol or _infer_protocol(path),
    )

    selected: AdapterResult | None = None
    adapter_name = "generic"
    for adapter in ADAPTERS:
        if adapter.matches(data):
            selected = adapter.extract(data)
            adapter_name = adapter.name
            break
    if selected is None:
        selected = AdapterResult("unknown")

    generic = _extract_generic(data)
    for field_name, signal in generic.signals.items():
        selected.signals.setdefault(field_name, signal)

    result: dict[str, object] = {
        "schema_version": TRACE_CONTEXT_SCHEMA_VERSION,
        "adapter": adapter_name,
        "client": selected.client,
        "protocol": data.protocol,
    }
    if selected.client_version:
        result["client_version"] = selected.client_version
    for field_name, signal in selected.signals.items():
        result[field_name] = signal.value
        result[f"{field_name}_source"] = signal.source
        result[f"{field_name}_confidence"] = signal.confidence
    return result


def context_from_record(record: Mapping[str, object]) -> dict:
    """Read current records and backfill normalized context for old traces."""

    existing = record.get("trace_context")
    if isinstance(existing, dict) and existing.get("schema_version") == TRACE_CONTEXT_SCHEMA_VERSION:
        return dict(existing)
    request = record.get("request")
    request = request if isinstance(request, dict) else {}
    return extract_trace_context(
        request.get("headers") if isinstance(request.get("headers"), dict) else {},
        request.get("body"),
        str(request.get("path") or ""),
    )


def session_key(context: Mapping[str, object], capture_id: str) -> str:
    """Build a collision-resistant UI key with an explicit capture fallback."""

    explicit_id = _clean(context.get("session_id"))
    if explicit_id:
        client = _clean(context.get("client")) or "unknown"
        return f"session:{client}:{explicit_id}"
    return f"capture:{capture_id or 'standalone'}"
