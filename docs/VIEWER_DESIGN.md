# Viewer design

The viewer is a raw LLM-request inspector, not a reconstructed agent transcript
or a generic HTTP debugger. Its primary task is to answer three questions
quickly:

1. Which logical CLI session did this request belong to?
2. What exact ordered messages and content blocks were sent to the model?
3. What did the model return, which tools did it call, and what exact arguments
   were emitted in that response?

The visual reference is DeepSeek Harness at commit `99f6f02`. The viewer uses
the same flat, dense, hairline-separated design language while keeping a
different information model: every center row is one captured LLM request.

## Current interaction contract

Decisions 131-188 below are the current contract. They supersede conflicting
historical resolutions in the audit log without erasing why those earlier
choices were made.

- The ledger has exactly one fixed-height row per captured LLM inference
  request. It never inserts separate message, tool, or result rows.
- A wide desktop call row is 64 px tall. Its quiet metadata rail and activity area
  share the available width instead of reserving rigid table columns. Rows form
  one continuous flat stream with hairline separators, not a stack of rounded
  cards. Tool-bearing rows lead with up to two direct calls in source order;
  assistant prose moves to the shared hover/focus preview. Text-only rows retain
  prose as their primary line. Medium and compact panes use 58 px and 52 px rows.
- Local time is the primary locator. The session ordinal is a tiny `#N` aid,
  not a dedicated column or stacked control, and yields before the timestamp
  when the ledger reaches its compact width.
- Routine 2xx transport status creates no badge. Only exceptional HTTP or
  stream state is shown, and a tool-result error remains ordinary request
  content rather than becoming a page-level alarm.
- Model identity is the primary metadata. Input and output usage form one quiet
  `IN … · OUT …` caption with distinct emphasis; aggregate session token facts
  are not repeated above the ledger.
- Messages preserve request roles, source-array order, content-block order,
  provider block types, tool-call ids, and tool-result ids. Each visible raw
  content block becomes exactly one flat article with its original role, type,
  and source path; matching ids expose relationships without moving or merging
  blocks.
- Messages finish the captured exchange with one explicit `RESPONSE` divider,
  followed by the model response after every request-side group. Response adapters extract only model content from
  Anthropic Messages, OpenAI Chat, OpenAI Responses, and Gemini envelopes;
  transport headers, usage summaries, and SSE frames stay out of this view.
  Unknown or failed readable bodies remain available as labelled lossless
  fallbacks.
- Request and output groups use the same restrained group construction. User
  and assistant groups receive quiet blue and green role surfaces; the response
  divider explains direction without introducing another card system. Concurrent
  tools share their message group's surface, while only each readable payload
  receives a neutral inset. Color explains hierarchy without turning tool-result
  failures into page-level errors.
- Message articles use their natural content height. Ordinary content never
  creates a nested vertical scroller; only genuinely extreme text, thinking,
  raw, or tool payloads receive a bounded reading region. At that boundary,
  native wheel and trackpad input chains to the inspector, which remains the
  primary vertical scroll owner.
- The inspector is the primary wide, resizable reading surface. It has no fixed
  desktop maximum and may grow until the center ledger reaches its 216 px compact
  locator contract.
- Pane adaptation follows the width of the pane that owns the content, using
  container queries where appropriate. Viewport breakpoints only choose the
  outer split, rail, drawer, or overlay mode.
- Selecting a request reads from cache synchronously when possible and schedules
  nearby request bodies for idle prefetch. In-flight reads are deduplicated and
  all loaded bodies remain bounded by the record LRU.
- Reading state belongs to the inspector and is restored per record and tab. A
  first visit within the same capture and tab inherits the previous request's
  position so repeated prompt prefixes stay anchored; revisits use the target
  request's own saved position. Stable long-message and tool-payload blocks
  inherit and restore their own local offsets by source identity. A shorter
  request may clamp the displayed offset but never overwrites the intended
  position for the next compatible request. Wrap, language, and full-value
  rerenders save both outer and local positions before replacing the view.
- JSON interaction follows DeepSeek Harness `JsonTree`: the top level is open,
  nested branches start closed, and only the disclosure control or field label
  toggles a branch. The deepest hovered row receives the highlight and copy
  affordance; the context menu copies the value, compact/pretty JSON, or path;
  Left/Right and Up/Down provide tree navigation with roving focus.
- Tool calls and tool results in Messages use that same JSON tree whenever their
  normalized input or content is an object or array. Anthropic `input/content`,
  OpenAI Chat `arguments/content`, Responses `action/output`, and Gemini
  `args/response` map to that semantic payload. Protocol ids remain available
  for call-result pairing and the Request/Response JSON tabs retain the complete
  wire envelope; primitive or malformed payloads fall back to lossless wrapped text.
- The sidebar is a compact session browser: a 280 px expanded list, a 56 px
  icon rail, and a drawer on small screens. Its panel toggle follows the
  DeepSeek interaction exactly: the resting brand glyph swaps to the panel
  glyph on hover/focus, a delayed fixed tooltip explains the action, and click
  performs a two-stage 300 ms transition. The footer does not repeat totals.
- Known Anthropic, OpenAI Chat/Responses, Gemini, and Codex blocks retain their
  native identifiers and useful fields. Every unknown role, content block,
  schema, and response shape falls back to complete, labelled JSON rather than
  disappearing or being guessed.

## Audit and resolution

The redesign addressed the following concrete problems in the previous UI.

| # | Previous problem | Resolution |
|---:|---|---|
| 1 | A global header consumed vertical space without helping trace inspection. | Removed it; session context now lives in a 54 px local header. |
| 2 | The centered live-connection pill dominated the header. | Moved connection state to a quiet footer dot and settings menu. |
| 3 | Brand, version, endpoint, and capture repeated the same context. | Kept one compact brand row and one capture control. |
| 4 | Five metric cards consumed an entire row. | Replaced them with a single text facts line. |
| 5 | The three-lane timeline duplicated the request list. | Removed it completely. |
| 6 | Timeline bars looked interactive but did not explain model behavior. | Model output and tool arguments now occupy each call row. |
| 7 | Rows led with the repeated user prompt instead of the model response. | Assistant output is the primary row text; prompt is only a fallback. |
| 8 | A `TOOL` label hid which tools ran. | Up to two aligned tool names and argument previews are visible inline, followed by a hidden-count marker. |
| 9 | Tool arguments were unavailable without reading raw JSON. | Every tool-call block always shows its complete compact argument JSON as one text value. |
| 10 | Tool calls and later results were merged into an invented interaction. | Calls and results stay only where they occur in the selected request; the UI never crosses request boundaries. |
| 11 | The default inspector view was transport metadata. | Messages is now the default view and represents only the selected request body. |
| 12 | System prompt, messages, and tool definitions were mixed together. | They have separate inspector tabs. |
| 13 | Tool definitions were flat names with no schemas. | Definitions use searchable disclosure rows with description and schema. |
| 14 | Closing the inspector cleared the selected request. | Selection remains; the inspector can be reopened in place. |
| 15 | Switching records or tabs reset inspector scroll. | Scroll is remembered per record and per tab. |
| 16 | The left panel permanently occupied 380 px. | It is 248 px, collapses to a 56 px rail, and becomes a mobile drawer. |
| 17 | Raw session ids and identity confidence were shown in the session list. | Internal ids moved to copy actions and raw data only. |
| 18 | Native selects looked different on every operating system. | Capture and model choices use keyboard-accessible custom menus. |
| 19 | Text-only Close and action buttons consumed inspector width. | Dense actions use the shared 28 px icon control. |
| 20 | Inspector tabs could wrap and break the header. | Tabs stay one line and scroll horizontally without a visible scrollbar. |
| 21 | Toolbar controls wrapped or overflowed on narrow screens. | Controls use min-width constraints, stable icon actions, and compact breakpoints. |
| 22 | The desktop three-column layout did not have a concession strategy. | Sidebar collapses first; the inspector clamps before the center pane. |
| 23 | Mobile reused squeezed desktop columns. | Sessions and inspector become separate full-height overlays. |
| 24 | Status treatment did not distinguish transport success from stream failure. | Every row keeps a compact status, while SSE/WS errors override a misleading HTTP `200`. |
| 25 | Repeated method and path strings drowned out activity. | Transport details moved to Request and Raw views. |
| 26 | Excessive rounded cards fragmented the page. | Primary surfaces are flat rows separated by hairlines. |
| 27 | Bright semantic colors appeared on routine metadata. | Color is reserved for selection, tool activity, and errors. |
| 28 | Raw payloads forced long horizontal scrolling. | JSON views use a typed, keyboard-accessible folding tree. |
| 29 | Large tool parameters could take over the inspector. | Tool JSON wraps to the available width and gains a bounded vertical scroller only when it is genuinely tall. |
| 30 | Every live append rebuilt all session summaries. | Session summaries and search text update incrementally. |
| 31 | Scrolling rebuilt virtual rows on every native scroll event. | Virtual-list updates are coalesced with `requestAnimationFrame`. |
| 32 | Static lazy records were decompressed again when revisited. | Loaded records now use an LRU cache. |
| 33 | Selecting a call preloaded later requests to guess tool-result pairs. | Cross-request pairing and its index were removed; selection fetches exactly one record. |
| 34 | Direct store mutation bypassed the event contract when closing details. | Panel state is UI state; trace selection remains store-owned. |
| 35 | The UI had no consistent icon language. | Controls use the attributed DeepSeek Harness icon set. |
| 36 | Auxiliary traffic and title generation looked like ordinary user turns. | Non-inference traffic is omitted from the LLM-call ledger; title generation keeps a quiet explicit activity title. |
| 37 | The readable view flattened messages and therefore changed request structure. | Every request message is one unnumbered role section in source-array order. |
| 38 | `tool_result` blocks disappeared from the readable view. | Tool calls and results remain inside their original message and content-block positions. |
| 39 | Large JSON was handled by truncating the entire document. | Top-level JSON stays available while individual object, array, and long-string branches expand lazily. |
| 40 | One long prompt or result could make the whole inspector unwieldy. | Long message and tool blocks use bounded scrolling with native wheel handoff; the System tab remains one continuous document. |
| 41 | The Messages view appended the current response and blurred the request boundary. | Messages now contains only source items from `request.body`; output remains in the center summary and Response JSON. |
| 42 | Message cards added synthetic indexes, boundary labels, and redundant metadata. | Cards now contain only a role badge, an optional protocol type, and original content blocks. |
| 43 | Tool definitions and tools called by the response shared one tab. | Tools contains request-mounted definitions only; response calls remain response activity. |
| 44 | Provider-specific schemas rendered inconsistently or not at all. | Anthropic `input_schema`, OpenAI parameter/custom formats, Gemini declarations, and Codex namespaces normalize into one recursive schema view. |
| 45 | JSON field names could wrap one character per line. | Keys are non-shrinking and non-wrapping; only values wrap. |
| 46 | Expanded JSON chevrons did not reflect state. | `aria-expanded` and the icon rotation now change together. |
| 47 | Expanded JSON inserted large blank gaps between fields. | Structural rows use normal flow while each actual JSON row retains code whitespace. |
| 48 | Static exports stripped streams before deriving metadata. | Metadata is derived from the original records, then event payloads are stripped only from the embedded copy. |
| 49 | Cached input tokens were omitted from call and session totals. | Input totals include uncached, cache-read, and cache-creation tokens. |
| 50 | The compact tablet layout inherited the expanded-sidebar width when sizing the inspector. | Inspector track sizing now resolves inside the app shell after the sidebar concession, preserving a usable reading pane from 800 px upward. |
| 51 | Message cards repeated borders, radius, padding, and gaps around every source item. | Messages now form one flat hairline-separated stream with a shared 12 px content edge. |
| 52 | Tool parameters used the interactive JSON tree, which over-indented values and broke long commands character by character. | The readable view now uses one compact JSON string with no inserted formatting lines; CSS wraps it to the available width without removing data. |
| 53 | Empty signed thinking blocks appeared as misleading expandable panels. | Empty thinking payloads are omitted from the readable preview; visible thinking is a left-rule text block in source position. |
| 54 | Fixed 82 px request rows left large empty bands around short errors. | Desktop rows now use a 44 px two-line rhythm; phones use 54 px for touch comfort without turning short failures into empty bands. |
| 55 | New provider content-block types could fall into an empty or lossy preview. | Known Anthropic, OpenAI Chat/Responses, Gemini, and Codex shapes are normalized; every unknown object uses a type-labelled, all-field JSON fallback. |
| 56 | Tool cards required repetitive expansion and accumulated arrows, borders, hover states, and duplicated summaries. | Message tools are static, always-visible blocks with only the tool name, format label, and raw payload on a quiet background. |
| 57 | The mobile session drawer could appear partially on first paint while its transition initialized. | Drawer transitions stay disabled until the viewer has completed two paint frames. |
| 58 | JavaScript switched to compact mode at a different width than the CSS layout. | Both layers now use the same 1120 px desktop concession point. |
| 59 | Between 800 and 899 px, a split inspector left too little room for either pane. | The session rail remains available, but the selected request opens as a full-width reading surface. |
| 60 | The footer settings menu could be clipped by the sidebar edge or viewport bottom. | It now opens upward and right-aligned, with viewport-bounded height. |
| 61 | Capture, model, and disclosure chevrons did not consistently communicate open state. | Each icon has one transform owner and rotates only from the corresponding expanded state. |
| 62 | Inspector tabs overflowed at tablet and phone widths, sometimes widening the page. | The overlay inspector owns the full viewport below 900 px; phone tabs use concise labels and fit at 320 px. |
| 63 | Inspector tabs only supported pointer selection. | Tabs now expose a roving tab stop with Arrow, Home, and End navigation. |
| 64 | Several phone controls were only 28–32 px tall. | Primary icon, menu, search, tab, and disclosure targets now meet a 36–40 px compact touch rhythm. |
| 65 | An off-screen mobile sidebar remained keyboard and screen-reader reachable. | Hidden surfaces now synchronize `inert`, `aria-hidden`, and trigger `aria-expanded`. |
| 66 | A zero-width closed inspector remained focusable. | Closing the inspector makes the whole surface inert and restores focus to the reopen control on overlays. |
| 67 | Full-screen detail views left the covered call list interactive. | Overlay state makes underlying call and session surfaces inert until the inspector closes. |
| 68 | Escape did not reliably dismiss the active layer. | Escape closes menus first, then the inspector or mobile drawer, restoring focus to its trigger. |
| 69 | Historical tool results were labelled generically even when their call id identified the tool. | Result blocks resolve their name from earlier call ids while preserving source order. |
| 70 | A failed tool result painted an entire long payload red. | Error emphasis is limited to a two-pixel rule and compact heading; payload text remains readable. |
| 71 | Reaching the end of a nested message scroller trapped wheel input. | Nested blocks use native scroll chaining so continued wheel input moves the inspector. |
| 72 | Unknown future protocol blocks used unwrapped preformatted text. | Their complete compact JSON now wraps within the reading pane without dropping fields. |
| 73 | Expanded long JSON strings sized themselves from the viewport rather than the inspector. | Long values now derive width from the actual JSON pane. |
| 74 | JSON previews could exceed a resized inspector even while the document itself fit. | Every preview and expanded branch is constrained by its containing flex track. |
| 75 | Tool descriptions repeated in both header and body and became a narrow column when expanded. | Collapsed rows keep a one-line preview; expanded rows show the full description once at body width above the schema. |
| 76 | Long enum and default values disappeared behind ellipses. | Schema metadata wraps on its own line while field names remain intact. |
| 77 | Deeply nested schemas accumulated boxes, indentation, and visual noise. | Nested objects use one quiet guide rule and reduced indentation. |
| 78 | Request/response JSON reused a message-search placeholder. | JSON and mounted-tool views now have view-specific, count-aware filter copy. |
| 79 | Title-generation calls exposed raw `{"title": ...}` as the row summary. | A safe parser extracts the readable title while retaining the raw value for inspection. |
| 80 | Dark mode kept a light browser theme color. | The theme-color meta value now follows the active surface theme. |
| 81 | Small metadata colors were too pale against light surfaces. | Caption, status, tool, and role tokens were rebalanced for readable contrast without bright badges. |
| 82 | Stream failures delivered over HTTP 200 looked like empty successful calls. | Rows, headers, and Messages show the stream error alongside the transport status. |
| 83 | The mounted-tools filter gave no sense of catalog size. | Its placeholder reports the number of definitions in the selected request. |
| 84 | Call rows did not expose meaningful selection or spoken context. | Rows now use `aria-current` and a composed label containing call, output, model, duration, and status. |
| 85 | The 320 px English tab set hid Response and Raw beyond an invisible horizontal edge. | Narrow screens substitute concise Request/Response labels; all six tabs fit without document or tab overflow. |
| 86 | An expanded long tool description delayed the first schema field by several screens on mobile. | The full description uses the body width and the header stays one compact line, bringing parameters into view sooner. |
| 87 | Failed requests were marked with a full-height red rail that visually cut the ledger into stripes. | Error state now lives in the factual error text and status cell; the row itself stays flat. |
| 88 | Selected requests added a second full-height blue rail on top of error state. | Selection uses one quiet active-row fill with top and bottom hairlines. |
| 89 | The selected session repeated the same blue-rail treatment in the sidebar. | Session selection now uses the same neutral fill language as the request ledger. |
| 90 | A short upstream error occupied the same 64 px as a multi-tool response. | The 44 px row rhythm keeps failures compact while retaining two lines for successful activity. |
| 91 | Mobile expanded every row to 74 px and showed very few calls per screen. | The phone rhythm is 54 px and still preserves a comfortable target and two readable lines. |
| 92 | Tool names lived in a separate 48–88 px grid column, creating a conspicuous gap before their arguments. | Name and argument now form one continuous inline tool phrase. |
| 93 | Center-row tools had no semantic icon and relied on orange text alone. | Each call uses the attributed DeepSeek Harness 12 px wrench glyph in a fixed optical box. |
| 94 | Tool glyph, name, and argument baselines drifted independently. | They share an 18 px flex line with a five-pixel glyph/name rhythm. |
| 95 | Two tools were stacked as unrelated rows with no model-call grouping. | Up to two calls share one grouped activity line with equal flexible tracks and a `+N` remainder. |
| 96 | A tool-only model response added the synthetic line “Empty model response.” | Tool calls stand on their own when the model emitted no prose. |
| 97 | Error rows reserved an unused secondary line. | A factual error reason is vertically centered and no placeholder description is invented. |
| 98 | Four-percent row separators disappeared on bright displays. | Ledger boundaries use the stronger level-two hairline while surrounding chrome remains level one. |
| 99 | The call index was left-padded while its header was centered. | Header and values now share the same centered 42 px column. |
| 100 | Model metadata was visually centered despite being read as a text column. | Model and token lines are left-aligned on one consistent edge. |
| 101 | “TIME / STATUS” was permanently ellipsized in its 66 px column. | The heading is the concise “TIMING”; duration and status remain explicit in every row. |
| 102 | Stream errors over HTTP 200 used a wide generic “ERROR” label while HTTP failures used numbers. | The status cell uses compact `ERR` for stream errors and preserves real HTTP error codes. |
| 103 | The session header and filter toolbar both repeated the same total call count. | The toolbar owns the filter-sensitive count; the header keeps duration, tokens, and errors. |
| 104 | The session browser was wider than needed for a single title and one metadata line. | Its expanded track is 248 px and its session rows use a denser 60 px rhythm. |
| 105 | The capture control was taller than adjacent search and toolbar controls. | Its height is 40 px and aligns to the sidebar’s compact vertical cadence. |
| 106 | The request toolbar consumed 44 px despite containing 30 px controls. | The toolbar is 40 px with balanced five-pixel vertical padding. |
| 107 | The column header used a 30 px band that felt disconnected from the denser data rows. | It now uses the same 28 px utility rhythm as DeepSeek Harness. |
| 108 | Icon host spans inherited text line-height and produced subtle one-pixel vertical drift. | Shared icon hosts are inline-flex, line-height zero, and centered in their control boxes. |
| 109 | Inspector actions used circular hover chrome even though they sit in a rectangular header. | Inspector action seats use the source implementation’s 6 px corner geometry. |
| 110 | Tool previews used an 11 px code face that was noticeably lighter than adjacent response text. | Tool activity is 12 px while indexes and metadata remain deliberately smaller. |
| 111 | Failed tool results repeated the disliked left-rule error treatment inside messages. | Their heading remains red while the block receives only a four-percent error wash. |
| 112 | Width-only breakpoint transitions forcibly replaced every virtual row, briefly detaching the active target. | Resize and sidebar transitions now retain mounted rows unless the visible range or row height actually changes. |
| 113 | Localized protocol vocabulary such as “System prompt” obscured the source request concepts. | Protocol roles, tabs, activity kinds, token labels, and JSON-oriented terms stay in English in every locale. |
| 114 | A historical response failure used a full-width red alert and made the viewer itself look broken. | Messages uses a compact neutral response-status strip; only the HTTP/SSE status receives restrained error color. |
| 115 | Failed rows collapsed the entire captured request into the word “Overloaded.” | The first line retains the latest request or tool context and a second `RESPONSE` line states the upstream failure. |
| 116 | Rows with prose, tools, or both changed structure unpredictably. | Every call uses one prose/status line followed by one direct tool-call line when tools are present. |
| 117 | Adjacent user, assistant, context, and tool-result messages visually blended together. | Stronger hairlines and subtle role-tinted surfaces separate source messages without turning them into floating cards. |
| 118 | Selecting an unread call always reset the active inspector tab to the top. | New calls inherit the current tab's reading position; revisiting a call restores that call's own saved position. |
| 119 | SSE failures transported over HTTP 200 were presented as contradictory “HTTP 200 · Error” facts. | The viewer reports `SSE error` for stream failures and preserves real HTTP codes such as `HTTP 529`. |
| 120 | `MESSAGE`, `TOOLS`, `REQUEST`, and `RESPONSE` labels made one captured LLM call look like several timeline events. | Every ledger row is now one undivided model-call summary with no synthetic event taxonomy. |
| 121 | Full tool commands and paths competed with model output in the center ledger. | Each direct tool call keeps its exact name plus one bounded high-signal hint; complete arguments remain in the selected request's detail view. |
| 122 | Long response previews and session titles filled nearly the entire reading width. | Ledger prose is bounded to a 112-character preview and the session title to a restrained 58ch measure, with full content retained in details and tooltips. |
| 123 | Short output left tool activity stranded at the far edge of the column. | Output and tools use two adjacent compact lines, so both begin on the same reading edge without synthetic labels. |
| 124 | A narrow viewport could clip a second tool after its icon, leaving a meaningless partial item. | Mobile keeps the first complete direct tool call and replaces all remaining calls with an exact `+N` count. |
| 125 | Grouping repeated tool names hid the difference between separate `Bash`, `Read`, or agent operations. | Desktop renders the first two calls independently with concise descriptions, command purposes, or path tails. |
| 126 | Expanded mounted-tool schemas spent too much width on a second indentation level and too much height between fields. | The body now returns to the inspector content edge, with tighter description, heading, and parameter rhythm. |
| 127 | JSON branches only felt reliably clickable on the tiny chevron. | The entire expandable row toggles the branch while the chevron remains keyboard focusable and text selection is preserved. |
| 128 | Token-count and other auxiliary requests appeared as fake model calls and inflated session totals. | Session indexes, call totals, and the ledger now include only captured LLM inference requests. |
| 129 | Duration alone did not reveal when a model request occurred, making correlation with terminal events difficult. | Every call begins with a stable session ordinal and local `HH:mm:ss`; phones retain `HH:mm`, while duration stays in the timing column. |
| 130 | Stacking call number over timestamp made a minor locator look like a dense two-row control. | Number and time now share one quiet baseline (`01 · 16:08:38`), shortened to minute precision on phones. |
| 131 | The compact session facts line still repeated client, duration, and aggregate token totals without helping inspect a call. | Removed the aggregate facts line; session identity and the filter-sensitive call count live only where they are acted on. This supersedes resolutions 4 and 103. |
| 132 | The 44/54 px rows became visually inconsistent when prose and tool calls used different templates. | Every desktop call row uses a stable 56 px, two-rail activity frame; the touch layout uses 62 px without changing the information order. This supersedes resolutions 54, 90, and 91. |
| 133 | Call ordinal and timestamp still had equal weight even though time is the useful correlation signal. | Local time leads the row and the ordinal is a tiny `#N` locator on the same quiet line; the ordinal disappears before the timestamp is shortened. This supersedes resolutions 99, 129, and 130. |
| 134 | Repeating `200` on every successful request spent scarce width on the normal case. | Routine 2xx status has no rendered badge; only an exceptional HTTP or stream status appears. This supersedes resolution 24. |
| 135 | Model and input/output usage looked like three equivalent labels and became cramped. | Model identity is primary; usage is one compact `IN … · OUT …` caption with output distinguished typographically, not another badge. |
| 136 | Prose-plus-tools, tool-only, and error rows each shifted their vertical rhythm. | They now share one response rail and one tool rail; an absent rail stays structurally predictable without synthetic “empty response” copy. This supersedes resolutions 96, 97, 115, and 116 where their templates conflict. |
| 137 | Message previews hid tool-call ids and made tool results look unrelated or globally erroneous. | Calls and results expose their original ids and matching relation in source order; result errors use neutral content chrome and restrained metadata. This supersedes resolutions 70 and 111. |
| 138 | Bounded message, result, and string scrollers competed with the inspector and intercepted ordinary wheel gestures. | Each inspector tab owns one vertical scroller; blocks remain in normal flow and only code-like horizontal overflow is local. This supersedes resolutions 29, 40, and 71. |
| 139 | The inspector stopped growing while the less-important ledger retained excess width. | Its splitter supports a much wider persisted reading width and a focus-size toggle, while the ledger may contract to a 280 px locator surface. |
| 140 | Viewport-only rules hid or wrapped columns based on window width rather than the actual center-pane width. | Ledger concessions use container queries; viewport breakpoints are reserved for outer rail, drawer, split, and overlay modes. |
| 141 | Loading exactly one selected record caused a visible pause on every neighboring selection. | The selected body resolves from cache immediately when present; adjacent bodies prefetch during idle time and duplicate in-flight reads coalesce. This supersedes resolution 33 without reintroducing cross-request pairing. |
| 142 | Making the whole JSON row toggle was easy to trigger accidentally and differed from the source interaction. | Only the disclosure control and field label toggle; deepest-row hover, edge copy, context-copy variants, roving tree focus, and arrow navigation follow DeepSeek Harness. This supersedes resolution 127. |
| 143 | The tablet sidebar visually stayed at rail width after the user reopened it. | Collapsed rail rules are state-scoped; an open sidebar always receives its full compact width, and small screens use an inert-aware drawer. |
| 144 | Provider normalization could still drop identity fields that are essential for protocol debugging. | Readable views retain raw role/type/order, call ids, result ids, and all unknown block fields; normalization supplies presentation only, never a lossy replacement. |
| 145 | JSON styling resembled the reference but lacked its predictable copy and keyboard semantics. | `JsonTree` behavior is treated as a compatibility contract and attributed under the DeepSeek Harness MIT license, not merely as visual inspiration. |
| 146 | Separate response and tool rails made text-only, tool-only, and mixed calls look like three unrelated row components and left visible empty bands. | Every call now has one 20 px activity rail inside a 52 px desktop row. Tool calls lead, prose follows on the same baseline, and absent content produces no empty rail. This supersedes resolutions 123, 132, and 136. |
| 147 | The collapsed sidebar behaved like a generic dark icon strip and did not match the reference's hover affordance or motion. | The sidebar now uses the source's 280/56 px geometry, 36 px control seats, brand-to-panel hover swap, 500 ms fixed tooltip, neutral selection, and two-stage 300 ms collapse/expand contract. |
| 148 | A single shared activity rail forced mixed and multi-tool calls into one crowded baseline and made tool density inconsistent. | Mixed and tool-bearing rows now use a 40 px activity area with two vertically stacked 20 px tool lines. Model prose is a 10.5 px tertiary side note on wide panes; at or below 620 px, mixed rows keep the primary tool plus inline `+N` on the first line and the prose on the second. This supersedes resolution 146 and the single-rail portions of resolutions 123, 132, and 136. |
| 149 | Forcing every message to grow inside the inspector's single scroller made long source messages dominate the tab and obscured role boundaries. | Every source message is a fixed 168 px card with a 30 px role header and an overflowing body; native overscroll at the body boundary chains to the inspector. This supersedes the single-scroll portions of resolutions 40, 71, and 138. |
| 150 | Restoring only the inspector offset lost a developer's place inside long message bodies after switching requests or tabs. | The inspector restores per record and tab, and every message body restores per record, tab, and source-message index, with each value clamped after remount. This supersedes resolution 118 where it prescribed an inherited first-visit position. |
| 151 | Wrap, language, and full-value actions rebuilt the active view before persisting its live scroll offsets. | All same-view rerenders now pass through one preserving path that cancels stale restoration, saves outer and message-body positions, and then remounts. |
| 152 | The closed mobile drawer inherited the 56 px desktop-rail width, then jumped to full width as it opened. | Below 800 px the drawer keeps its full bounded width in both states and animates only its off-canvas transform. |
| 153 | Persistent prose beside stacked tools made mixed rows wider, less consistent, and visually noisier than tool-only rows. | Mixed rows show the same two vertical tool lines as every tool-bearing request; complete assistant prose moves to the shared fixed hover/focus preview. Exact `+N` remains on the second line. This supersedes the prose portions of resolutions 146 and 148. |
| 154 | Treating a whole source message as one fixed card either trapped several raw blocks together or forced short blocks into large empty frames. | The rendering unit is now one original content block. Blocks retain role, type, source path, ids, and order; height is natural, and only extreme payloads scroll locally with native boundary handoff. This supersedes resolutions 149-151 and narrows the exception in resolution 138. |
| 155 | Expanded tool definitions mixed an orange icon, blue type, orange required state, grey slab, and nested parameter card at equal visual weight. | Expanded definitions use one neutral white surface, bounded pre-line prose, quiet catalogue icons, compact schema rows, low-contrast types, and a neutral `Required` state. |
| 156 | The inspector header, role strips, and action chrome still resembled a generic debug panel rather than the source reference. | The inspector now follows the source's 42 px one-line request header, dot/name/location hierarchy, 34 px tabs, restrained hover-only chrome, and compact role chips. The application mark uses the shared solid icon asset. |
| 157 | Repeating a role/type header and two horizontal rules around every raw tool block made one source message look like a stack of unrelated table rows. | One request-message group owns the role/type summary, while every original content block remains an independent article inside it. Six-pixel whitespace and quiet surfaces replace card and payload separator lines. |
| 158 | Role colors, orange tool glyphs, format labels, ids, and payload text competed at the same visual weight. | Messages use a tonal hierarchy: role and tool names are primary, payloads secondary, and glyphs, phases, ids, formats, and type summaries are captions. |
| 159 | Removing nearly all color made adjacent roles and the mounted-tool catalogue difficult to scan. | The DeepSeek role palette now colors compact role chips and a restrained corresponding message surface; tool/function payload blocks use the same quiet amber family across call and result roles. Mounted-tool names use the DeepSeek business blue while glyphs, descriptions, schemas, ids, and payloads stay neutral. |
| 160 | Messages stopped at the request body, forcing developers to switch tabs to discover what that model call returned. | Protocol-specific response adapters append model output after the final request group in the same ordered reading flow. |
| 161 | Rendering the full response envelope would mix HTTP metadata, token accounting, and SSE transport frames with actual model content. | Messages extracts only response text, reasoning, and tool-call blocks for Anthropic Messages, OpenAI Chat, OpenAI Responses, and Gemini; transport and usage data remain in their dedicated views. |
| 162 | A strict known-protocol parser could silently hide future providers, primitive bodies, and useful upstream error payloads. | Unknown successful and failed readable bodies use explicit `response_fallback` or `response_error` blocks without dropping fields or primitive values. |
| 163 | Blue user cards, green assistant cards, purple context cards, and amber tool cards made the Messages view look like four unrelated products. | All roles now share one neutral card and header-band system; role text and source order carry identity without a hue per role. This supersedes resolutions 117 and 159. |
| 164 | Solid uppercase role pills competed with payload content and made a raw protocol inspector resemble a status dashboard. | Role names use restrained title-case text inside a compact shared header; `Output` is a small blue source marker on the same line. |
| 165 | Orange ledger tools, blue mounted tools, amber message payloads, and colored role surfaces created an unnecessary semantic palette. | Tool names and glyphs now use the product's single blue accent across ledger, Messages, and definitions; ids, phases, formats, schemas, and payloads remain neutral. This supersedes the color portions of resolutions 93, 155, 158, and 159. |
| 166 | The message renderer retained dead JSON-folding methods, unused styling classes, duplicated tag branches, obsolete role tokens, and tests that froze incidental colors and pixel gaps. | Removed confirmed dead JS/CSS and legacy classes; tests now protect protocol order, accessibility, overflow, and scroll ownership instead of a discarded palette. |
| 167 | Tool calls and results were reduced to a hand-picked argument or output summary, which hid provider fields and made relationships difficult to verify. | Object and array payloads now render their complete original value through the shared `JsonTree`; ids, arguments, results, provider-specific fields, and source order remain intact. Primitive or malformed values use a lossless wrapped-text fallback. |
| 168 | A rigid TIME / ACTIVITY / MODEL / DURATION table left useful width unused and made narrow ledgers show clipped tool prose instead of actionable metadata. | Call rows are now adaptive flat rows. Wide rows place a compact metadata rail above assistant prose and direct tool calls; no permanent column header is needed. |
| 169 | Narrow center panes tried to preserve truncated output and tool hints even though the inspector is the primary reading surface. | At or below 620 px of ledger width, activity is hidden and every row keeps the stable locator, model, input/output usage, duration, and exceptional status. The complete hidden activity remains in the accessible label. At 420 px the row contracts again without horizontal overflow. |
| 170 | A single neutral accent made request direction, model output, and tool payloads blend together, while earlier multi-hue cards made the inspector noisy. | Groups keep neutral borders and bodies; request headers use restrained DeepSeek blue, output headers use restrained green, and tools use one amber family. JSON keys and punctuation remain neutral, strings use amber, and numeric or boolean values use blue. This supersedes the color portions of resolutions 163-165. |
| 171 | A right-aligned metadata column consumed a fixed slice of every call row even when activity needed that width. | The metadata rail now spans the row above activity, groups model and usage together, and keeps duration at the opposite edge. Activity receives the full lower rail, improving both prose and multi-tool density. |
| 172 | Tool-family pictograms made a dense catalogue look like a mixed icon gallery even though names and descriptions already identified each tool. | The ledger, Messages, and mounted-tool catalogue use one quiet wrench glyph. Raw tool names and descriptions carry semantics consistently across Claude Code, Codex, OpenAI Responses, Gemini CLI, and MCP without a second visual taxonomy. |
| 173 | Long source blocks below the old extreme threshold could still grow beyond 1,500 px and dominate the inspector. | Content over 3,200 characters or 40 line breaks receives a local vertical reading region bounded at `clamp(260px, 44vh, 440px)` with native boundary handoff. Short content remains in the inspector's single scroll flow. |
| 174 | Components still owned local colors, so the same tool, role, error, and selected state changed meaning between the ledger, Messages, Tools, and JSON. | The theme is now the only color authority. Components consume `surface`, `border`, `text`, `brand`, `role`, `status`, and `syntax` tokens; legacy names are compatibility aliases rather than a second palette. This supersedes the competing palette contracts in resolutions 159, 163, 165, and 170. |
| 175 | Treating each inference as a rounded card introduced nested slabs, excessive padding, and a false dashboard hierarchy. | The center is one continuous ActionList-style trace surface. Rows have no radius, shadow, margin, or four-sided outline; hover is neutral and selection is a soft blue wash with a two-pixel inset marker. |
| 176 | Metadata appeared before the model's actual work, making repeated model and Token facts look primary. | Wide rows lead with assistant output and vertically stacked tool calls. Model, input/output usage, cache facts, tool count, special request kind, and duration form a quiet second rail. |
| 177 | Viewport breakpoints and container-query visibility could disagree, leaving tall empty rows when the inspector narrowed the center pane. | A `ResizeObserver` assigns wide, medium, or compact density from the call-panel's measured width. Virtual row height and CSS visibility now change from the same container contract. |
| 178 | Aggregate Token counts hid the useful distinction between fresh input and prompt-cache traffic, while routine HTTP 200 badges consumed attention. | The ledger keeps compact `IN`/`OUT`, shows cache share only when relevant, and exposes fresh/cache-read/cache-write/output detail on hover or focus. Only overloaded, rate-limited, timeout, and failed requests receive status badges. |
| 179 | Tool-count affordances were either a contextless `+N` or absent from compact metadata. | Wide metadata shows the shared wrench plus an explicit tool count; medium and compact states retain the glyph/count while omitting the label. Complete tool names, arguments, and output stay in the row preview and inspector. |
| 180 | Sidebar state could report “settled” on the same frame as the 300 ms grid transition ended, so an immediate hover occasionally measured a partially interpolated center pane. | Transition phase cleanup includes a 40 ms post-motion settle window. The visible motion remains 300 ms, while hover, focus, list measurement, and follow-up menus begin only after geometry is stable. |
| 181 | Tool-call and tool-result cards repeated the complete provider wrapper, so `type`, ids, names, status, and transport fields competed with the actual arguments and output. | Messages renders only the normalized tool input or result content. Pairing ids remain in structural metadata, and the complete provider object remains available in the raw JSON views. |
| 182 | Appended model output looked like another request-history message and made the exchange boundary ambiguous. | A single compact `RESPONSE` divider now separates request history from all normalized output groups. |
| 183 | Concurrent tools created a role card around several individually bordered tool cards, producing a three-layer frame hierarchy. | One role message owns the outer surface. Tool names and phases sit directly on that surface, and only each readable input/content payload receives a quiet inset panel. |
| 184 | Realizing role-tinted groups through `content-visibility` could trigger browser scroll anchoring after a saved inspector position had already been restored. | The inspector owns programmatic restoration and disables native overflow anchoring, so switching calls returns to the exact saved offset instead of jumping toward the end. |
| 185 | The 78/68/60 px adaptive rows still left excessive vertical whitespace, and mixed prose/tool rows did not scan like tool-only rows. | Wide, medium, and compact rows now use 64/58/52 px. Tool-bearing rows lead with up to two direct tool calls; secondary prose remains available in the shared hover/focus preview. |
| 186 | A fixed 1120 px inspector ceiling and 280 px center floor prevented the primary reading surface from using available width. | The inspector has no fixed desktop ceiling and may grow until the center reaches a 216 px locator view. Pointer, keyboard, and double-click resizing share the same limits. |
| 187 | An unread call always started at the top even when adjacent calls repeated the same long prompt prefix. | A first visit inherits the current session-and-tab reading offset; revisiting a call restores that call's exact offset. This supersedes the first-visit reset portion of resolution 150. |
| 188 | Character and line thresholds could cap a long `Read` result while allowing a similarly tall `Bash` result to dominate the inspector. | Every tool result shares the same `clamp(180px, 24vh, 240px)` maximum. Short results keep their natural height; only overflowing content becomes a local scroller with native boundary handoff. Tool-call inputs use the same cap when classified as long. This supersedes resolution 173 for tool results. |

## Frontend boundaries

- `icons.js` owns the shared icon library and icon hydration.
- `core.js` owns events, translations, formatting, and session aggregation.
- `data.js` owns static/live sources, indexes, filters, and selection loading.
- `components.js` owns menus, virtualization, ordered protocol previews, the
  lazy JSON tree, and the inspector.
- `app.js` owns layout state and turns store events into rendered views.

The static and live viewers use the same assets. `viewer.py` assembles them
into one self-contained document for portable exports.

## Responsive contract

- Above 1120 px, the compact sidebar is expanded unless the user collapsed it.
- Between 900 px and 1120 px, the sidebar becomes a 56 px rail while the
  inspector remains split from the call list.
- Between 800 px and 899 px, the rail remains and the inspector becomes a
  full-screen reading surface.
- Below 800 px, the sidebar is a drawer and the inspector remains a full-screen
  reading surface.
- In split mode, the inspector defaults to 56% of the viewport and has no fixed
  desktop ceiling. It can grow until the center reaches its 216 px locator
  minimum. The resize handle supports pointer, keyboard, and reset/focus-size
  actions.
- Call rows adapt to the ledger container, not the viewport. Above 720 px, wide
  rows place either text output or up to two vertically stacked tool calls above
  their quiet metadata rail. Tool-bearing prose remains in the preview tooltip.
- Between 521 px and 720 px, medium rows keep the activity rail but remove long
  arguments and cache labels while retaining both visible tool names.
- At or below 520 px, activity yields completely. Model, input and output usage,
  duration, exceptional status, time, and semantic tool count remain visible;
  the accessible row label and hover/focus preview retain the complete activity.
- At or below 380 px, the locator and metadata insets contract again and the
  timestamp shortens without widening the document.

## Performance contract

- Center and session lists remain virtualized with fixed-height rows. Call rows
  use 64 px in wide mode, 58 px in medium mode, and 52 px in compact mode; the
  active height is derived from the same call-panel width that drives CSS.
- Record bodies stay lazy. Selection loads the target while idle work prefetches
  the two nearest requests in each direction; the record LRU bounds memory.
- Concurrent demand for the same body shares one in-flight request, and a cached
  body is rendered synchronously instead of flashing a loading state.
- Search uses precomputed lowercase metadata rather than serializing records.
- Live appends update one session accumulator and render at most once per frame.
- Structured JSON creates DOM only for open branches. Nested branches and long
  primitive strings remain folded until explicitly opened; opening content does
  not introduce a second vertical scroller.
- Inspector scroll state is stored per record and tab, with a session-and-tab
  fallback for first visits, restored after the final content height is known,
  and clamped after remount or layout change. Every tool result has the same
  240 px upper bound, while short results stay at natural height; long message
  and tool-call payloads use bounded local scrollers only when needed.
