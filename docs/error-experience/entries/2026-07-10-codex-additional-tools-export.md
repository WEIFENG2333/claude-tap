# Codex additional tools missing from prompt export

Date: 2026-07-10

## What broke

Codex CLI 0.144.x prompt exports rendered `_No tools captured._` even though
the CLI still sent tool definitions in its Responses API request.

## Root cause

Codex moved its tool declarations from the top-level `tools` field to an
`additional_tools` item inside the request `input` list. Prompt snapshot
scoring and rendering only inspected the former location.

## What actually fixed it

OpenAI prompt normalization now combines tools from both locations before
scoring and rendering a snapshot:

- `body.tools[]`
- `body.input[].tools[]` when the input item type is `additional_tools`

The behavior was verified against a real Codex 0.144.0 trace, which exported
the `collaboration`, `exec`, `request_user_input`, and `wait` tool declarations.

## Lessons

1. Treat tool declarations as part of provider normalization rather than
   reading a single wire-format field directly in multiple code paths.
2. When a client upgrade appears to remove tools, inspect the complete request
   input before concluding that the tool surface was removed.
3. Validate prompt exporters against real traces after client protocol changes.
