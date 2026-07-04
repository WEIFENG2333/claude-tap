"""Upstream API protocols.

A ``Protocol`` describes *how to talk to one upstream API* — its allowed
paths, whether a request is streaming, how to reassemble the streamed
events, and how to extract token usage. It says nothing about which CLI
binary uses it (that's the job of ``clients.py``).

Adding a new upstream is a single ``Protocol`` instance + (optionally) a
new reassembler in ``sse.py``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from claude_tap.sse import (
    AnthropicReassembler,
    GeminiReassembler,
    OpenAIReassembler,
    PassthroughReassembler,
    StreamReassembler,
)


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_create_tokens: int = 0


def _default_is_streaming(path: str, body: object) -> bool:
    return bool(isinstance(body, dict) and body.get("stream"))


def _default_path_rewrite(path: str, target: str) -> str:
    return path


@dataclass(frozen=True)
class Protocol:
    name: str
    default_target: str

    # Paths the proxy will accept. Matching is "equal-or-prefix-with-slash".
    # The base URL the *client* uses (with or without ``/v1`` suffix) is
    # decided by ``Client.env_overrides`` on the client side.
    allowed_paths: tuple[str, ...]

    # Set to True to bypass path matching entirely. Used by the passthrough
    # protocol for clients whose SSE format we don't reassemble (Cursor /
    # Qoder / Devin) — we still capture every byte and record the raw event
    # list, we just don't produce a structured snapshot.
    accepts_all_paths: bool = False

    # Optional narrower capture filter. Some clients (notably Codex.app)
    # need every request relayed through the proxy, but only the model API
    # requests are useful enough to write to the trace.
    capture_paths: tuple[str, ...] | None = None
    capture_methods: tuple[str, ...] | None = None

    # Streaming detection — most protocols use ``body.stream``; Gemini uses
    # the URL verb instead, so we override per-protocol.
    is_streaming: Callable[[str, object], bool] = field(default=_default_is_streaming)

    # Per-protocol path rewrite. Used by Codex OAuth where the configured
    # target ``https://chatgpt.com/backend-api/codex`` does NOT accept the
    # ``/v1`` prefix the client emits. Default: identity.
    rewrite_upstream_path: Callable[[str, str], str] = field(default=_default_path_rewrite)

    # SSE/WS event accumulator.
    make_reassembler: Callable[[], StreamReassembler] = field(default=AnthropicReassembler)

    # Token-usage extractor for the reassembled response.body.
    extract_usage: Callable[[dict], Usage] = field(default=lambda body: Usage())

    def matches(self, path: str) -> bool:
        if self.accepts_all_paths:
            return True
        return _path_matches(path, self.allowed_paths)

    def captures(self, method: str, path: str) -> bool:
        if self.capture_methods is not None and method.upper() not in self.capture_methods:
            return False
        if self.capture_paths is None:
            return self.matches(path)
        return _path_matches(path, self.capture_paths)


def _path_matches(path: str, allowed_paths: tuple[str, ...]) -> bool:
    clean = path.split("?", 1)[0].rstrip("/") or "/"
    for path_prefix in allowed_paths:
        p = path_prefix.rstrip("/") or "/"
        if p.startswith("suffix:"):
            suffix = p[len("suffix:") :].rstrip("/") or "/"
            if clean == suffix or clean.endswith(suffix):
                return True
            continue
        if clean == p or clean.startswith(p + "/"):
            return True
        if p.endswith(":") and clean.startswith(p):
            return True
    return False


# ---------------------------------------------------------------------------
# Streaming detection helpers
# ---------------------------------------------------------------------------


def _gemini_is_streaming(path: str, body: object) -> bool:
    # Gemini encodes streaming in the URL, not the body.
    return ":streamGenerateContent" in path or "alt=sse" in path


def _codex_app_is_streaming(path: str, body: object) -> bool:
    # Codex.app posts to the ChatGPT Codex backend with text/event-stream
    # responses. The body may be zstd-encoded, so path alone is the stable
    # streaming signal.
    return path.split("?", 1)[0].rstrip("/") == "/backend-api/codex/responses"


# ---------------------------------------------------------------------------
# Path rewrite helpers
# ---------------------------------------------------------------------------


def _openai_rewrite_upstream_path(path: str, target: str) -> str:
    """The Codex CLI hits ``${base}/v1/responses``. For the OpenAI default
    target ``https://api.openai.com`` we keep the ``/v1`` segment; for the
    ChatGPT OAuth backend ``https://chatgpt.com/backend-api/codex`` the
    server expects ``/responses`` (no ``/v1``)."""

    if "api.openai.com" in target:
        return path
    if path.startswith("/v1"):
        rest = path[len("/v1") :] or "/"
        return rest
    return path


def _codex_app_rewrite_upstream_path(path: str, target: str) -> str:
    """Keep Codex.app reverse-mode paths usable with either target shape."""

    if "chatgpt.com/backend-api/codex" in target and path.startswith("/backend-api/codex"):
        rest = path[len("/backend-api/codex") :] or "/"
        return rest
    return path


# ---------------------------------------------------------------------------
# Usage extractors
# ---------------------------------------------------------------------------


def _anthropic_usage(body: dict) -> Usage:
    if not isinstance(body, dict):
        return Usage()
    u = body.get("usage") or {}
    if not isinstance(u, dict):
        return Usage()
    return Usage(
        input_tokens=int(u.get("input_tokens", 0) or 0),
        output_tokens=int(u.get("output_tokens", 0) or 0),
        cache_read_tokens=int(u.get("cache_read_input_tokens", 0) or 0),
        cache_create_tokens=int(u.get("cache_creation_input_tokens", 0) or 0),
    )


def _openai_usage(body: dict) -> Usage:
    if not isinstance(body, dict):
        return Usage()
    u = body.get("usage") or {}
    if not isinstance(u, dict):
        return Usage()
    cached = 0
    details = u.get("input_tokens_details")
    if isinstance(details, dict):
        cached = int(details.get("cached_tokens", 0) or 0)
    return Usage(
        input_tokens=int(u.get("input_tokens", u.get("prompt_tokens", 0)) or 0),
        output_tokens=int(u.get("output_tokens", u.get("completion_tokens", 0)) or 0),
        cache_read_tokens=cached,
        cache_create_tokens=0,
    )


def _gemini_usage(body: dict) -> Usage:
    if not isinstance(body, dict):
        return Usage()
    meta = body.get("usageMetadata") or {}
    if not isinstance(meta, dict):
        return Usage()
    return Usage(
        input_tokens=int(meta.get("promptTokenCount", 0) or 0),
        output_tokens=int(meta.get("candidatesTokenCount", 0) or 0),
        cache_read_tokens=int(meta.get("cachedContentTokenCount", 0) or 0),
        cache_create_tokens=0,
    )


# ---------------------------------------------------------------------------
# Concrete protocols
# ---------------------------------------------------------------------------


ANTHROPIC = Protocol(
    name="anthropic",
    default_target="https://api.anthropic.com",
    allowed_paths=("/v1/messages", "/v1/complete"),
    make_reassembler=AnthropicReassembler,
    extract_usage=_anthropic_usage,
)


OPENAI = Protocol(
    name="openai",
    default_target="https://api.openai.com",
    allowed_paths=(
        # As emitted by clients that put /v1 in their base URL:
        "/v1/responses",
        "/v1/chat/completions",
        "/v1/completions",
        "/v1/models",
        "/v1/embeddings",
        # As seen on the OAuth ChatGPT backend after rewrite_upstream_path
        # drops the /v1 prefix:
        "/responses",
        "/chat/completions",
        "/completions",
        "/models",
        "/embeddings",
        # Some OpenAI-compatible providers keep their product prefix in the
        # URL path, e.g. Kimi Code uses /coding/v1/chat/completions.
        "suffix:/chat/completions",
    ),
    rewrite_upstream_path=_openai_rewrite_upstream_path,
    make_reassembler=OpenAIReassembler,
    extract_usage=_openai_usage,
)


GEMINI = Protocol(
    name="gemini",
    default_target="https://generativelanguage.googleapis.com",
    allowed_paths=(
        "/v1beta/models",
        "/v1beta/files",
        "/v1beta/cachedContents",
        "/v1/models",
        "/v1/files",
    ),
    is_streaming=_gemini_is_streaming,
    make_reassembler=GeminiReassembler,
    extract_usage=_gemini_usage,
)


ANTIGRAVITY = Protocol(
    name="antigravity",
    default_target="https://daily-cloudcode-pa.googleapis.com",
    allowed_paths=("/v1internal:",),
    capture_paths=("/v1internal:streamGenerateContent",),
    capture_methods=("POST",),
    is_streaming=_gemini_is_streaming,
    make_reassembler=GeminiReassembler,
    extract_usage=_gemini_usage,
)


CODEX_APP = Protocol(
    name="codexapp",
    default_target="https://chatgpt.com",
    allowed_paths=(),
    accepts_all_paths=True,
    capture_paths=("/backend-api/codex/responses",),
    capture_methods=("POST",),
    is_streaming=_codex_app_is_streaming,
    rewrite_upstream_path=_codex_app_rewrite_upstream_path,
    make_reassembler=OpenAIReassembler,
    extract_usage=_openai_usage,
)


# For clients whose SSE protocol we don't natively understand (Cursor's
# proprietary RPC, Qoder's, Devin's). We accept every path so the proxy can
# transparently relay bytes. The reassembler only collects the raw event
# stream — the trace will record everything as ``response.sse_events`` even
# though no structured snapshot is produced.
PASSTHROUGH = Protocol(
    name="passthrough",
    default_target="",  # client must supply --target or rely on Client default
    allowed_paths=(),
    accepts_all_paths=True,
    make_reassembler=PassthroughReassembler,
)


_REGISTRY: dict[str, Protocol] = {p.name: p for p in (ANTHROPIC, ANTIGRAVITY, CODEX_APP, OPENAI, GEMINI, PASSTHROUGH)}


def get(name: str) -> Protocol:
    if name not in _REGISTRY:
        raise KeyError(f"unknown protocol: {name!r} (known: {sorted(_REGISTRY)})")
    return _REGISTRY[name]


def names() -> list[str]:
    return sorted(_REGISTRY)


def select_for_path(path: str, protocols: tuple[Protocol, ...]) -> Protocol | None:
    """Pick the first protocol whose ``allowed_paths`` matches ``path``."""
    for p in protocols:
        if p.matches(path):
            return p
    return None
