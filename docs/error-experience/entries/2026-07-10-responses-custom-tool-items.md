# Responses custom tool items missing from the viewer

Date: 2026-07-10

## What broke

Codex traces using `custom_tool_call` and `custom_tool_call_output` kept the
items in Raw JSON but omitted them from the readable request and response
sections. Tool filters and lazy metadata also missed custom calls.

## Root cause

The HTML viewer normalized only `function_call` and `function_call_output`.
Response rendering also trusted `response.completed.response.output`, which can
be empty even when completed items were delivered in `response.output_item.done`
events.

## What actually fixed it

Responses input and output now share one item normalizer:

- every `*_call` is rendered as a tool call;
- every `*_call_output` is rendered as a tool result;
- unknown item and content types fall back to a labeled full JSON block;
- completed streamed items are used when the final response output is empty;
- lazy metadata recognizes the same call types.

The behavior was verified in Chromium with the `gpt-5.6-sol` request shape,
including free-form custom input, array output content, call IDs, status, and
synthetic future item types on both request and response paths.

## Lessons

1. Protocol viewers must preserve unknown records instead of silently dropping
   them.
2. Normalize request history, response output, search/filter metadata, and
   streamed fallback through the same item-type rules.
3. Use type families for evolving API records, with specialized rendering only
   where it improves readability without hiding fields.

## Follow-up: opaque reasoning content

The generic fallback initially rendered `reasoning` items with an empty
`summary` as full JSON, including very large `encrypted_content` values. These
values are useful for protocol continuity but have no readable value in the
message view. Empty-summary reasoning now renders as one compact row that says
the content is unavailable and reports only its size. Protocol metadata and the
original value remain available in Full JSON.
