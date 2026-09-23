# Design QA

## Current redesign acceptance contract

The evidence and measurements later in this document include prior row and
sidebar baselines. They remain useful regression history; the current pass
validates decisions 131-188 in `docs/VIEWER_DESIGN.md`.

The current pass must verify all of the following with a real multi-step trace:

1. Every center row represents one LLM request and uses the shared 64/58/52 px
   wide/medium/compact density contract. Rows form one restrained locator list;
   tool-bearing rows lead with direct tool calls, and compact rows yield activity
   to model, usage, duration, and time without leaving an empty rail.
2. Time is the primary locator; the tiny `#N` remains legible when space allows
   and yields entirely in the compact ledger. Model and `IN … · OUT …` usage
   have distinct hierarchy, and successful rows contain no `200` badge or
   status element. Narrow rows retain no clipped prose or tool fragments.
3. The active-session header contains no aggregate client/duration/token facts.
4. Messages preserve roles and source order and visibly retain each tool-call id
   and tool-result id. Object and array call/result payloads use the shared JSON
   tree without projecting away provider fields; primitive or malformed values
   use a lossless wrapped-text fallback. Matching ids can be correlated without
   moving blocks, and result errors do not paint the payload or page red.
5. Messages render one article per original visible content block, with exactly
   one body block and the original role, type, source path, ids, and order.
   Articles take their natural height. Every tool result shares a 240 px upper
   bound; only overflowing payloads scroll, and native boundary chaining returns
   continued wheel or trackpad input to the inspector.
6. The inspector has no fixed desktop ceiling and can grow until the center is a
   216 px compact locator. The resize handle works by pointer, keyboard,
   double-click reset/focus-size, and after reload.
7. Opening a collapsed sidebar at 800-1120 px produces the real expanded width,
   not a 48 px panel with expanded content clipped inside it.
8. Ledger concessions respond to the ledger container while resizing the
   inspector, not merely to viewport breakpoints; desktop, tablet, and phone
   layouts have no document-width overflow.
9. Selecting cached and prefetched neighboring records has no loading flash;
   concurrent selection/prefetch performs one detail fetch and stale responses
   cannot replace the active record.
10. JSON matches the DeepSeek Harness interaction contract: top-level open,
    nested branches closed, toggle only from the disclosure or field label,
    deepest hovered-row highlight, edge copy affordance, value/JSON/path context
    actions, Left/Right collapse-expand, Up/Down roving focus, and correct
    `tree`/`treeitem`/`group` and `aria-expanded` state.
11. Collapsed JSON branches do not construct descendant DOM. A large real trace
    remains responsive while expanding, searching, copying, and switching calls.
12. Anthropic, OpenAI Chat/Responses, Gemini, Codex, and an unknown synthetic
   content block retain all original identifiers and fields in readable or
   labelled fallback form.
13. Inspector position restores per record and tab and clamps after remount and
    responsive layout changes. A first visit inherits the active capture/tab
    offset; a revisit restores that call's exact offset. Stable long-message and
    tool-payload blocks preserve their local offsets too, and a shorter request
    cannot erase the intended position. Wrap, language, and full-value rerenders
    preserve the same reading state.
14. The collapsed desktop rail exposes an explicit panel toggle, and the
    sidebar footer does not repeat capture call counts or byte totals.
15. The sidebar is 280 px open and 56 px collapsed; hover/focus swaps the brand
    glyph for the panel glyph, delayed tooltips stay viewport-bounded, and the
    two-stage transition never clips expanded content. Below 800 px the drawer
    retains one bounded width while closed and open, moving only by transform.
16. The inspector uses a 42 px one-line request header, 34 px source-style tabs,
    one role/type header per raw request message, and one quiet surface per raw
    content block. Request direction uses the DeepSeek blue tertiary surface,
    model output uses the green tertiary surface, and tool payloads use the
    amber tertiary surface. Neutral bodies, borders, ids, phases, and formats
    keep those three semantic cues subordinate to source content. Expanded
    definitions remain compact and inset from the pane edge.
17. Messages appends response content after every request-side group. Anthropic
    Messages, OpenAI Chat, OpenAI Responses, and Gemini preserve response block
    order, roles, reasoning, tool-call ids, and arguments. Unknown and failed
    readable bodies remain visible, while response headers, usage fields, and
    SSE frames never become message blocks.
18. The ledger, Messages, and mounted-tool catalogue share one quiet wrench
    glyph. Raw tool names and descriptions retain the useful protocol semantics
    without turning a dense catalogue into a mixed icon gallery.
19. Ordinary message text and short tool payloads remain in the inspector's one
    scroll flow. Long message content is bounded at 340 px, while all tool
    results use `clamp(180px, 24vh, 240px)` so provider or tool names cannot
    produce inconsistent heights. Boundary wheel input returns to the inspector.

Required fresh evidence includes the flat wide ledger, metadata-only narrow
ledger, complete tool-call/result JSON, the shared tool glyph in all three surfaces,
ordinary and extreme scroll ownership, light and dark palettes, failed response,
matched tool call/result, wide inspector, desktop/tablet/mobile overflow,
JSON hover/copy/context menu, JSON keyboard navigation, and unknown-protocol
fallback. The final reference and implementation screenshots must be inspected
together in one comparison image.

## Final current-pass evidence

- 2026-08-22 implementation screenshots:
  `/tmp/claude-tap-qa-2026-08-22/31-ledger-clean-default.png`,
  `32-ledger-hover-preview.png`, `35-inspector-refined-messages.png`,
  `36-inspector-refined-tools.png`, `37-inspector-refined-compact.png`, and
  `38-current-reference-viewport-tools.png`.
- Same-viewport source comparison:
  `/tmp/claude-tap-qa-2026-08-22/39-reference-current-comparison.png` pairs the
  2048 x 918 DeepSeek reference and current implementation in one inspected
  input.
- Message grouping comparison:
  `/tmp/claude-tap-qa-2026-08-22/48-message-before-after-comparison.png` pairs
  the repeated-header/separator reference state with the final grouped raw
  message stream in one inspected input. Desktop detail, 390 px mobile, and dark
  evidence are `47-message-hierarchy-final.png`, `45-message-groups-mobile.png`,
  and `46-message-groups-dark.png`.
- DeepSeek palette evidence:
  `/tmp/claude-tap-qa-2026-08-22/53-role-blocks-deepseek-palette.png` verifies
  role-specific chips and content surfaces; `54-tools-accent-hierarchy.png`
  verifies blue mounted-tool names against neutral glyphs and descriptions.
  Same-viewport source reviews are
  `57-deepseek-tools-palette-comparison.png` and
  `58-deepseek-role-palette-comparison.png`; both pair the 2048 x 918 DeepSeek
  source state with the corresponding current implementation in one inspected
  image. Current 390 px and dark-theme checks are
  `59-message-palette-mobile-current.png` and
  `60-message-palette-dark-current.png`; both measured zero document overflow.
- Measured at 1440 px: five 52 px rows; mixed and tool-bearing rows contain a
  40 px activity area with two 20 px tool lines, no persistent prose, and one
  viewport-bounded hover preview. The document does not overflow; the sidebar
  is 280 px open and 56 px collapsed.
- Measured at 390 and 320 px: five 60 px rows, two vertical tools remain visible,
  exact `+N` is retained, the drawer is 286/280 px, and the document does not
  overflow.
- Messages render one natural-height article per visible raw content block.
  Normal bodies have visible overflow; only extreme payloads gain a bounded
  scroller, which chains to the inspector at its native boundary. Record/tab
  switching restores the inspector's outer reading position.
- The final inspector uses a 42 px one-line request header and 34 px tabs. Each
  source message has one compact role/type header; its original content blocks
  remain separate, naturally sized articles with six-pixel rhythm and no
  repeated divider lines. All roles share the same neutral card and header band;
  title-case role text and order distinguish them. Tool names and glyphs use the
  same blue accent in the ledger and Messages, while payloads remain secondary
  and ids/phases/formats remain captions. Expanded definitions remain neutral
  and compact.
- The collapsed toggle keeps the brand glyph at rest, swaps to the panel glyph
  on hover/focus without opening, and exposes a viewport-bounded tooltip after
  500 ms. Reduced-motion skips transition phases.
- The real five-call Claude Code trace appends response text and tool calls
  after request groups. Request #2 shows one assistant output group containing
  its prose followed by three tool calls and their exact ids/JSON arguments;
  request #5 shows the complete final model text. Evidence is
  `/tmp/claude-tap-qa-2026-08-22-sidebar/72-output-tool-calls.png` and
  `74-output-final.png`.
- Output remains inside the single inspector scroller at 1440 px and 390 px;
  both document and inspector measured `scrollWidth <= clientWidth`. Mobile and
  dark evidence is `75-output-mobile.png` and `76-output-dark.png`. The current
  2048 x 918 source comparison is
  `78-reference-current-combined.png`, inspected as one combined image.

### Prior retained evidence (superseded geometry)

- Earlier same-day sidebar evidence:
  `/tmp/claude-tap-qa-2026-08-22/07-open-unified.png`,
  `/tmp/claude-tap-qa-2026-08-22/09-collapsed-hover-unified.png`, and
  `/tmp/claude-tap-qa-2026-08-22/mobile-390-unified-final2.png`.

- Real trace: `.traces/2026-08-21/trace_160833.jsonl`, five visible Claude Code
  LLM requests plus one retained but intentionally hidden auxiliary request.
- Browser evidence: `/tmp/claude-tap-redesign-qa-final/`, including the 2048 px
  overview, ordered messages, JSON hover/context menu, 1120 px inspector,
  collapsed rail, 390 px overview/messages, tool-call/result blocks, and mounted
  tool schema.
- Combined source comparison:
  `/tmp/claude-tap-redesign-qa-final/11-reference-comparison.png`.
- Measured at 2048 px: sidebar 228 px, compact-safe center 700 px, inspector
  1120 px, five 56 px rows, identical 8.5 px first-rail offsets, no routine
  status badges, no active nested vertical scroller, and no document or
  inspector horizontal overflow.
- Measured at 390 px: five 62 px rows, full `HH:mm:ss`, hidden secondary
  ordinals, and no document overflow.
- Per-call reading position restored exactly from 320 px to 320 px after a
  neighboring selection. User wheel, pointer, touch, or scroll-key input cancels
  delayed restoration.
- The collapsed desktop rail is 44 px and exposes the actual panel toggle; the
  footer contains no repeated request or byte totals.
- Final browser run produced zero console errors and zero page errors.

- Source visual truth: `/tmp/paseo-attachments-dUzj2B/6ee092ee5d77762b581b3bfca9a615f1ae62ff7eb4893e8917c328515fe21a8b.png`
- Focused source truth: `/tmp/paseo-attachments-dUzj2B/cff8384cbe05cd7d7e778cfa56e85b99da206fe411c1cb8b74fa54346de066cf.png`
- Source implementation: DeepSeek Harness commit `99f6f02fecdb7dff40c3fbc9470f5907c29f74ca`
- Browser: Playwright Chromium, device scale factor 1
- Real data: `.traces/2026-08-19/trace_223911.jsonl`, one Claude Code session, 34 model calls
- Final screenshot set: `/tmp/claude-tap-qa-2026-08-21/`
- Error and scroll follow-up set: `/tmp/claude-tap-audit-2026-08-21-error-scroll/`
- Clean-ledger follow-up set: `/tmp/claude-tap-audit-2026-08-21-clean-ledger/`
- Direct-tool and fresh-trace set: `/tmp/claude-tap-audit-2026-08-21-direct-tools/`
- Final source/prototype comparison: `/tmp/claude-tap-audit-2026-08-21-direct-tools/17-reference-final-inline-time.png`
- Fresh real trace: `.traces/2026-08-21/trace_160833.jsonl`, one Claude Code session, 5 LLM calls plus 1 hidden token-count request
- Self-contained export: `/tmp/claude-tap-final-2026-08-21.html`

## Visual and interaction inspection

| # | Inspected state | Health | Evidence |
|---:|---|---|---|
| 1 | Desktop call ledger with inspector closed | Passed | `01-desktop-overview-2048.png` |
| 2 | Ordered request messages | Passed | `02-desktop-messages-2048.png` |
| 3 | One continuous system prompt | Passed | `03-desktop-system-2048.png` |
| 4 | Mounted tools and expanded schema | Passed | `04-desktop-tools-2048.png` |
| 5 | Foldable request JSON | Passed | `05-desktop-request-json-2048.png` |
| 6 | Response JSON | Passed | `06-desktop-response-json-2048.png` |
| 7 | Complete raw record | Passed | `07-desktop-raw-2048.png` |
| 8 | Collapsed desktop session rail | Passed | `08-desktop-collapsed-2048.png` |
| 9 | 1120 px compact inspector layout | Passed | `09-compact-messages-1120.png` |
| 10 | 820 px full-screen inspector | Passed | `10-tablet-messages-820.png` |
| 11 | 390 px model-call overview | Passed | `11-mobile-overview-390.png` |
| 12 | 390 px session drawer | Passed | `12-mobile-sessions-390.png` |
| 13 | Naturally wrapped mobile messages | Passed | `13-mobile-messages-390.png` |
| 14 | Mobile mounted-tool schema | Passed | `14-mobile-tools-390.png` |
| 15 | 320 px JSON and six-tab navigation | Passed | `15-mobile-request-320.png` |
| 16 | Dark mounted-tool state | Passed | `16-dark-tools-1440.png` |
| 17 | Self-contained static export | Passed | `17-static-export-1440.png` |
| 18 | Final post-resize desktop ledger | Passed | `18-final-overview-after-resize.png` |
| 19 | Final post-resize mounted-tool catalog | Passed | `19-final-tools-after-resize.png` |
| 20 | Quiet SSE failure with request context | Passed | `04-error-messages-after.png` |
| 21 | Typed message/tool call ledger | Passed | `05-call-ledger-after.png` |
| 22 | Role-separated successful messages | Passed | `06-success-messages-after.png` |
| 23 | Inherited cross-call reading position | Passed | `07-switch-scroll-after.png` |
| 24 | Real HTTP 529 with request body visible | Passed | `08-http-529-after.png` |
| 25 | Mobile HTTP 529 inspector | Passed | `09-mobile-http-529-after.png` |
| 26 | Before: synthetic event labels and long tool arguments | Failed | `01-before-cluttered-ledger.png` |
| 27 | Desktop single-call output ledger | Passed | `05-desktop-clean-ledger.png` |
| 28 | Mobile single-call output ledger | Passed | `07-mobile-clean-ledger-final.png` |
| 29 | Fresh real Claude Code trace with direct `Read`/`Bash` calls | Passed | `05-final-fresh-trace-overview.png` |
| 30 | Whole-row JSON expansion target | Passed | `06-json-row-expanded.png` |
| 31 | Compact mounted-tool schema rhythm | Passed | `07-compact-tool-schema.png` |
| 32 | Per-call timestamp at the desktop reading edge | Passed | `10-time-overview-desktop.png` |
| 33 | Compact `HH:mm` timestamp on a 390 px viewport | Passed | `11-time-overview-mobile.png` |
| 34 | Fresh selected request with timestamps and Messages | Passed | `12-final-selected-messages-time.png` |
| 35 | Inline call/time rhythm at desktop width | Passed | `14-inline-call-time-desktop.png` |
| 36 | Inline call/time rhythm at 390 px | Passed | `15-inline-call-time-mobile.png` |
| 37 | Selected Messages view with final inline locator | Passed | `16-final-inline-time-messages.png` |

The 2048 x 918 source reference, its focused trajectory reference, the final
overview, and the final mounted-tool state were inspected together in one
comparison input after the last code change. The implementation now follows the
source's flat surfaces, one-pixel separators, compact control seats, restrained
neutral chrome, orange tool activity, and continuous tool-name/argument rhythm.
Intentional product differences remain: the center is a ledger of captured LLM
requests, not a reconstructed agent runtime, so it omits the composer, duplicate
timeline, and synthetic tool-result rows.

## Follow-up audit resolved

The final passes found and fixed 44 additional issues, numbered 87-130 in
`docs/VIEWER_DESIGN.md`. The pass removed decorative selection and error rails,
eliminated blank tool-only/error rows, stabilized desktop/mobile row heights at
56/62 px, aligned call indices and tool glyphs mathematically, grouped multiple
tool calls inline, tightened header and toolbar geometry, simplified status
language, strengthened hairline boundaries, and prevented virtualized rows from
being detached during responsive width transitions. The follow-up retained
request context on failures, replaced alarming page-level error chrome with a
quiet response status, kept protocol vocabulary stable across locales, clarified
message boundaries, and preserved reading position while switching calls.
The latest reduction pass removed the synthetic event taxonomy from the call
ledger, removed tool arguments from its default view, bounded long prose, kept
tool names adjacent to the output they came from, and replaced clipped mobile
tool fragments with an exact remainder count. The latest pass restored direct
per-call `Bash`/`Read` summaries, removed auxiliary token-count traffic from the
LLM ledger, widened JSON disclosure hit targets to the whole row, and removed
the redundant mounted-tool body indentation.
Each call now also exposes its occurrence time before the output on the same
baseline as its ordinal. Seconds remain visible at compact widths while the
secondary ordinal yields.

The current pass keeps two stacked 20 px tool lines inside the 40 px activity
area and moves optional prose into the shared hover/focus preview, so text-only,
tool-only, and mixed rows no longer use competing templates. It also projects
each original request content block into one natural-height article. Ordinary
blocks stay in the inspector's reading flow; only extreme payloads receive a
bounded local scroller with native boundary handoff.

## Prior measured results (retained)

These measurements document superseded geometry and scroll behavior. Decisions
153-156 and the current-pass evidence above define the current implementation.

- Widths 2048, 1120, 820, 390, and 320 px all satisfy `scrollWidth <= clientWidth`.
- Desktop model-call rows are exactly 56 px; mobile rows are exactly 62 px.
- Row boundaries are one pixel, selected/error pseudo-rails are absent, and both
  call-index and tool-glyph vertical center deltas measure zero.
- Inspector widths are 820 px at 2048, 504 px at 1120, and a full-width 820 px
  overlay at the tablet breakpoint.
- At 820 and 320 px the underlying call surface is inert while the inspector is
  open; closing it restores the call surface.
- Mobile 320 px tabs and inspector content remain inside their client width.
- Static export contains all 34 calls and opens the inspector successfully.
- A real HTTP 529 call retains both request messages below its compact response
  status, while an SSE failure over HTTP 200 is labelled `SSE error`.
- Switching from a call at 280 px scroll to an unread call preserves the same
  280 px position; revisiting a call restores its own position.
- The center ledger contains no `MESSAGE`, `TOOLS`, `REQUEST`, or `RESPONSE`
  event labels and no full tool command/path payloads. Desktop shows up to two
  direct tool calls with bounded hints; mobile shows one complete call plus `+N`.
- The fresh trace renders 5 actual model calls; its `/count_tokens` request is
  retained in raw capture data but omitted from the session ledger and totals.
- Fresh-trace call rows show `16:08:38 #1` through `16:09:55 #5` on one
  baseline at desktop width. At 390 px, `HH:mm:ss` remains while the secondary
  ordinal yields; measured first-content rail delta is zero.
- Clicking a JSON disclosure or expandable field label changes
  `aria-expanded`; clicking its preview does not. Both document and inspector
  overflow remain zero.
- All inspected states produced zero console errors and zero page errors.

## Functional coverage

- Sessions use normalized header/body identities for Claude Code, Codex,
  DeepSeek Harness, OpenAI Responses/Chat, Gemini, and generic clients, while
  preserving explicit thread, turn, parent, and agent relationships.
- Messages preserve the raw request role and content-block order. System,
  mounted tools, request JSON, response JSON, and raw data remain separate views.
  Each source request message has one role/type summary and contains one article
  per visible raw block, preserving paths, ids, order, and natural height. Only
  extreme payloads scroll locally; inspector position is restored per request
  and tab.
- Center rows summarize each actual model request with up to two stacked tool
  calls, hover/focus prose preview, model, input/output token counts, latency,
  and factual exceptional status.
- Tool arguments/results remain compact complete JSON text with natural wrapping.
  Extreme message-body overflow chains to the inspector at its native boundary.
- Mounted tools normalize Anthropic `input_schema`, OpenAI parameters/custom
  formats, Gemini declarations, Codex namespace tools, and unknown future blocks.
- JSON objects, arrays, and long strings fold independently; chevrons and
  `aria-expanded` agree; search opens matching ancestors; keys never split.
- Virtualized rows stay mounted across width-only responsive transitions, so
  selection, focus, pointer targets, and measurements do not race layout changes.

## Quality gates

- `uv run ruff check .` - passed
- `uv run ruff format --check .` - passed, 46 files already formatted
- `uv run pytest tests/ -x --timeout=60` - passed, 330 tests
- Responsive resize regression - passed five consecutive browser runs
- Browser suite - passed, 14 tests covering drawer bounds, inert surfaces, stream
  errors, provider blocks, tool schemas, JSON folding, wrapping, and inherited
  per-call scroll state, plus compact single-call ledger summaries
- `git diff --check` - passed

prior baseline result: passed (superseded by the acceptance contract above)

## 2026-08-22 final gate

- `uv run ruff check .` — passed.
- `uv run ruff format --check .` — passed, 46 files already formatted.
- `uv run pytest tests/ -x --timeout=60` — passed, 330 tests.
- `git diff --check` — passed.
- Browser contract — passed, 23 tests covering the 280/56 px sidebar,
  transition phases, hover/focus icon swap, delayed tooltip, reduced motion,
  stacked-tool activity states, raw-block message projection, extreme-payload
  scroll handoff, responsive layouts, inspector geometry, protocol fallbacks,
  and JSON interaction.
- Response-output contract — passed for Anthropic Messages, OpenAI Chat,
  OpenAI Responses, Gemini, unknown success, primitive response bodies, and
  readable HTTP failures. Request groups always precede output groups; usage
  envelopes and SSE event frames do not render as messages.
- Live preview — local and `n251-232-042.byted.org:8080` both returned HTTP 200.
- Latest implementation review — the clean ledger, hover preview, natural block
  stream, raw-message grouping, expanded neutral schema, focused tool-call/result
  blocks, and compact inspector were inspected in `31-ledger-clean-default.png`,
  `32-ledger-hover-preview.png`, `35-inspector-refined-messages.png`,
  `36-inspector-refined-tools.png`, `37-inspector-refined-compact.png`, and
  `47-message-hierarchy-final.png`. The final same-viewport source comparison is
  `39-reference-current-comparison.png`; the final message-state comparison is
  `48-message-before-after-comparison.png`. The final DeepSeek palette review is
  captured in `57-deepseek-tools-palette-comparison.png` and
  `58-deepseek-role-palette-comparison.png`.

## 2026-08-23 neutral message-system gate

- `uv run ruff check .` — passed.
- `uv run ruff format --check .` — passed, 46 files already formatted.
- `uv run pytest tests/ -x --timeout=60` — passed, 330 tests in 34.02s.
- Focused message and scroll contract — passed, 53 tests. Tests now protect
  protocol order, block identity, accessibility, overflow, and scroll ownership
  without freezing discarded role colors or incidental pixel gaps.
- Palette contract — passed. User, system, assistant, tool-call, and tool-result
  content now share one neutral card system. Role is conveyed by stable header
  text and source order; mounted tools and model-output tools share one blue
  action accent. Error data remains readable without recoloring an entire block.
- Content contract — passed. Request blocks and normalized model output remain
  in one ordered message stream with tool ids, arguments, and results intact.
  Ordinary blocks expand naturally; only extreme payloads receive a bounded
  local scroller with boundary handoff to the inspector.
- Cleanup contract — passed. Removed dead preview helpers, raw-message JSON CSS,
  duplicate render branches, legacy message classes, duplicated mobile rules,
  and obsolete per-role color tokens.
- Browser inspection — passed at desktop light, desktop dark, mobile, and the
  720 x 1024 CSS-pixel reference viewport. Document and inspector horizontal
  overflow measured zero, role card surfaces were identical within each theme,
  and the page produced zero console or page errors.
- Visual evidence — `04-final-light.png`, `05-final-dark.png`,
  `06-final-mobile.png`, and `07-inspector-reference-scale.png`. The required
  same-scale source/prototype review is `08-reference-current-combined.png`.

## 2026-08-23 flat ledger, semantic tools, and restrained-scroll gate

- `uv run ruff check .` — passed.
- `uv run ruff format --check .` — passed, 47 files already formatted.
- `uv run pytest tests/ -x --timeout=60` — passed, 331 tests in 40.28s.
- Focused viewer contract — passed, 54 tests covering raw tool JSON, the shared
  wrench glyph, flat wide and compact ledgers, message scroll ownership, provider
  blocks, JSON interaction, sidebar behavior, and responsive overflow.
- Ledger contract — passed. Wide and compact states are one continuous flat
  request stream with no rounded row cards or nested metadata slabs. At 514 px
  the activity rail yields entirely while time, model, `IN`/`OUT`, and duration
  remain visible. Measured row radius and margin were `0px`; document and row
  horizontal overflow were zero.
- Tool contract — passed. Complete call/result objects render through the shared
  foldable JSON tree. One quiet wrench glyph is shared across the ledger,
  Messages, and mounted-tool catalogue while every raw tool name and identifier
  remains visible.
- Palette contract — passed. Request, output, and tool hierarchy uses the
  DeepSeek blue, green, and amber tertiary surfaces respectively, with neutral
  bodies and borders. Light and dark themes produced zero document overflow and
  zero console or page errors.
- Scroll contract — passed. Ordinary messages and ordinary tool JSON stay in
  the inspector's single scroll flow. Payloads over 3,200 characters or 40 line
  breaks receive the bounded `clamp(260px, 44vh, 440px)` local reading region.
- Visual evidence — `/tmp/claude-tap-qa-final-2026-08-23/01-center-wide-flat.png`,
  `02-messages-json-icons.png`, `03-mounted-tools-icons.png`,
  `04-messages-dark.png`, `05-center-narrow-flat-retina.png`,
  `06-messages-tool-json-focused.png`, and
  `07-center-narrow-flat-reference-size.png`. The inspected same-size
  before/current comparison is `08-reference-current-comparison.png`.
- Live preview — local ports 8080 and 8765 and
  `n251-232-042.byted.org:8080` returned HTTP 200.
- `git diff --check` — passed.

final result: passed

## 2026-08-23 uniform tool glyph and message-frame correction

- Tool icon contract — passed. The semantic classifier and provider alias table
  were removed. Ledger activity, tool-count metadata, message call/results, and
  all 68 mounted-tool rows render the same attributed wrench glyph; the browser
  regression compares the SVG path across all three surfaces.
- Message frame contract — passed. Request-message groups use the default panel
  border rather than the former hairline, while internal block separators remain
  subtle so the group is visible without adding another colored rail.
- Long-content contract — passed on the real five-call trace. The first User
  group dropped from 1,587 px to 583 px; its 3,571-character source block owns a
  440 px reading region while the following 323-character block stays natural.
  Native inner-to-inspector scroll handoff remains covered by browser tests.
- Visual evidence — `/tmp/claude-tap-icon-frame-qa/01-messages-frame-long-content.png`,
  `/tmp/claude-tap-icon-frame-qa/02-tools-uniform-wrench.png`, and the inspected
  user-reference/current comparison `03-reference-current-comparison.png`.
- Focused regression — 34 passed.
- Full repository gate — passed: `uv run ruff check .`, `uv run ruff format
  --check .` (47 files), `uv run pytest tests/ -x --timeout=60` (332 tests in
  37.37s), and `git diff --check`.
- Live preview — local ports 8080 and 8765 returned HTTP 200 with the corrected
  renderer.

final result: passed

## 2026-08-23 unified theme and trace-list gate

- Theme authority — passed. The root light/dark theme now owns every literal
  color. Viewer components consume semantic surface, border, text, brand, role,
  status, tool, and syntax tokens; a regression test rejects component-level
  RGB, RGBA, or hex literals.
- Trace-list hierarchy — passed. The center is one continuous flat list rather
  than a stack of rounded cards. Output and vertically stacked tool activity
  lead each wide row; model, `IN`/`OUT`, cache share, tool count,
  special request kind, duration, and exceptional status form the quiet rail.
- Responsive density — passed. A 1,028 x 922 call panel measured zero horizontal
  overflow with five 72 px wide rows. Opening the inspector reduced the call
  panel to 504 px, switched its ResizeObserver state to `compact`, and produced
  five 56 px metadata-only rows with no blank activity band.
- Useful disclosure — passed. Hover/focus previews expose complete assistant and
  tool activity. Usage disclosure reports fresh input, cache read, cache write,
  and output; overload, rate limit, timeout, and failure use exception-only
  status chips while routine HTTP 200 remains absent.
- Inspector hierarchy — passed. Messages keeps request and normalized response
  output in one ordered stream, with natural-height blocks and complete foldable
  tool JSON. Mounted Tools uses the same quiet wrench/name/description pattern
  and compact schema hierarchy in both themes.
- Visual comparison — passed. The user-provided 1,028 x 922 former center state
  and `/tmp/claude-tap-design-system-qa/05-current-call-panel-1028x922.png`
  were inspected together at the same size. The new state removes nested slabs,
  four-sided borders, oversized empty rows, repeated normal-status badges, and
  model-first hierarchy while retaining the same five calls.
- Visual states — passed. Light Messages output, light Tools, dark Tools, and
  light Messages were inspected in `/tmp/claude-tap-design-system-qa/06-` through
  `09-*.png`; document and inspector overflow measured zero and the browser
  produced zero console or page errors.
- Browser regression — passed, 24 tests covering sidebar behavior, scroll
  ownership, container-driven 72/64/56 px density, vertical tools, cache detail,
  semantic statuses, provider blocks, JSON interaction, and the uniform tool
  glyph. The collapsed-sidebar hover geometry regression also passed five
  consecutive runs after adding the post-motion settle window.
- Full repository gate — passed: `uv run ruff check .`, `uv run ruff format
  --check .` (47 files), `uv run pytest tests/ -x --timeout=60` (332 tests in
  43.39s), and `git diff --check`.
- Live preview — local ports 8080 and 8765 and
  `n251-232-042.byted.org:8080` returned HTTP 200; the public document includes
  the semantic token system and current `call-entry` renderer.

final result: passed

## 2026-08-26 compact ledger, wide inspector, and uniform tool-result gate

- Source evidence — the user-reported unbounded `Bash` result is
  `/tmp/paseo-attachments-dUzj2B/72016faaaeed16edca4071b28602a1e45c4329d8bba2160e0c1e5aff6c0492f1.png`.
  It was inspected together with the corrected real-trace screenshot
  `/tmp/claude-tap-final-bash-cap.png` in one comparison input after the final
  CSS change.
- Tool-result contract — passed on real request #3. Two `Read` results and the
  `Bash` result all measured 238 px visible height with a computed 240 px
  maximum and `overflow-y: auto`. Their complete scroll heights were 1698,
  1443, and 389 px respectively, so no source output was truncated.
- Ledger and inspector contract — passed. The keyboard maximum measured a
  216 px call locator and 1552 px inspector at a 2048 px viewport. Compact rows
  measured 52 px, preserve model/usage/time/duration, and the document measured
  zero horizontal overflow. Evidence is `/tmp/claude-tap-final-focus.png`.
- Mobile contract — passed at 390 x 844. The inspector occupied the viewport,
  both document and inspector-body horizontal overflow measured zero, and the
  long `Read` and `Bash` results retained the same bounded treatment. Evidence
  is `/tmp/claude-tap-final-mobile-bash.png`.
- Scroll contract — passed. First visits inherit the active capture/tab offset,
  revisits restore the call-specific offset, stable long payloads restore their
  own offset, and a short intervening request cannot erase either position.
  Long local payloads consume wheel input until their boundary, then continued
  input returns to the inspector.
- Browser regression — passed, 58 viewer tests including dedicated long Bash
  output case, provider normalization, JSON behavior, resizing, responsive
  overflow, prefetching, scroll inheritance, and nested-scroll handoff.
- Full repository gate — passed: `uv run ruff check .`, `uv run ruff format
  --check .` (47 files), `uv run pytest tests/ -x --timeout=60` (335 tests), and
  `git diff --check`.
- Live preview — local ports 8080 and 8765 returned HTTP 200. The real-trace
  browser run produced zero console and page errors.

final result: passed
