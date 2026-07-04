"""Protocol layer: path matching, streaming detection, usage extraction, path rewrite."""

from __future__ import annotations

import pytest

from claude_tap import protocols
from claude_tap.protocols import ANTHROPIC, ANTIGRAVITY, CODEX_APP, GEMINI, OPENAI


def test_registry_has_known_protocols():
    names = protocols.names()
    assert names == ["anthropic", "antigravity", "codexapp", "gemini", "openai", "passthrough"]


def test_registry_get_unknown_raises():
    with pytest.raises(KeyError):
        protocols.get("does-not-exist")


# --- path matching --------------------------------------------------------


def test_anthropic_matches_messages_paths():
    assert ANTHROPIC.matches("/v1/messages")
    assert ANTHROPIC.matches("/v1/messages?beta=true")
    assert ANTHROPIC.matches("/v1/messages/sub")
    assert not ANTHROPIC.matches("/v1/responses")
    assert not ANTHROPIC.matches("/etc/passwd")


def test_openai_matches_both_with_and_without_v1():
    """The OAuth ChatGPT backend serves /responses; the regular OpenAI API
    serves /v1/responses. We accept both forms."""
    assert OPENAI.matches("/v1/responses")
    assert OPENAI.matches("/responses")
    assert OPENAI.matches("/v1/chat/completions")
    assert OPENAI.matches("/coding/v1/chat/completions")


def test_codexapp_relays_all_but_captures_only_response_posts():
    assert CODEX_APP.matches("/backend-api/wham/remote/control/server")
    assert CODEX_APP.matches("/backend-api/codex/analytics-events/events")
    assert CODEX_APP.matches("/backend-api/codex/responses")
    assert CODEX_APP.captures("POST", "/backend-api/codex/responses")
    assert not CODEX_APP.captures("GET", "/backend-api/codex/responses")
    assert not CODEX_APP.captures("POST", "/backend-api/codex/analytics-events/events")


def test_antigravity_relays_internal_paths_but_captures_only_generation():
    assert ANTIGRAVITY.matches("/v1internal:loadCodeAssist")
    assert ANTIGRAVITY.matches("/v1internal:streamGenerateContent?alt=sse")
    assert not ANTIGRAVITY.matches("/v1/internal")
    assert ANTIGRAVITY.captures("POST", "/v1internal:streamGenerateContent?alt=sse")
    assert not ANTIGRAVITY.captures("GET", "/v1internal:streamGenerateContent?alt=sse")
    assert not ANTIGRAVITY.captures("POST", "/v1internal:fetchAvailableModels")


def test_gemini_matches_versioned_paths():
    assert GEMINI.matches("/v1beta/models")
    assert GEMINI.matches("/v1beta/models/gemini-3:streamGenerateContent?alt=sse")


def test_select_for_path_picks_first_match():
    p = protocols.select_for_path("/v1beta/models/x:generateContent", (ANTHROPIC, OPENAI, GEMINI))
    assert p is GEMINI

    p = protocols.select_for_path("/v1/messages", (OPENAI, ANTHROPIC, GEMINI))
    assert p is ANTHROPIC

    assert protocols.select_for_path("/etc/passwd", (ANTHROPIC, OPENAI, GEMINI)) is None


# --- streaming detection (now per-protocol) -------------------------------


def test_anthropic_streaming_via_body():
    assert ANTHROPIC.is_streaming("/v1/messages", {"stream": True})
    assert not ANTHROPIC.is_streaming("/v1/messages", {"stream": False})
    assert not ANTHROPIC.is_streaming("/v1/messages", {})
    assert not ANTHROPIC.is_streaming("/v1/messages", "not-a-dict")


def test_gemini_streaming_via_url():
    """Gemini doesn't set body.stream; it uses :streamGenerateContent + ?alt=sse."""
    assert GEMINI.is_streaming("/v1beta/models/x:streamGenerateContent?alt=sse", {})
    assert GEMINI.is_streaming("/foo:streamGenerateContent", {})
    assert GEMINI.is_streaming("/foo?alt=sse", {})
    assert not GEMINI.is_streaming("/v1beta/models/x:generateContent", {})


def test_antigravity_streaming_uses_gemini_url_signal():
    assert ANTIGRAVITY.is_streaming("/v1internal:streamGenerateContent?alt=sse", {})


def test_openai_streaming_via_body():
    assert OPENAI.is_streaming("/v1/responses", {"stream": True})
    assert not OPENAI.is_streaming("/v1/responses", {})


# --- upstream path rewrite (Codex OAuth special case) ---------------------


def test_anthropic_no_path_rewrite():
    assert ANTHROPIC.rewrite_upstream_path("/v1/messages", "https://api.anthropic.com") == "/v1/messages"


def test_openai_keeps_v1_for_default_target():
    assert OPENAI.rewrite_upstream_path("/v1/responses", "https://api.openai.com") == "/v1/responses"


def test_openai_strips_v1_for_chatgpt_oauth_target():
    assert OPENAI.rewrite_upstream_path("/v1/responses", "https://chatgpt.com/backend-api/codex") == "/responses"


def test_openai_strip_handles_root():
    """Stripping must not return an empty path."""
    assert OPENAI.rewrite_upstream_path("/v1", "https://chatgpt.com/backend-api/codex") == "/"


def test_codexapp_reverse_target_strips_backend_prefix_when_needed():
    assert (
        CODEX_APP.rewrite_upstream_path("/backend-api/codex/responses", "https://chatgpt.com/backend-api/codex")
        == "/responses"
    )


# --- usage extraction -----------------------------------------------------


def test_anthropic_extract_usage():
    body = {
        "usage": {
            "input_tokens": 100,
            "output_tokens": 50,
            "cache_read_input_tokens": 30,
            "cache_creation_input_tokens": 5,
        }
    }
    u = ANTHROPIC.extract_usage(body)
    assert u.input_tokens == 100
    assert u.output_tokens == 50
    assert u.cache_read_tokens == 30
    assert u.cache_create_tokens == 5


def test_openai_extract_usage_responses_shape():
    body = {"usage": {"input_tokens": 10, "output_tokens": 20, "input_tokens_details": {"cached_tokens": 4}}}
    u = OPENAI.extract_usage(body)
    assert u.input_tokens == 10
    assert u.output_tokens == 20
    assert u.cache_read_tokens == 4


def test_openai_extract_usage_chat_completions_shape():
    body = {"usage": {"prompt_tokens": 11, "completion_tokens": 22}}
    u = OPENAI.extract_usage(body)
    assert u.input_tokens == 11
    assert u.output_tokens == 22


def test_gemini_extract_usage():
    body = {
        "usageMetadata": {
            "promptTokenCount": 100,
            "candidatesTokenCount": 50,
            "cachedContentTokenCount": 30,
        }
    }
    u = GEMINI.extract_usage(body)
    assert u.input_tokens == 100
    assert u.output_tokens == 50
    assert u.cache_read_tokens == 30


def test_extractors_handle_garbage():
    for proto in (ANTHROPIC, OPENAI, GEMINI):
        u = proto.extract_usage("not-a-dict")
        assert u.input_tokens == 0
        u = proto.extract_usage({})
        assert u.input_tokens == 0
