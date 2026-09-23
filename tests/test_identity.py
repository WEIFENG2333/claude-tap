"""Identity adapters for real AI CLI request shapes."""

from claude_tap.identity import context_from_record, extract_trace_context


def test_claude_code_session_and_subagent_headers():
    context = extract_trace_context(
        {
            "User-Agent": "claude-cli/2.1.234 (external, sdk-cli)",
            "X-Claude-Code-Session-Id": "bbe343b5-8c6c-4b76-9dca-f33e286c5298",
            "X-Claude-Code-Agent-Id": "agent-child",
            "X-Claude-Code-Parent-Agent-Id": "agent-root",
        },
        {"metadata": {"user_id": "not-a-session"}},
        "/v1/messages?beta=true",
    )

    assert context["client"] == "claude-code"
    assert context["client_version"] == "2.1.234"
    assert context["protocol"] == "anthropic"
    assert context["session_id"] == "bbe343b5-8c6c-4b76-9dca-f33e286c5298"
    assert context["session_id_source"] == "header.x-claude-code-session-id"
    assert context["agent_id"] == "agent-child"
    assert context["parent_agent_id"] == "agent-root"


def test_codex_turn_metadata_wins_and_exposes_hierarchy():
    context = extract_trace_context(
        {
            "User-Agent": "codex_exec/0.144.1 (Mac OS; arm64)",
            "Originator": "codex_exec",
            "Session-Id": "header-session",
            "Thread-Id": "header-thread",
            "X-Codex-Turn-Metadata": (
                '{"session_id":"meta-session","thread_id":"meta-thread",'
                '"turn_id":"turn-7","window_id":"meta-session:0","request_kind":"turn"}'
            ),
        },
        {},
        "/v1/responses",
    )

    assert context["client"] == "codex"
    assert context["session_id"] == "meta-session"
    assert context["thread_id"] == "meta-thread"
    assert context["turn_id"] == "turn-7"
    assert context["window_id"] == "meta-session:0"
    assert context["request_kind"] == "turn"


def test_codex_client_metadata_is_body_fallback():
    context = extract_trace_context(
        {"Originator": "codex_exec"},
        {"client_metadata": {"session_id": "s1", "thread_id": "t1", "turn_id": "u1"}},
        "/v1/responses",
    )
    assert context["session_id"] == "s1"
    assert context["session_id_source"] == "body.client_metadata.session_id"
    assert context["thread_id"] == "t1"
    assert context["turn_id"] == "u1"


def test_deepseek_harness_session_header():
    context = extract_trace_context(
        {"X-DeepSeek-Harness-Session-Id": "dsh-session", "User-Agent": "deepseek-harness/0.1.0-rc.7"},
        {},
        "/chat/completions",
    )
    assert context["client"] == "deepseek-harness"
    assert context["session_id"] == "dsh-session"


def test_pi_and_hermes_underscore_session_header():
    pi = extract_trace_context(
        {"User-Agent": "pi-coding-agent/0.72.1", "session_id": "pi-session"},
        {},
        "/ai/v1/chat/completions",
    )
    hermes = extract_trace_context(
        {"User-Agent": "codex_cli_rs/0.0.0 (Hermes Agent)", "session_id": "hermes-session"},
        {},
        "/backend-api/codex/responses",
    )
    assert (pi["client"], pi["session_id"]) == ("pi", "pi-session")
    assert (hermes["client"], hermes["session_id"]) == ("hermes", "hermes-session")


def test_grok_session_agent_and_turn_headers():
    context = extract_trace_context(
        {
            "User-Agent": "grok/1.0.3",
            "X-Grok-Session-Id": "grok-session",
            "X-Grok-Agent-Id": "grok-agent",
            "X-Grok-Conv-Id": "do-not-use-as-session",
            "X-Grok-Turn-Idx": "4",
        },
        {},
        "/v1/responses",
    )
    assert context["session_id"] == "grok-session"
    assert context["agent_id"] == "grok-agent"
    assert context["turn_id"] == "4"


def test_grok_shell_warmup_without_ids_is_still_classified_as_grok():
    context = extract_trace_context(
        {"User-Agent": "grok-shell/1.0.3 (linux; x86_64)", "X-Grok-Session-Id": ""},
        {},
        "/v1/responses",
    )
    assert context["client"] == "grok"
    assert context["client_version"] == "1.0.3"
    assert "session_id" not in context


def test_opencode_parent_session_is_hierarchy_not_primary_session():
    context = extract_trace_context(
        {
            "User-Agent": "opencode/1.14.33",
            "X-Session-ID": "child",
            "X-Parent-Session-ID": "parent",
        },
        {},
        "/v1/messages",
    )
    assert context["client"] == "opencode"
    assert context["session_id"] == "child"
    assert context["parent_thread_id"] == "parent"


def test_openai_conversation_and_previous_response_link():
    context = extract_trace_context(
        {"User-Agent": "OpenAI/JS 6.38.0"},
        {"conversation": {"id": "conv_123"}, "previous_response_id": "resp_123"},
        "/v1/responses",
    )
    assert context["client"] == "unknown"
    assert context["session_id"] == "conv_123"
    assert context["thread_id"] == "conv_123"
    assert context["previous_response_id"] == "resp_123"


def test_gemini_has_no_invented_session_id():
    context = extract_trace_context(
        {"User-Agent": "GeminiCLI/0.40.1/linux"},
        {"contents": [{"role": "user", "parts": [{"text": "hello"}]}]},
        "/v1beta/models/gemini-2.5-flash-lite:generateContent",
    )
    assert context["client"] == "gemini-cli"
    assert context["protocol"] == "gemini"
    assert "session_id" not in context


def test_nested_tool_session_id_is_never_scanned():
    context = extract_trace_context(
        {"User-Agent": "claude-cli/2.1.75"},
        {
            "messages": [
                {
                    "role": "assistant",
                    "content": [{"type": "tool_use", "name": "process", "input": {"sessionId": "terminal-7"}}],
                }
            ]
        },
        "/v1/messages",
    )
    assert context["client"] == "claude-code"
    assert "session_id" not in context


def test_previous_response_id_alone_is_not_a_session():
    context = extract_trace_context({}, {"previous_response_id": "resp_1"}, "/v1/responses")
    assert "session_id" not in context
    assert context["previous_response_id"] == "resp_1"


def test_context_from_record_backfills_old_trace():
    context = context_from_record(
        {
            "request": {
                "path": "/v1/messages",
                "headers": {"X-Claude-Code-Session-Id": "old-trace-session"},
                "body": {},
            }
        }
    )
    assert context["session_id"] == "old-trace-session"
