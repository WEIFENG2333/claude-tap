"""Browser coverage for nested viewer scrolling."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from claude_tap.viewer import render_html

playwright = pytest.importorskip("playwright.sync_api")


def _render_long_xml_viewer(tmp_path: Path) -> Path:
    xml = "<root>\n" + "\n".join(f'  <item id="{index}">value {index}</item>' for index in range(240)) + "\n</root>"
    medium_text = "\n".join(f"Medium message line {index}" for index in range(28))
    ordinary_tool_payload = "\n".join(f"ordinary {index} " + "x" * 72 for index in range(24))
    extreme_tool_payload = "\n".join(f"extreme {index} " + "x" * 112 for index in range(80))
    record = {
        "timestamp": "2026-07-31T12:00:00+08:00",
        "request_id": "req_scroll_chain",
        "turn": 1,
        "duration_ms": 42,
        "request": {
            "method": "POST",
            "path": "/v1/messages",
            "headers": {},
            "body": {
                "model": "claude-test",
                "system": "\n".join(f"System prompt line {index}" for index in range(80)),
                "messages": [
                    {"role": "user", "content": xml},
                    {"role": "user", "content": medium_text},
                    {
                        "role": "assistant",
                        "content": [
                            {
                                "type": "tool_use",
                                "id": "toolu_scroll_ordinary",
                                "name": "Bash",
                                "input": {"command": "printf ordinary", "payload": ordinary_tool_payload},
                            },
                            {
                                "type": "tool_use",
                                "id": "toolu_scroll_extreme",
                                "name": "Bash",
                                "input": {"command": "printf extreme", "payload": extreme_tool_payload},
                            },
                        ],
                    },
                    *[{"role": "user", "content": f"Short message {index}"} for index in range(28)],
                ],
            },
        },
        "response": {
            "status": 200,
            "headers": {},
            "body": {
                "content": [
                    {
                        "type": "text",
                        "text": "\n".join(f"Response line {index}" for index in range(160)),
                    }
                ]
            },
        },
    }
    trace = tmp_path / "trace_scroll_chain.jsonl"
    auxiliary = {
        "timestamp": "2026-07-31T12:00:01+08:00",
        "request_id": "req_count_tokens",
        "turn": 2,
        "duration_ms": 4,
        "request": {
            "method": "POST",
            "path": "/v1/messages/count_tokens?beta=true",
            "headers": {},
            "body": {"model": "claude-test", "messages": [{"role": "user", "content": "count only"}]},
        },
        "response": {"status": 200, "headers": {}, "body": {"input_tokens": 3}},
    }
    trace.write_text("\n".join((json.dumps(record), json.dumps(auxiliary), "")), encoding="utf-8")
    output = trace.with_suffix(".html")
    assert render_html(trace, output)
    return output


def _wait_for_sidebar_settle(page, state: str, width: int) -> None:
    """Wait for the semantic state, animation phase, and rendered track to agree."""
    page.wait_for_function(
        """({ state, width }) => {
          const workspace = document.querySelector("#workspace");
          const sidebar = document.querySelector("#session-panel");
          if (!workspace || !sidebar) return false;
          return workspace.dataset.sidebar === state
            && !workspace.hasAttribute("data-sidebar-phase")
            && Math.abs(sidebar.getBoundingClientRect().width - width) <= 1;
        }""",
        arg={"state": state, "width": width},
    )


def _watch_sidebar_phases(page) -> None:
    page.evaluate(
        """() => {
          window.__claudeTapSidebarPhaseObserver?.disconnect();
          window.__claudeTapSidebarPhases = [];
          const workspace = document.querySelector("#workspace");
          window.__claudeTapSidebarPhaseObserver = new MutationObserver(records => {
            if (!records.some(record => record.attributeName === "data-sidebar-phase")) return;
            const phase = workspace.getAttribute("data-sidebar-phase");
            const phases = window.__claudeTapSidebarPhases;
            if (!phases.length || phases[phases.length - 1] !== phase) phases.push(phase);
          });
          window.__claudeTapSidebarPhaseObserver.observe(workspace, { attributes: true });
        }"""
    )


def _sidebar_phases(page) -> list[str | None]:
    return page.evaluate("window.__claudeTapSidebarPhases")


def _assert_message_headers_accessible_and_contained(page) -> None:
    headers = page.locator(".request-message-header")
    assert headers.count() == page.locator(".request-message-group").count()
    assert headers.evaluate_all(
        """elements => elements.every(header => {
          const group = header.closest('.request-message-group');
          const role = header.querySelector('.message-role');
          const type = header.querySelector('.message-type');
          if (!group?.getAttribute('aria-label')?.trim() || !role?.textContent?.trim() || !type?.textContent?.trim()) {
            return false;
          }
          const bounds = header.getBoundingClientRect();
          const roleBounds = role.getBoundingClientRect();
          const typeBounds = type.getBoundingClientRect();
          const tolerance = 1;
          const inside = rect => rect.width > 0
            && rect.height > 0
            && rect.left >= bounds.left - tolerance
            && rect.right <= bounds.right + tolerance
            && rect.top >= bounds.top - tolerance
            && rect.bottom <= bounds.bottom + tolerance;
          const overlapWidth = Math.min(roleBounds.right, typeBounds.right) - Math.max(roleBounds.left, typeBounds.left);
          const overlapHeight = Math.min(roleBounds.bottom, typeBounds.bottom) - Math.max(roleBounds.top, typeBounds.top);
          return inside(roleBounds)
            && inside(typeBounds)
            && !(overlapWidth > tolerance && overlapHeight > tolerance)
            && header.scrollWidth <= header.clientWidth + tolerance;
        })"""
    )


def _assert_message_groups_do_not_overlap(page) -> None:
    assert page.locator(".request-message-group").evaluate_all(
        """elements => {
          const boxes = elements.map(element => element.getBoundingClientRect());
          return boxes.every((box, index) => index === 0 || box.top >= boxes[index - 1].bottom - 1);
        }"""
    )


def _assert_only_extreme_payloads_own_nested_vertical_scroll(page) -> None:
    inspector = page.locator("#inspector-body")
    assert inspector.evaluate("element => ['auto', 'scroll'].includes(getComputedStyle(element).overflowY)")
    assert page.locator(
        ".message-list, .request-message-group, .request-message-blocks, .request-message, .request-message-body"
    ).evaluate_all(
        """elements => elements.every(element => {
          const overflow = getComputedStyle(element).overflowY;
          return !['auto', 'scroll'].includes(overflow) || element.scrollHeight <= element.clientHeight + 1;
        })"""
    )
    assert page.locator(".conversation-flow *").evaluate_all(
        """elements => elements.every(element => {
          const overflow = getComputedStyle(element).overflowY;
          const ownsScroll = ['auto', 'scroll'].includes(overflow)
            && element.scrollHeight > element.clientHeight + 1;
          return !ownsScroll || Boolean(element.closest('.long-content'));
        })"""
    )


def test_message_cards_follow_raw_blocks_and_only_bound_extreme_payloads(tmp_path: Path) -> None:
    output = _render_long_xml_viewer(tmp_path)

    with playwright.sync_playwright() as runtime:
        try:
            browser = runtime.chromium.launch(headless=True)
        except playwright.Error as exc:
            pytest.skip(f"Playwright Chromium is unavailable: {exc}")
        page = browser.new_page(viewport={"width": 1440, "height": 800})
        page.goto(output.as_uri())
        assert page.locator(".trajectory-row").count() == 1
        assert page.locator("#capture-foot").is_hidden()
        page.locator(".trajectory-row").first.click()
        page.locator('#workspace[data-inspector="open"]').wait_for()
        page.locator('[data-tab="conversation"]').click()

        messages = page.locator(".request-message")
        assert messages.count() == 33
        xml_card = messages.filter(has_text="<root>")
        medium_card = messages.filter(has_text="Medium message line 0")
        short_card = messages.filter(has_text="Short message 0")
        ordinary_tool = page.locator('.message-tool-block[data-call-id="toolu_scroll_ordinary"]')
        extreme_tool = page.locator('.message-tool-block[data-call-id="toolu_scroll_extreme"]')
        xml_card.wait_for()
        assert "value 239" in xml_card.inner_text()

        groups = page.locator(".request-message-group")
        assert groups.locator(".request-message").count() == messages.count()
        _assert_message_headers_accessible_and_contained(page)
        _assert_message_groups_do_not_overlap(page)
        assert messages.evaluate_all(
            "elements => elements.every(element => element.querySelectorAll(':scope > .request-message-body > .message-block').length === 1)"
        )
        assert xml_card.bounding_box()["height"] > short_card.bounding_box()["height"]

        xml_body = xml_card.locator(".request-message-body")
        medium_body = medium_card.locator(".request-message-body")
        short_body = short_card.locator(".request-message-body")
        xml_block = xml_body.locator(".long-content")
        ordinary_tool_data = ordinary_tool.locator(".message-tool-data")
        extreme_tool_data = extreme_tool.locator(".message-tool-data")
        inspector = page.locator("#inspector-body")
        assert xml_body.evaluate("element => getComputedStyle(element).overflowY") == "visible"
        assert medium_body.evaluate("element => getComputedStyle(element).overflowY") == "visible"
        assert short_body.evaluate("element => getComputedStyle(element).overflowY") == "visible"
        assert xml_block.evaluate("element => getComputedStyle(element).overflowY") in {"auto", "scroll"}
        assert xml_block.evaluate("element => getComputedStyle(element).overscrollBehaviorY") == "auto"
        assert xml_block.evaluate("element => element.scrollHeight > element.clientHeight")
        assert medium_body.locator(".long-content").count() == 0
        assert short_body.locator(".long-content").count() == 0
        assert "long-content" not in (ordinary_tool.get_attribute("class") or "")
        assert "long-content" in (extreme_tool.get_attribute("class") or "")
        assert ordinary_tool_data.evaluate("element => getComputedStyle(element).overflowY") == "visible"
        assert ordinary_tool_data.evaluate("element => element.scrollHeight <= element.clientHeight + 1")
        assert extreme_tool_data.evaluate("element => getComputedStyle(element).overflowY") in {"auto", "scroll"}
        assert extreme_tool_data.evaluate("element => getComputedStyle(element).overscrollBehaviorY") == "auto"
        assert extreme_tool_data.evaluate("element => element.scrollHeight > element.clientHeight")
        assert extreme_tool_data.evaluate("element => element.clientHeight <= 240")
        assert extreme_tool_data.evaluate("element => parseFloat(getComputedStyle(element).maxHeight) <= 240")
        assert inspector.evaluate("element => element.scrollHeight > element.clientHeight")
        _assert_only_extreme_payloads_own_nested_vertical_scroll(page)

        xml_card.scroll_into_view_if_needed()
        xml_block.evaluate("element => { element.scrollTop = 0; }")
        inspector_before_inner = inspector.evaluate("element => element.scrollTop")
        xml_block.hover()
        page.mouse.wheel(0, 220)
        page.wait_for_function("document.querySelector('.message-block.long-content').scrollTop > 0")
        assert inspector.evaluate("element => element.scrollTop") == inspector_before_inner

        xml_block.evaluate("element => { element.scrollTop = element.scrollHeight; }")
        inspector_before_chain = inspector.evaluate("element => element.scrollTop")
        xml_block.hover()
        page.mouse.wheel(0, 220)
        page.wait_for_function(
            "before => document.querySelector('#inspector-body').scrollTop > before",
            arg=inspector_before_chain,
        )

        ordinary_tool.scroll_into_view_if_needed()
        inspector_before_ordinary_tool = inspector.evaluate("element => element.scrollTop")
        ordinary_tool_data.hover()
        page.mouse.wheel(0, 180)
        page.wait_for_function(
            "before => document.querySelector('#inspector-body').scrollTop > before",
            arg=inspector_before_ordinary_tool,
        )

        extreme_tool.scroll_into_view_if_needed()
        extreme_tool_data.evaluate("element => { element.scrollTop = 0; }")
        inspector_before_extreme_inner = inspector.evaluate("element => element.scrollTop")
        extreme_tool_data.hover()
        page.mouse.wheel(0, 220)
        page.wait_for_function(
            "document.querySelector('[data-call-id=\"toolu_scroll_extreme\"] .message-tool-data').scrollTop > 0"
        )
        assert inspector.evaluate("element => element.scrollTop") == inspector_before_extreme_inner

        extreme_tool_data.evaluate("element => { element.scrollTop = element.scrollHeight; }")
        inspector_before_extreme_chain = inspector.evaluate("element => element.scrollTop")
        extreme_tool_data.hover()
        page.mouse.wheel(0, 220)
        page.wait_for_function(
            "before => document.querySelector('#inspector-body').scrollTop > before",
            arg=inspector_before_extreme_chain,
        )

        short_card.scroll_into_view_if_needed()
        inspector_before_short = inspector.evaluate("element => element.scrollTop")
        short_body.hover()
        page.mouse.wheel(0, 160)
        page.wait_for_function(
            "before => document.querySelector('#inspector-body').scrollTop > before",
            arg=inspector_before_short,
        )
        assert messages.evaluate_all(
            "elements => elements.every(element => element.scrollWidth <= element.clientWidth + 1)"
        )
        assert messages.locator(".request-message-body").evaluate_all(
            "elements => elements.every(element => element.scrollWidth <= element.clientWidth + 1)"
        )
        assert inspector.evaluate("element => element.scrollWidth <= element.clientWidth + 1")

        remembered = inspector.evaluate("(element) => element.scrollTop")
        page.locator("#close-inspector").click()
        assert page.locator(".trajectory-row.selected").count() == 1
        page.locator("#reopen-inspector").click()
        page.wait_for_function(
            "remembered => Math.abs(document.querySelector('#inspector-body').scrollTop - remembered) <= 1",
            arg=remembered,
        )

        page.set_viewport_size({"width": 390, "height": 844})
        page.wait_for_function("document.querySelector('#inspector').getBoundingClientRect().width === 390")
        _assert_message_headers_accessible_and_contained(page)
        _assert_message_groups_do_not_overlap(page)
        assert xml_card.bounding_box()["height"] > short_card.bounding_box()["height"]
        assert messages.evaluate_all(
            "elements => elements.every(element => element.scrollWidth <= element.clientWidth + 1)"
        )
        assert messages.locator(".request-message-body").evaluate_all(
            "elements => elements.every(element => element.scrollWidth <= element.clientWidth + 1)"
        )
        assert inspector.evaluate("element => element.scrollWidth <= element.clientWidth + 1")
        assert page.evaluate("document.documentElement.scrollWidth === innerWidth")

        browser.close()


def test_mobile_drawer_and_settings_menu_stay_accessible_and_in_bounds(tmp_path: Path) -> None:
    output = _render_long_xml_viewer(tmp_path)

    with playwright.sync_playwright() as runtime:
        try:
            browser = runtime.chromium.launch(headless=True)
        except playwright.Error as exc:
            pytest.skip(f"Playwright Chromium is unavailable: {exc}")
        page = browser.new_page(viewport={"width": 320, "height": 568})
        page.goto(output.as_uri())
        page.locator("html[data-viewer-ready='true']").wait_for()

        sidebar = page.locator("#session-panel")
        closed_box = sidebar.bounding_box()
        assert closed_box is not None and closed_box["x"] + closed_box["width"] <= 1
        assert abs(closed_box["width"] - 280) <= 1
        assert sidebar.get_attribute("aria-hidden") == "true"
        assert sidebar.evaluate("element => element.inert")

        page.locator("#open-sidebar").click()
        page.locator('#workspace[data-sidebar="open"]').wait_for()
        assert sidebar.get_attribute("aria-hidden") == "false"
        assert not sidebar.evaluate("element => element.inert")
        page.wait_for_function("document.activeElement?.id === 'session-search'")
        assert page.locator("#call-panel").evaluate("element => element.inert")
        assert page.locator("#open-sidebar").get_attribute("aria-expanded") == "true"
        assert page.locator("#sidebar-browser").get_attribute("data-search-expanded") == "true"

        page.locator("#settings-trigger").click()
        menu = page.locator("#settings-menu")
        menu.wait_for(state="visible")
        menu_box = menu.bounding_box()
        sidebar_box = sidebar.bounding_box()
        assert menu_box is not None and sidebar_box is not None
        assert abs(sidebar_box["width"] - closed_box["width"]) <= 1
        assert menu_box["x"] >= sidebar_box["x"]
        assert menu_box["x"] + menu_box["width"] <= sidebar_box["x"] + sidebar_box["width"] + 1
        assert menu_box["y"] >= 12
        assert menu_box["y"] + menu_box["height"] <= 568 - 12
        assert menu.evaluate("element => element.parentElement === document.body")
        assert all(
            height >= 40 for height in menu.locator(".menu-item").evaluate_all("els => els.map(e => e.offsetHeight)")
        )
        assert page.evaluate("document.documentElement.scrollWidth === innerWidth")

        page.keyboard.press("Escape")
        assert menu.is_hidden()
        assert page.locator("#settings-trigger").evaluate("element => element === document.activeElement")
        assert not menu.evaluate("element => element.parentElement === document.body")

        page.locator("#mobile-scrim").click(position={"x": 310, "y": 40})
        page.locator('#workspace[data-sidebar="collapsed"]').wait_for()
        page.wait_for_function("document.activeElement?.id === 'open-sidebar'")
        assert sidebar.get_attribute("aria-hidden") == "true"
        assert sidebar.evaluate("element => element.inert")
        assert not page.locator("#call-panel").evaluate("element => element.inert")

        page.locator("#open-sidebar").click()
        page.locator('#workspace[data-sidebar="open"]').wait_for()
        page.locator("#settings-trigger").focus()
        page.keyboard.press("Escape")
        page.locator('#workspace[data-sidebar="collapsed"]').wait_for()
        page.wait_for_function("document.activeElement?.id === 'open-sidebar'")
        assert page.evaluate("document.documentElement.scrollWidth === innerWidth")

        browser.close()


def test_session_list_owns_scroll_only_when_sessions_overflow(tmp_path: Path) -> None:
    def record(index: int) -> dict[str, object]:
        return {
            "timestamp": f"2026-08-20T09:{index:02d}:00+08:00",
            "request_id": f"req_session_{index}",
            "turn": 1,
            "duration_ms": 10,
            "request": {
                "method": "POST",
                "path": "/v1/messages",
                "headers": {"X-Claude-Code-Session-Id": f"session-{index:02d}"},
                "body": {
                    "model": "claude-test",
                    "messages": [{"role": "user", "content": f"Session {index}"}],
                },
            },
            "response": {
                "status": 200,
                "headers": {},
                "body": {"content": [{"type": "text", "text": f"Reply {index}"}]},
            },
        }

    short_trace = tmp_path / "trace_short_session_list.jsonl"
    short_trace.write_text(json.dumps(record(0)) + "\n", encoding="utf-8")
    short_output = short_trace.with_suffix(".html")
    assert render_html(short_trace, short_output)

    long_trace = tmp_path / "trace_long_session_list.jsonl"
    long_trace.write_text(
        "\n".join(json.dumps(record(index)) for index in range(40)) + "\n",
        encoding="utf-8",
    )
    long_output = long_trace.with_suffix(".html")
    assert render_html(long_trace, long_output)

    with playwright.sync_playwright() as runtime:
        try:
            browser = runtime.chromium.launch(headless=True)
        except playwright.Error as exc:
            pytest.skip(f"Playwright Chromium is unavailable: {exc}")

        page = browser.new_page(viewport={"width": 1440, "height": 800})
        page.goto(short_output.as_uri())
        page.locator(".session-row").wait_for()
        session_list = page.locator("#session-list")
        session_canvas = page.locator("#session-canvas")
        assert session_list.evaluate("element => element.scrollHeight === element.clientHeight")
        assert session_list.evaluate("element => getComputedStyle(element).scrollbarGutter") == "auto"
        assert session_canvas.evaluate("element => element.style.height") == "34px"
        assert session_canvas.evaluate("element => getComputedStyle(element).minHeight") == "0px"

        page.evaluate(
            """() => {
              const input = document.querySelector('#session-search');
              input.value = 'no matching session';
              input.dispatchEvent(new Event('input', { bubbles: true }));
            }"""
        )
        page.wait_for_function("document.querySelector('#session-count').textContent === '0'")
        assert session_canvas.evaluate("element => element.style.height") == "0px"
        assert session_list.evaluate("element => element.scrollHeight === element.clientHeight")
        page.close()

        page = browser.new_page(viewport={"width": 1440, "height": 800})
        page.goto(long_output.as_uri())
        page.wait_for_function("document.querySelector('#session-count').textContent === '40'")
        session_list = page.locator("#session-list")
        assert session_list.evaluate("element => element.scrollHeight > element.clientHeight")
        before = session_list.evaluate("element => element.scrollTop")
        session_list.evaluate("element => { element.scrollTop = 320; element.dispatchEvent(new Event('scroll')); }")
        page.wait_for_timeout(50)
        assert session_list.evaluate("element => element.scrollTop") > before
        browser.close()


def test_sidebar_search_expands_filters_clears_and_opens_from_the_rail(tmp_path: Path) -> None:
    output = _render_long_xml_viewer(tmp_path)

    with playwright.sync_playwright() as runtime:
        try:
            browser = runtime.chromium.launch(headless=True)
        except playwright.Error as exc:
            pytest.skip(f"Playwright Chromium is unavailable: {exc}")
        page = browser.new_page(viewport={"width": 1440, "height": 800})
        page.goto(output.as_uri())
        page.locator("html[data-viewer-ready='true']").wait_for()

        search_toggle = page.locator("#session-search-toggle")
        search = page.locator("#session-search")
        clear = page.locator("#session-search-clear")
        assert page.locator(".session-row").count() == 1
        assert search_toggle.get_attribute("aria-expanded") == "false"
        assert search.get_attribute("tabindex") == "-1"

        search_toggle.click()
        page.wait_for_function("document.activeElement?.id === 'session-search'")
        assert page.locator("#sidebar-browser").get_attribute("data-search-expanded") == "true"
        assert search_toggle.get_attribute("aria-expanded") == "true"
        assert search.get_attribute("tabindex") == "0"
        assert clear.is_visible()

        search.fill("definitely-not-a-session")
        page.wait_for_function("document.querySelectorAll('.session-row').length === 0")
        clear.click()
        page.wait_for_function("document.querySelectorAll('.session-row').length === 1")
        assert search.input_value() == ""
        assert page.locator("#sidebar-browser").get_attribute("data-search-expanded") == "false"
        assert search_toggle.get_attribute("aria-expanded") == "false"
        assert search_toggle.evaluate("element => element === document.activeElement")

        search_toggle.click()
        search.fill("still-not-a-session")
        page.wait_for_function("document.querySelectorAll('.session-row').length === 0")
        search.press("Escape")
        page.wait_for_function("document.querySelectorAll('.session-row').length === 1")
        assert search.input_value() == ""
        assert page.locator("#sidebar-browser").get_attribute("data-search-expanded") == "false"
        assert search_toggle.evaluate("element => element === document.activeElement")
        assert page.locator("#workspace").get_attribute("data-sidebar") == "open"

        page.locator("#toggle-sidebar").click()
        _wait_for_sidebar_settle(page, "collapsed", 56)
        page.locator("#rail-search").click()
        _wait_for_sidebar_settle(page, "open", 280)
        page.wait_for_function("document.activeElement?.id === 'session-search'")
        assert page.locator("#sidebar-browser").get_attribute("data-search-expanded") == "true"
        assert search_toggle.get_attribute("aria-expanded") == "true"

        browser.close()


def test_portaled_sidebar_menus_stay_in_viewport_and_support_keyboard_navigation(tmp_path: Path) -> None:
    output = _render_long_xml_viewer(tmp_path)

    with playwright.sync_playwright() as runtime:
        try:
            browser = runtime.chromium.launch(headless=True)
        except playwright.Error as exc:
            pytest.skip(f"Playwright Chromium is unavailable: {exc}")
        page = browser.new_page(viewport={"width": 1440, "height": 260})
        page.goto(output.as_uri())
        page.locator("html[data-viewer-ready='true']").wait_for()

        capture_trigger = page.locator("#capture-trigger")
        capture_trigger.click()
        capture_menu = page.locator("#capture-menu")
        capture_menu.wait_for(state="visible")
        page.wait_for_function("document.activeElement?.closest('#capture-menu') !== null")
        capture_box = capture_menu.bounding_box()
        assert capture_box is not None
        assert capture_menu.evaluate("element => element.parentElement === document.body")
        assert capture_menu.evaluate("element => getComputedStyle(element).position") == "fixed"
        assert capture_box["x"] >= 12
        assert capture_box["x"] + capture_box["width"] <= 1440 - 12
        assert capture_box["y"] >= 12
        assert capture_box["y"] + capture_box["height"] <= 260 - 12
        assert capture_menu.locator('[role="menuitemradio"][aria-checked="true"]').count() == 1
        page.keyboard.press("Escape")
        assert capture_menu.is_hidden()
        assert capture_trigger.evaluate("element => element === document.activeElement")
        assert capture_trigger.get_attribute("aria-expanded") == "false"
        assert not capture_menu.evaluate("element => element.parentElement === document.body")

        settings_trigger = page.locator("#settings-trigger")
        settings_trigger.click()
        settings_menu = page.locator("#settings-menu")
        settings_menu.wait_for(state="visible")
        page.wait_for_function("document.activeElement?.dataset.menuId === 'theme'")
        settings_box = settings_menu.bounding_box()
        assert settings_box is not None
        assert settings_menu.evaluate("element => element.parentElement === document.body")
        assert settings_box["x"] >= 12
        assert settings_box["x"] + settings_box["width"] <= 1440 - 12
        assert settings_box["y"] >= 12
        assert settings_box["y"] + settings_box["height"] <= 260 - 12

        page.keyboard.press("ArrowDown")
        assert page.evaluate("document.activeElement?.dataset.menuId") == "language"
        page.keyboard.press("ArrowDown")
        assert page.evaluate("document.activeElement?.dataset.menuId") == "theme"
        page.keyboard.press("End")
        assert page.evaluate("document.activeElement?.dataset.menuId") == "language"
        page.keyboard.press("Home")
        assert page.evaluate("document.activeElement?.dataset.menuId") == "theme"
        page.keyboard.press("Tab")
        assert settings_menu.is_hidden()
        assert settings_trigger.get_attribute("aria-expanded") == "false"

        settings_trigger.click()
        settings_menu.wait_for(state="visible")
        page.keyboard.press("Escape")
        assert settings_menu.is_hidden()
        assert settings_trigger.evaluate("element => element === document.activeElement")
        assert not settings_menu.evaluate("element => element.parentElement === document.body")

        browser.close()


@pytest.mark.parametrize("viewport_width", [800, 960, 1120])
def test_compact_desktop_sidebar_can_still_open(viewport_width: int, tmp_path: Path) -> None:
    output = _render_long_xml_viewer(tmp_path)

    with playwright.sync_playwright() as runtime:
        try:
            browser = runtime.chromium.launch(headless=True)
        except playwright.Error as exc:
            pytest.skip(f"Playwright Chromium is unavailable: {exc}")
        page = browser.new_page(viewport={"width": viewport_width, "height": 720})
        page.goto(output.as_uri())
        page.locator("html[data-viewer-ready='true']").wait_for()

        _wait_for_sidebar_settle(page, "collapsed", 56)
        page.locator("#toggle-sidebar").click()
        _wait_for_sidebar_settle(page, "open", 280)
        sidebar = page.locator("#session-panel")
        assert abs(sidebar.bounding_box()["width"] - 280) <= 1
        assert page.locator("#session-panel .sidebar-wide").first.is_visible()
        assert page.locator("#session-search-toggle").is_visible()
        assert page.locator("#session-search").is_hidden()

        page.locator("#toggle-sidebar").click()
        _wait_for_sidebar_settle(page, "collapsed", 56)
        assert abs(sidebar.bounding_box()["width"] - 56) <= 1
        page.locator("#toggle-sidebar").click()
        _wait_for_sidebar_settle(page, "open", 280)
        assert abs(sidebar.bounding_box()["width"] - 280) <= 1
        assert page.evaluate("document.documentElement.scrollWidth === innerWidth")

        browser.close()


def test_sidebar_rapid_reversal_cancels_stale_phases_and_rail_capture_opens_its_menu(tmp_path: Path) -> None:
    output = _render_long_xml_viewer(tmp_path)

    with playwright.sync_playwright() as runtime:
        try:
            browser = runtime.chromium.launch(headless=True)
        except playwright.Error as exc:
            pytest.skip(f"Playwright Chromium is unavailable: {exc}")
        page = browser.new_page(viewport={"width": 1440, "height": 800})
        page.goto(output.as_uri())
        page.locator("html[data-viewer-ready='true']").wait_for()

        toggle = page.locator("#toggle-sidebar")
        toggle.click()
        page.locator('#workspace[data-sidebar-phase="collapsing"]').wait_for()
        toggle.evaluate("element => element.click()")
        _wait_for_sidebar_settle(page, "open", 280)
        assert page.evaluate("localStorage.getItem('claude-tap-sidebar')") == "open"
        assert page.locator("#session-search-toggle").is_visible()
        assert page.locator("#rail-capture").is_hidden()

        toggle.click()
        _wait_for_sidebar_settle(page, "collapsed", 56)
        page.locator("#rail-capture").click()
        _wait_for_sidebar_settle(page, "open", 280)
        capture_menu = page.locator("#capture-menu")
        capture_menu.wait_for(state="visible")
        page.wait_for_function("document.activeElement?.closest('#capture-menu') !== null")
        assert capture_menu.evaluate("element => element.parentElement === document.body")
        assert page.locator("#capture-trigger").get_attribute("aria-expanded") == "true"
        assert page.evaluate("localStorage.getItem('claude-tap-sidebar')") == "open"

        page.keyboard.press("Escape")
        assert capture_menu.is_hidden()
        assert page.locator("#capture-trigger").evaluate("element => element === document.activeElement")

        browser.close()


def test_collapsed_sidebar_hover_swaps_icons_without_opening(tmp_path: Path) -> None:
    output = _render_long_xml_viewer(tmp_path)

    with playwright.sync_playwright() as runtime:
        try:
            browser = runtime.chromium.launch(headless=True)
        except playwright.Error as exc:
            pytest.skip(f"Playwright Chromium is unavailable: {exc}")
        page = browser.new_page(viewport={"width": 1440, "height": 800})
        page.goto(output.as_uri())
        page.locator("html[data-viewer-ready='true']").wait_for()

        toggle = page.locator("#toggle-sidebar")
        _watch_sidebar_phases(page)
        toggle.click()
        _wait_for_sidebar_settle(page, "collapsed", 56)
        assert _sidebar_phases(page) == ["collapsing", "rail-entering", None]
        sidebar = page.locator("#session-panel")
        rest_icon = toggle.locator(".sidebar-rest-icon")
        panel_icon = toggle.locator(".sidebar-panel-icon")
        assert rest_icon.is_visible()
        assert panel_icon.is_hidden()
        assert page.locator("#rail-capture").is_visible()
        assert page.locator("#rail-search").is_visible()

        persisted = page.evaluate("localStorage.getItem('claude-tap-sidebar')")
        call_panel_before = page.locator("#call-panel").bounding_box()
        toggle.hover()
        assert rest_icon.is_hidden()
        assert panel_icon.is_visible()
        assert page.locator("#workspace").get_attribute("data-sidebar") == "collapsed"
        assert abs(sidebar.bounding_box()["width"] - 56) <= 1
        assert page.evaluate("localStorage.getItem('claude-tap-sidebar')") == persisted
        call_panel_after = page.locator("#call-panel").bounding_box()
        assert call_panel_before is not None and call_panel_after is not None
        assert abs(call_panel_after["x"] - call_panel_before["x"]) <= 1
        assert abs(call_panel_after["width"] - call_panel_before["width"]) <= 1

        page.mouse.move(call_panel_after["x"] + 20, call_panel_after["y"] + 20)
        assert rest_icon.is_visible()
        assert panel_icon.is_hidden()
        browser.close()


def test_short_viewport_rail_tooltip_is_delayed_clamped_and_dismissed_by_menu(tmp_path: Path) -> None:
    output = _render_long_xml_viewer(tmp_path)

    with playwright.sync_playwright() as runtime:
        try:
            browser = runtime.chromium.launch(headless=True)
        except playwright.Error as exc:
            pytest.skip(f"Playwright Chromium is unavailable: {exc}")
        page = browser.new_page(viewport={"width": 960, "height": 220})
        page.goto(output.as_uri())
        page.locator("html[data-viewer-ready='true']").wait_for()
        _wait_for_sidebar_settle(page, "collapsed", 56)

        settings = page.locator("#settings-trigger")
        settings.hover()
        page.wait_for_timeout(400)
        assert page.locator('[role="tooltip"]').count() == 0
        page.wait_for_timeout(150)
        tooltip = page.locator('[role="tooltip"]')
        tooltip.wait_for()
        tooltip_box = tooltip.bounding_box()
        assert tooltip_box is not None
        assert tooltip.inner_text() == "Settings"
        assert tooltip.evaluate("element => element.parentElement === document.body")
        assert tooltip_box["x"] >= 12
        assert tooltip_box["x"] + tooltip_box["width"] <= 960 - 12
        assert tooltip_box["y"] >= 12
        assert tooltip_box["y"] + tooltip_box["height"] <= 220 - 12

        settings.click()
        assert page.locator('[role="tooltip"]').count() == 0
        menu = page.locator("#settings-menu")
        menu.wait_for(state="visible")
        menu_box = menu.bounding_box()
        assert menu_box is not None
        assert menu_box["x"] >= 12
        assert menu_box["x"] + menu_box["width"] <= 960 - 12
        assert menu_box["y"] >= 12
        assert menu_box["y"] + menu_box["height"] <= 220 - 12

        page.keyboard.press("Escape")
        assert menu.is_hidden()
        browser.close()


def test_sidebar_tooltip_keyboard_and_aria_contract(tmp_path: Path) -> None:
    output = _render_long_xml_viewer(tmp_path)

    with playwright.sync_playwright() as runtime:
        try:
            browser = runtime.chromium.launch(headless=True)
        except playwright.Error as exc:
            pytest.skip(f"Playwright Chromium is unavailable: {exc}")
        page = browser.new_page(viewport={"width": 1440, "height": 800})
        page.goto(output.as_uri())
        page.locator("html[data-viewer-ready='true']").wait_for()

        toggle = page.locator("#toggle-sidebar")
        toggle.click()
        _wait_for_sidebar_settle(page, "collapsed", 56)
        assert toggle.get_attribute("aria-label") == "Open sidebar"
        assert toggle.get_attribute("aria-expanded") == "false"
        toggle.evaluate("element => element.blur()")

        # Hover is deliberately delayed, and leaving before the deadline must
        # cancel the pending tooltip rather than producing a late flash.
        toggle.hover()
        page.wait_for_timeout(400)
        assert page.locator('[role="tooltip"]').count() == 0
        toggle_box = toggle.bounding_box()
        assert toggle_box is not None
        page.mouse.move(toggle_box["x"] + toggle_box["width"] + 40, toggle_box["y"])
        page.wait_for_timeout(150)
        assert page.locator('[role="tooltip"]').count() == 0

        toggle.hover()
        page.wait_for_timeout(550)
        tooltip = page.locator('[role="tooltip"]')
        tooltip.wait_for()
        assert tooltip.inner_text() == "Open sidebar"
        tooltip_id = tooltip.get_attribute("id")
        assert tooltip_id and tooltip_id in (toggle.get_attribute("aria-describedby") or "").split()
        tooltip_box = tooltip.bounding_box()
        toggle_box = toggle.bounding_box()
        assert tooltip_box is not None and toggle_box is not None
        assert tooltip_box["x"] >= toggle_box["x"] + toggle_box["width"]
        assert tooltip_box["x"] >= 12
        assert tooltip_box["x"] + tooltip_box["width"] <= 1440 - 12
        assert tooltip_box["y"] >= 12
        assert tooltip_box["y"] + tooltip_box["height"] <= 800 - 12

        page.mouse.move(300, 300)
        assert tooltip.count() == 0
        assert toggle.get_attribute("aria-describedby") is None

        # Keyboard focus has no hover delay and exposes the same affordance.
        page.keyboard.press("Tab")
        toggle.focus()
        assert toggle.evaluate("element => element === document.activeElement")
        page.locator('[role="tooltip"]').wait_for()
        assert page.locator('[role="tooltip"]').inner_text() == "Open sidebar"
        assert toggle.locator(".sidebar-rest-icon").is_hidden()
        assert toggle.locator(".sidebar-panel-icon").is_visible()
        _watch_sidebar_phases(page)
        page.keyboard.press("Enter")
        _wait_for_sidebar_settle(page, "open", 280)
        assert _sidebar_phases(page) == ["expanding", None]
        assert toggle.get_attribute("aria-expanded") == "true"
        assert toggle.get_attribute("aria-label") == "Collapse sidebar"
        assert toggle.evaluate("element => element === document.activeElement")
        assert page.locator('[role="tooltip"]').count() == 0
        toggle.evaluate("element => element.blur()")
        toggle.focus()
        page.locator('[role="tooltip"]').wait_for()
        assert page.locator('[role="tooltip"]').inner_text() == "Collapse sidebar"

        _watch_sidebar_phases(page)
        page.keyboard.press("Space")
        _wait_for_sidebar_settle(page, "collapsed", 56)
        assert _sidebar_phases(page) == ["collapsing", "rail-entering", None]
        assert toggle.get_attribute("aria-expanded") == "false"
        assert toggle.get_attribute("aria-label") == "Open sidebar"
        assert toggle.evaluate("element => element === document.activeElement")
        assert page.locator('[role="tooltip"]').count() == 0
        toggle.evaluate("element => element.blur()")
        toggle.focus()
        page.locator('[role="tooltip"]').wait_for()
        assert page.locator('[role="tooltip"]').inner_text() == "Open sidebar"
        toggle.evaluate("element => element.blur()")
        assert page.locator('[role="tooltip"]').count() == 0
        browser.close()


def test_sidebar_reduced_motion_settles_without_animation_phases(tmp_path: Path) -> None:
    output = _render_long_xml_viewer(tmp_path)

    with playwright.sync_playwright() as runtime:
        try:
            browser = runtime.chromium.launch(headless=True)
        except playwright.Error as exc:
            pytest.skip(f"Playwright Chromium is unavailable: {exc}")
        page = browser.new_page(viewport={"width": 1440, "height": 800})
        page.emulate_media(reduced_motion="reduce")
        page.goto(output.as_uri())
        page.locator("html[data-viewer-ready='true']").wait_for()

        toggle = page.locator("#toggle-sidebar")
        toggle.click()
        _wait_for_sidebar_settle(page, "collapsed", 56)
        assert page.locator("#workspace").get_attribute("data-sidebar-phase") is None
        assert page.locator("#rail-capture").is_visible()
        assert page.locator("#rail-search").is_visible()
        max_motion_duration_ms = page.locator("#workspace").evaluate(
            """element => {
              const milliseconds = value => value.trim().endsWith("ms")
                ? Number.parseFloat(value)
                : Number.parseFloat(value) * 1000;
              const values = [];
              for (const node of [element, ...element.querySelectorAll("*")]) {
                const style = getComputedStyle(node);
                values.push(...style.transitionDuration.split(",").map(milliseconds));
                values.push(...style.animationDuration.split(",").map(milliseconds));
              }
              return Math.max(...values.filter(Number.isFinite));
            }"""
        )
        assert max_motion_duration_ms <= 0.011

        rail_capture = page.locator("#rail-capture")
        rail_capture.focus()
        tooltip = page.locator('[role="tooltip"]')
        tooltip.wait_for()
        assert tooltip.evaluate("element => element.parentElement === document.body")
        assert tooltip.evaluate("element => element.style.opacity") == "1"
        assert tooltip.evaluate("element => element.style.transition") == ""
        assert (
            tooltip.evaluate(
                """element => {
                  const value = getComputedStyle(element).transitionDuration.trim();
                  return value.endsWith('ms') ? Number.parseFloat(value) : Number.parseFloat(value) * 1000;
                }"""
            )
            <= 0.011
        )
        rail_capture.evaluate("element => element.blur()")
        assert tooltip.count() == 0

        toggle.click()
        _wait_for_sidebar_settle(page, "open", 280)
        assert page.locator("#workspace").get_attribute("data-sidebar-phase") is None
        assert page.locator("#session-search-toggle").is_visible()
        assert page.locator("#sidebar-browser").get_attribute("data-search-expanded") != "true"
        browser.close()


def test_inspector_separator_supports_keyboard_and_a_wide_detail_view(tmp_path: Path) -> None:
    output = _render_long_xml_viewer(tmp_path)

    with playwright.sync_playwright() as runtime:
        try:
            browser = runtime.chromium.launch(headless=True)
        except playwright.Error as exc:
            pytest.skip(f"Playwright Chromium is unavailable: {exc}")
        page = browser.new_page(viewport={"width": 2048, "height": 900})
        page.goto(output.as_uri())
        page.locator(".trajectory-row").click()
        page.locator('#workspace[data-inspector="open"]').wait_for()

        separator = page.locator("#resize-handle")
        assert separator.get_attribute("role") == "separator"
        assert separator.get_attribute("tabindex") == "0"
        initial_width = page.locator("#inspector").bounding_box()["width"]
        separator.focus()
        page.keyboard.press("ArrowLeft")
        page.wait_for_timeout(50)
        assert page.locator("#inspector").bounding_box()["width"] > initial_width

        page.keyboard.press("End")
        page.wait_for_function("document.querySelector('#inspector').getBoundingClientRect().width > 1400")
        inspector_width = page.locator("#inspector").bounding_box()["width"]
        call_panel_width = page.locator("#call-panel").bounding_box()["width"]
        sidebar_width = page.locator("#session-panel").bounding_box()["width"]
        assert inspector_width > 1400
        assert inspector_width == pytest.approx(2048 - sidebar_width - 216, abs=1)
        assert call_panel_width == pytest.approx(216, abs=1)
        assert int(separator.get_attribute("aria-valuenow")) == round(inspector_width)
        # This is deliberately a container-width assertion: the viewport is
        # still wide, but a narrow center ledger must retain scan metadata and
        # remove the prose/tool preview that no longer fits.
        compact_row = page.locator(".trajectory-row")
        page.wait_for_function("document.querySelector('#call-panel').dataset.trajectoryDensity === 'compact'")
        page.wait_for_function("document.querySelector('.trajectory-row').getBoundingClientRect().height === 52")
        assert page.locator("#call-panel").get_attribute("data-trajectory-density") == "compact"
        assert not compact_row.locator(".call-activity").is_visible()
        assert compact_row.locator(".call-model-cell").is_visible()
        assert compact_row.locator(".call-timing").is_visible()
        assert compact_row.bounding_box()["height"] == 52
        compact_entry = compact_row.locator(".call-entry")
        compact_meta = compact_row.locator(".call-meta")
        assert compact_entry.evaluate("element => element.scrollWidth <= element.clientWidth + 1")
        assert compact_meta.evaluate("element => element.scrollWidth <= element.clientWidth + 1")
        assert compact_entry.evaluate("element => getComputedStyle(element).borderRadius") == "7px"
        assert compact_entry.evaluate("element => getComputedStyle(element).marginBlock") == "1px"
        assert compact_entry.evaluate("element => getComputedStyle(element).borderLeftWidth") == "0px"
        assert compact_entry.evaluate("element => getComputedStyle(element).borderBottomWidth") == "0px"
        assert compact_meta.evaluate("element => getComputedStyle(element).backgroundColor") == "rgba(0, 0, 0, 0)"
        assert compact_row.evaluate("element => getComputedStyle(element).backgroundColor") == "rgba(0, 0, 0, 0)"
        assert compact_entry.evaluate("element => getComputedStyle(element).backgroundColor") != "rgba(0, 0, 0, 0)"
        assert page.evaluate("document.documentElement.scrollWidth === innerWidth")

        separator.dblclick()
        page.wait_for_function(
            f"document.querySelector('#inspector').getBoundingClientRect().width < {inspector_width - 40}"
        )
        assert page.locator("#inspector").bounding_box()["width"] < inspector_width
        assert page.evaluate("document.documentElement.scrollWidth === innerWidth")

        browser.close()


def test_adjacent_prefetch_is_deduplicated_and_cached_selection_never_flashes_loading(tmp_path: Path) -> None:
    records = []
    for index in range(5):
        records.append(
            {
                "timestamp": f"2026-08-20T09:00:0{index}+08:00",
                "request_id": f"req_prefetch_{index}",
                "turn": index + 1,
                "request": {
                    "method": "POST",
                    "path": "/v1/messages",
                    "headers": {},
                    "body": {
                        "model": "claude-test",
                        "messages": [{"role": "user", "content": f"request {index}"}],
                    },
                },
                "response": {
                    "status": 200,
                    "headers": {},
                    "body": {"content": [{"type": "text", "text": f"response {index}"}]},
                },
            }
        )
    trace = tmp_path / "trace_prefetch.jsonl"
    trace.write_text("\n".join(json.dumps(item) for item in records) + "\n", encoding="utf-8")
    output = trace.with_suffix(".html")
    assert render_html(trace, output)

    with playwright.sync_playwright() as runtime:
        try:
            browser = runtime.chromium.launch(headless=True)
        except playwright.Error as exc:
            pytest.skip(f"Playwright Chromium is unavailable: {exc}")
        page = browser.new_page(viewport={"width": 1440, "height": 800})
        page.goto(output.as_uri())

        fetch_counts = page.evaluate(
            """async () => {
              const { EventHub } = window.ClaudeTapCore;
              const { LiveTraceSource, TraceStore } = window.ClaudeTapData;
              const hub = new EventHub();
              const source = new LiveTraceSource(hub);
              const counts = {};
              source._json = async (url) => {
                counts[url] = (counts[url] || 0) + 1;
                await new Promise((resolve) => setTimeout(resolve, 12));
                return { record: { url } };
              };
              const store = new TraceStore(source, hub);
              const metadata = Array.from({ length: 5 }, (_, record_index) => ({
                capture_id: "capture",
                record_index,
                request_category: "llm",
                session_key: "session",
                timestamp: `2026-08-20T09:00:0${record_index}+08:00`,
                model: "claude-test",
              }));
              store.state.currentCaptureId = "capture";
              store.state.metadata = metadata;
              store._rebuildIndexes();
              store.state.selectedSessionKey = "session";
              store.prefetchRecord(metadata[3]);
              store.prefetchRecord(metadata[3]);
              await store.selectRecord(metadata[2]);
              await new Promise((resolve) => setTimeout(resolve, 850));
              return counts;
            }"""
        )
        assert set(fetch_counts) == {
            "/api/captures/capture/records/0",
            "/api/captures/capture/records/1",
            "/api/captures/capture/records/2",
            "/api/captures/capture/records/3",
            "/api/captures/capture/records/4",
        }
        assert set(fetch_counts.values()) == {1}

        rows = page.locator(".trajectory-row")
        rows.nth(1).click()
        page.locator('#workspace[data-inspector="open"]').wait_for()
        page.evaluate(
            """() => {
              window.__claudeTapSawLoading = false;
              const body = document.querySelector("#inspector-body");
              const inspect = () => {
                if (body.querySelector(".loading-state") || body.textContent.includes("Loading model call")) {
                  window.__claudeTapSawLoading = true;
                }
              };
              new MutationObserver(inspect).observe(body, { childList: true, subtree: true, characterData: true });
            }"""
        )
        rows.nth(2).click()
        page.wait_for_function("document.querySelector('#inspector-title').textContent.includes('#3')")
        assert not page.evaluate("window.__claudeTapSawLoading")

        browser.close()


def test_namespace_tools_render_callable_children(tmp_path: Path) -> None:
    record = {
        "timestamp": "2026-07-31T12:00:00+08:00",
        "request_id": "req_namespace_tools",
        "turn": 1,
        "duration_ms": 42,
        "request": {
            "method": "POST",
            "path": "/v1/responses",
            "headers": {},
            "body": {
                "model": "gpt-test",
                "input": [
                    {
                        "type": "additional_tools",
                        "role": "developer",
                        "tools": [
                            {
                                "type": "namespace",
                                "name": "web",
                                "description": "Tools in the web namespace.",
                                "tools": [
                                    {
                                        "type": "function",
                                        "name": "run",
                                        "description": "Tool for accessing the internet.",
                                        "parameters": {
                                            "type": "object",
                                            "properties": {
                                                "search_query": {
                                                    "type": "array",
                                                    "description": "Queries to search for.",
                                                }
                                            },
                                        },
                                    }
                                ],
                            }
                        ],
                    },
                    {"type": "message", "role": "user", "content": "Search the web."},
                ],
            },
        },
        "response": {
            "status": 200,
            "headers": {},
            "body": {
                "output": [
                    {
                        "type": "function_call",
                        "call_id": "call_web",
                        "name": "web.run",
                        "arguments": '{"search_query":[]}',
                    }
                ]
            },
        },
    }
    trace = tmp_path / "trace_namespace_tools.jsonl"
    trace.write_text(json.dumps(record) + "\n", encoding="utf-8")
    output = trace.with_suffix(".html")
    assert render_html(trace, output)

    with playwright.sync_playwright() as runtime:
        try:
            browser = runtime.chromium.launch(headless=True)
        except playwright.Error as exc:
            pytest.skip(f"Playwright Chromium is unavailable: {exc}")
        page = browser.new_page(viewport={"width": 1440, "height": 800})
        page.goto(output.as_uri())
        page.locator(".trajectory-row").first.click()
        page.locator('[data-tab="tools"]').click()
        tool = page.locator(".tool-definition")
        tool.wait_for()

        assert tool.count() == 1
        assert page.locator(".tool-call").count() == 0
        assert page.locator(".tool-result").count() == 0
        assert tool.locator(".tool-call-name").inner_text() == "web.run"
        tool_name_color = tool.locator(".tool-call-name").evaluate("element => getComputedStyle(element).color")
        tool_description_color = tool.locator(".tool-call-summary").evaluate(
            "element => getComputedStyle(element).color"
        )
        tool_glyph_color = tool.locator(".tool-glyph").evaluate("element => getComputedStyle(element).color")
        assert tool_name_color != tool_description_color
        assert tool_name_color != tool_glyph_color
        tool.click()
        assert tool.locator("summary .tool-call-summary").is_hidden()
        assert tool.locator(".tool-definition-description").is_visible()
        assert "search_query" in tool.locator(".schema-name").all_inner_texts()
        page.locator('[data-tab="request"]').click()
        search = page.locator("#inspector-filter")
        search.fill("search_query")
        page.wait_for_timeout(150)
        assert "search_query" in page.locator(".json-tree").inner_text()
        search.fill("missing_parameter")
        page.wait_for_timeout(150)
        assert page.locator("#inspector-body .inspector-empty").is_visible()

        browser.close()


def test_tool_catalog_formats_anthropic_openai_responses_and_gemini_schemas(tmp_path: Path) -> None:
    records = [
        {
            "timestamp": "2026-08-20T09:00:00+08:00",
            "request_id": "req_anthropic_tools",
            "turn": 1,
            "request": {
                "method": "POST",
                "path": "/v1/messages",
                "headers": {},
                "body": {
                    "model": "claude-test",
                    "messages": [{"role": "user", "content": "anthropic"}],
                    "tools": [
                        {
                            "name": "anthropic_lookup",
                            "description": "Look up a value.",
                            "input_schema": {
                                "type": "object",
                                "properties": {"query": {"type": "string", "description": "Search query"}},
                                "required": ["query"],
                            },
                        }
                    ],
                },
            },
            "response": {"status": 200, "headers": {}, "body": {}},
        },
        {
            "timestamp": "2026-08-20T09:00:01+08:00",
            "request_id": "req_chat_tools",
            "turn": 2,
            "request": {
                "method": "POST",
                "path": "/v1/chat/completions",
                "headers": {},
                "body": {
                    "model": "gpt-test",
                    "messages": [{"role": "user", "content": "chat"}],
                    "tools": [
                        {
                            "type": "function",
                            "function": {
                                "name": "chat_lookup",
                                "description": "Read a path.",
                                "strict": True,
                                "parameters": {
                                    "type": "object",
                                    "properties": {"path": {"type": "string", "format": "path"}},
                                    "required": ["path"],
                                },
                            },
                        }
                    ],
                },
            },
            "response": {"status": 200, "headers": {}, "body": {}},
        },
        {
            "timestamp": "2026-08-20T09:00:02+08:00",
            "request_id": "req_responses_tools",
            "turn": 3,
            "request": {
                "method": "POST",
                "path": "/v1/responses",
                "headers": {},
                "body": {
                    "model": "gpt-test",
                    "input": [{"type": "message", "role": "user", "content": "responses"}],
                    "tools": [
                        {
                            "type": "function",
                            "name": "responses_lookup",
                            "description": "Return a bounded result.",
                            "parameters": {
                                "type": "object",
                                "properties": {"limit": {"type": "integer", "default": 10}},
                            },
                        },
                        {
                            "type": "custom",
                            "name": "grammar_tool",
                            "format": {"type": "grammar", "syntax": "lark", "definition": "start: WORD"},
                        },
                    ],
                },
            },
            "response": {"status": 200, "headers": {}, "body": {}},
        },
        {
            "timestamp": "2026-08-20T09:00:03+08:00",
            "request_id": "req_gemini_tools",
            "turn": 4,
            "request": {
                "method": "POST",
                "path": "/v1beta/models/gemini-test:generateContent",
                "headers": {},
                "body": {
                    "contents": [{"role": "user", "parts": [{"text": "gemini"}]}],
                    "tools": [
                        {
                            "functionDeclarations": [
                                {
                                    "name": "gemini_lookup",
                                    "description": "Look up a topic.",
                                    "parameters": {
                                        "type": "object",
                                        "properties": {"topic": {"type": "string"}},
                                        "required": ["topic"],
                                    },
                                }
                            ]
                        }
                    ],
                },
            },
            "response": {"status": 200, "headers": {}, "body": {}},
        },
    ]
    trace = tmp_path / "trace_provider_tools.jsonl"
    trace.write_text("\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8")
    output = trace.with_suffix(".html")
    assert render_html(trace, output)

    with playwright.sync_playwright() as runtime:
        try:
            browser = runtime.chromium.launch(headless=True)
        except playwright.Error as exc:
            pytest.skip(f"Playwright Chromium is unavailable: {exc}")
        page = browser.new_page(viewport={"width": 1440, "height": 800})
        page.goto(output.as_uri())
        expectations = [
            ("anthropic_lookup", "query"),
            ("chat_lookup", "path"),
            ("responses_lookup", "limit"),
            ("gemini_lookup", "topic"),
        ]
        for index, (tool_name, parameter_name) in enumerate(expectations):
            page.locator(".trajectory-row").nth(index).click()
            page.locator('#workspace[data-inspector="open"]').wait_for()
            page.locator('[data-tab="tools"]').click()
            tool = page.locator(".tool-definition").filter(has_text=tool_name).first
            tool.wait_for()
            tool.click()
            assert parameter_name in tool.locator(".schema-name").all_inner_texts()
            page.mouse.move(0, 0)
            assert tool.locator(".tool-definition-body").evaluate(
                "element => getComputedStyle(element).backgroundColor"
            ) == tool.locator("summary").evaluate("element => getComputedStyle(element).backgroundColor")
            if tool.locator(".tool-definition-description").count():
                assert (
                    tool.locator(".tool-definition-description").evaluate(
                        "element => getComputedStyle(element).whiteSpace"
                    )
                    == "pre-line"
                )
            assert tool.locator(".schema-heading").evaluate("element => getComputedStyle(element).backgroundColor") != (
                tool.locator(".tool-definition-body").evaluate("element => getComputedStyle(element).backgroundColor")
            )
            assert (
                tool.locator(".schema-name").first.evaluate("element => getComputedStyle(element).whiteSpace")
                == "nowrap"
            )
            if tool.locator(".schema-required").count():
                assert tool.locator(".schema-required").first.evaluate(
                    "element => getComputedStyle(element).color"
                ) != tool.locator(".tool-glyph").first.evaluate("element => getComputedStyle(element).color")
            if tool_name == "chat_lookup":
                assert "strict" in tool.locator("summary").inner_text()
            assert page.locator("#inspector-body").evaluate("element => element.scrollWidth <= element.clientWidth + 1")

        page.locator(".trajectory-row").nth(2).click()
        page.locator('[data-tab="tools"]').click()
        grammar = page.locator(".tool-definition").filter(has_text="grammar_tool").first
        grammar.click()
        assert "grammar" in grammar.locator(".json-tree").inner_text()

        browser.close()


def test_call_ledger_uses_flat_wide_detail_rows_and_compact_metadata_rows(tmp_path: Path) -> None:
    record = {
        "timestamp": "2026-08-20T09:00:00+08:00",
        "request_id": "req_compact_ledger",
        "turn": 1,
        "duration_ms": 4200,
        "request": {
            "method": "POST",
            "path": "/v1/messages",
            "headers": {},
            "body": {
                "model": "claude-test",
                "messages": [{"role": "user", "content": "Inspect the repository thoroughly."}],
            },
        },
        "response": {
            "status": 200,
            "headers": {},
            "body": {
                "content": [
                    {"type": "text", "text": "I will inspect the repository and run focused checks."},
                    {
                        "type": "tool_use",
                        "id": "toolu_bash_1",
                        "name": "Bash",
                        "input": {"description": "List files", "command": "find /very/long/path -maxdepth 3"},
                    },
                    {
                        "type": "tool_use",
                        "id": "toolu_bash_2",
                        "name": "Bash",
                        "input": {"description": "Run tests", "command": "pytest tests -q"},
                    },
                    {
                        "type": "tool_use",
                        "id": "toolu_read",
                        "name": "Read",
                        "input": {"file_path": "/very/long/path/to/source.py"},
                    },
                    {
                        "type": "tool_use",
                        "id": "toolu_agent",
                        "name": "Agent",
                        "input": {"prompt": "Perform a very thorough review."},
                    },
                ],
                "usage": {
                    "input_tokens": 490,
                    "cache_read_input_tokens": 900,
                    "cache_creation_input_tokens": 100,
                    "output_tokens": 321,
                },
            },
        },
    }
    trace = tmp_path / "trace_compact_ledger.jsonl"
    trace.write_text(json.dumps(record) + "\n", encoding="utf-8")
    output = trace.with_suffix(".html")
    assert render_html(trace, output)

    with playwright.sync_playwright() as runtime:
        try:
            browser = runtime.chromium.launch(headless=True)
        except playwright.Error as exc:
            pytest.skip(f"Playwright Chromium is unavailable: {exc}")
        page = browser.new_page(viewport={"width": 1440, "height": 800})
        page.goto(output.as_uri())
        row = page.locator(".trajectory-row")
        row.wait_for()

        assert page.locator("#active-session-facts").inner_text() == ""
        assert page.locator(".call-list-head").is_hidden()
        assert page.locator("#call-panel").get_attribute("data-trajectory-density") == "wide"
        assert row.locator(":scope > .call-entry").count() == 1
        assert row.locator(".call-body").count() == 1
        assert row.locator(".call-meta").count() == 1
        assert row.locator(".call-summary").count() == 1
        assert row.locator(".call-sequence").inner_text() == "#1"
        assert row.locator(".call-clock").get_attribute("datetime") == "2026-08-20T09:00:00+08:00"
        assert len(row.locator(".call-clock-full").inner_text().split(":")) == 3
        assert row.locator(".call-index").evaluate("element => getComputedStyle(element).flexDirection") == "row"
        sequence_box = row.locator(".call-sequence").bounding_box()
        clock_box = row.locator(".call-clock").bounding_box()
        assert sequence_box is not None and clock_box is not None
        assert clock_box["x"] < sequence_box["x"]
        assert row.locator(".call-clock").evaluate(
            "element => parseFloat(getComputedStyle(element).fontSize)"
        ) > row.locator(".call-sequence").evaluate("element => parseFloat(getComputedStyle(element).fontSize)")
        assert row.locator(".activity-kind").count() == 0
        assert row.locator(".call-summary-text").count() == 0
        assert "I will inspect the repository and run focused checks." not in row.inner_text()
        assert row.locator(".row-tool-name").all_inner_texts() == ["Bash", "Bash"]
        assert row.locator(".row-tool-args").all_inner_texts() == ["List files", "Run tests"]
        assert row.locator(".more-tools").count() == 0
        assert row.locator(".call-tool-fact").inner_text() == "4 tools"
        summary = row.locator(".call-summary")
        tool_list = row.locator(".call-tool-list")
        tools = tool_list.locator(".row-tool")
        assert "call-summary--mixed" in (summary.get_attribute("class") or "")
        assert tool_list.evaluate("element => getComputedStyle(element).flexDirection") == "column"
        tool_boxes = [tools.nth(index).bounding_box() for index in range(tools.count())]
        assert all(box is not None for box in tool_boxes)
        assert len(tool_boxes) == 2
        assert tool_boxes[0]["y"] + tool_boxes[0]["height"] <= tool_boxes[1]["y"] + 1
        assert abs(tool_boxes[0]["x"] - tool_boxes[1]["x"]) <= 1
        assert tool_list.locator(":scope > .row-tool").count() == 2
        assert row.locator(".compact-tool-overflow").count() == 0
        tool_list_box = tool_list.bounding_box()
        row_box = row.bounding_box()
        summary_box = summary.bounding_box()
        assert tool_list_box is not None
        assert row_box is not None and summary_box is not None
        assert row.locator(".call-output-line").count() == 0
        assert tool_list_box["width"] == pytest.approx(summary_box["width"], abs=1)
        entry_box = row.locator(".call-entry").bounding_box()
        meta_box = row.locator(".call-meta").bounding_box()
        assert entry_box is not None and meta_box is not None
        assert summary_box["y"] + summary_box["height"] <= meta_box["y"] + 1
        assert meta_box["y"] + meta_box["height"] <= entry_box["y"] + entry_box["height"] + 1
        assert entry_box["x"] == pytest.approx(row_box["x"] + 6, abs=1)
        assert entry_box["width"] == pytest.approx(row_box["width"] - 12, abs=1)
        assert row.locator(".call-entry").evaluate("element => getComputedStyle(element).borderRadius") == "8px"
        assert row.locator(".call-entry").evaluate("element => getComputedStyle(element).marginBlock") == "1px"
        assert row.locator(".call-entry").evaluate("element => getComputedStyle(element).borderLeftWidth") == "0px"
        assert row.locator(".call-entry").evaluate("element => getComputedStyle(element).borderBottomWidth") == "0px"
        assert (
            row.locator(".call-meta").evaluate("element => getComputedStyle(element).backgroundColor")
            == "rgba(0, 0, 0, 0)"
        )
        base_background = row.locator(".call-entry").evaluate("element => getComputedStyle(element).backgroundColor")
        row.hover()
        page.wait_for_timeout(80)
        assert (
            row.locator(".call-entry").evaluate("element => getComputedStyle(element).backgroundColor")
            != base_background
        )
        assert row.locator(".call-activity").evaluate("element => getComputedStyle(element).overflowX") in {
            "clip",
            "hidden",
        }
        assert row.locator(".call-summary").evaluate("element => element.scrollWidth <= element.clientWidth + 1")
        assert row.get_attribute("title") is None
        assert tools.nth(0).get_attribute("title") is None

        summary.hover()
        page.wait_for_timeout(380)
        tooltip = page.locator(".fixed-tooltip--preview")
        tooltip.wait_for()
        tooltip_text = tooltip.inner_text()
        assert tooltip_text.startswith("Bash  List files\nBash  Run tests\n")
        assert "Bash  List files" in tooltip_text
        assert "Read  file_path:" in tooltip_text
        assert "Agent  prompt:" in tooltip_text
        assert tooltip_text.endswith("I will inspect the repository and run focused checks.")
        tooltip_id = tooltip.get_attribute("id")
        assert tooltip_id and tooltip_id in (summary.get_attribute("aria-describedby") or "").split()
        assert tooltip.evaluate("element => getComputedStyle(element).position") == "fixed"
        assert tooltip.evaluate("element => getComputedStyle(element).pointerEvents") == "none"
        tooltip_box = tooltip.bounding_box()
        assert tooltip_box is not None
        assert tooltip_box["width"] <= 440
        assert tooltip_box["x"] >= 12
        assert tooltip_box["x"] + tooltip_box["width"] <= 1440 - 12
        page.mouse.move(8, 8)
        assert tooltip.count() == 0
        assert summary.get_attribute("aria-describedby") is None
        assert "MESSAGE" not in row.inner_text()
        assert "TOOLS" not in row.inner_text()
        assert "command:" not in row.inner_text()
        assert "/very/long/path" not in row.inner_text()
        assert row.locator(".call-status").count() == 0
        assert row.locator(".call-cache").inner_text() == "60% cached"
        assert row.locator(".call-usage").evaluate("element => getComputedStyle(element).whiteSpace") == "nowrap"
        assert row.locator(".call-model").evaluate(
            "element => parseFloat(getComputedStyle(element).fontSize)"
        ) > row.locator(".call-usage").evaluate("element => parseFloat(getComputedStyle(element).fontSize)")
        assert row.locator(".call-model").inner_text() == "claude-test"
        assert row.locator(".usage-item span").all_inner_texts() == ["1.5k", "input", "321", "output"]
        row.locator(".call-usage").hover()
        page.wait_for_timeout(380)
        usage_tooltip = page.locator(".fixed-tooltip--preview")
        usage_tooltip.wait_for()
        assert usage_tooltip.inner_text() == "Fresh input  490\nCache read   900\nCache write  100\nOutput       321"
        page.mouse.move(8, 8)
        assert usage_tooltip.count() == 0
        assert row.locator(".call-duration").inner_text() == "4.2s"
        assert row.bounding_box()["height"] == 64
        assert row.evaluate("element => element.scrollWidth <= element.clientWidth + 1")

        page.set_viewport_size({"width": 390, "height": 844})
        page.wait_for_function("document.querySelector('#call-panel').dataset.trajectoryDensity === 'compact'")
        page.wait_for_function("document.querySelector('.trajectory-row').getBoundingClientRect().height === 52")
        assert page.locator("#call-panel").get_attribute("data-trajectory-density") == "compact"
        assert row.locator(".call-sequence").is_visible()
        assert not row.locator(".call-activity").is_visible()
        assert not row.locator(".row-tool").nth(0).is_visible()
        assert not row.locator(".call-output-line").is_visible()
        assert row.locator(".call-model-cell").is_visible()
        assert row.locator(".call-usage").is_visible()
        assert row.locator(".call-timing").is_visible()
        assert row.locator(".usage-item span").all_inner_texts() == ["1.5k", "input", "321", "output"]
        assert row.locator(".call-tool-fact").inner_text() == "4 tools"
        assert row.locator(".call-duration").inner_text() == "4.2s"
        assert row.bounding_box()["height"] == 52
        compact_entry = row.locator(".call-entry")
        compact_meta = row.locator(".call-meta")
        row_box = row.bounding_box()
        entry_box = compact_entry.bounding_box()
        assert row_box is not None and entry_box is not None
        assert entry_box["x"] == pytest.approx(row_box["x"] + 4, abs=1)
        assert entry_box["width"] == pytest.approx(row_box["width"] - 8, abs=1)
        assert compact_entry.evaluate("element => element.scrollWidth <= element.clientWidth + 1")
        assert compact_meta.evaluate("element => element.scrollWidth <= element.clientWidth + 1")
        assert compact_entry.evaluate("element => getComputedStyle(element).borderRadius") == "7px"
        assert compact_entry.evaluate("element => getComputedStyle(element).marginBlock") == "1px"
        assert compact_entry.evaluate("element => getComputedStyle(element).borderLeftWidth") == "0px"
        assert compact_entry.evaluate("element => getComputedStyle(element).borderBottomWidth") == "0px"
        assert compact_meta.evaluate("element => getComputedStyle(element).backgroundColor") == "rgba(0, 0, 0, 0)"
        assert row.evaluate("element => element.scrollWidth <= element.clientWidth + 1")
        assert page.evaluate("document.documentElement.scrollWidth === innerWidth")

        browser.close()


def test_call_rows_use_centered_activity_states_without_empty_rails(tmp_path: Path) -> None:
    def record(turn: int, content: list[dict[str, object]]) -> dict[str, object]:
        return {
            "timestamp": f"2026-08-20T09:00:0{turn}+08:00",
            "request_id": f"req_row_{turn}",
            "turn": turn,
            "duration_ms": 1000 + turn,
            "request": {
                "method": "POST",
                "path": "/v1/messages",
                "headers": {},
                "body": {"model": "claude-test", "messages": [{"role": "user", "content": "run"}]},
            },
            "response": {
                "status": 200,
                "headers": {},
                "body": {
                    "content": content,
                    "usage": {"input_tokens": 1200 + turn, "output_tokens": 20 + turn},
                },
            },
        }

    records = [
        record(
            1,
            [
                {"type": "text", "text": "I will inspect the files."},
                {"type": "tool_use", "id": "toolu_read", "name": "Read", "input": {"file_path": "/tmp/a"}},
                {"type": "tool_use", "id": "toolu_bash_mixed", "name": "Bash", "input": {"command": "pwd"}},
            ],
        ),
        record(
            2,
            [
                {"type": "tool_use", "id": "toolu_bash", "name": "Bash", "input": {"command": "pwd"}},
                {"type": "tool_use", "id": "toolu_glob", "name": "Glob", "input": {"pattern": "**/*.py"}},
            ],
        ),
        record(3, [{"type": "text", "text": "Inspection complete."}]),
        record(4, []),
    ]
    trace = tmp_path / "trace_stable_rows.jsonl"
    trace.write_text("\n".join(json.dumps(item) for item in records) + "\n", encoding="utf-8")
    output = trace.with_suffix(".html")
    assert render_html(trace, output)

    with playwright.sync_playwright() as runtime:
        try:
            browser = runtime.chromium.launch(headless=True)
        except playwright.Error as exc:
            pytest.skip(f"Playwright Chromium is unavailable: {exc}")
        page = browser.new_page(viewport={"width": 1440, "height": 800})
        page.goto(output.as_uri())
        rows = page.locator(".trajectory-row")
        rows.nth(3).wait_for()
        assert rows.count() == 4
        assert rows.evaluate_all("elements => elements.map(element => element.getBoundingClientRect().height)") == [
            64,
            64,
            64,
            64,
        ]

        mixed = rows.nth(0)
        assert "call-summary--mixed" in (mixed.locator(".call-summary").get_attribute("class") or "")
        assert mixed.locator(".call-output-line").count() == 0
        assert mixed.locator(".call-summary-text").count() == 0
        assert mixed.locator(".call-tool-list").count() == 1
        assert mixed.locator(".call-summary > *").evaluate_all(
            "elements => elements.map(element => element.className)"
        ) == ["call-tool-list"]
        mixed_tool_list = mixed.locator(".call-tool-list")
        mixed_tools = mixed_tool_list.locator(".row-tool")
        assert mixed_tool_list.evaluate("element => getComputedStyle(element).flexDirection") == "column"
        assert mixed_tools.count() == 2
        mixed_tool_boxes = [mixed_tools.nth(index).bounding_box() for index in range(mixed_tools.count())]
        assert all(box is not None for box in mixed_tool_boxes)
        assert mixed_tool_boxes[0]["y"] + mixed_tool_boxes[0]["height"] <= mixed_tool_boxes[1]["y"] + 1
        assert abs(mixed_tool_boxes[0]["x"] - mixed_tool_boxes[1]["x"]) <= 1
        mixed_tool_list_box = mixed_tool_list.bounding_box()
        assert mixed_tool_list_box is not None
        mixed.locator(".call-summary").hover()
        page.wait_for_timeout(380)
        assert page.locator(".fixed-tooltip--preview").inner_text() == (
            "Read  file_path: /tmp/a\nBash  command: pwd\nI will inspect the files."
        )
        page.mouse.move(8, 8)
        assert page.locator(".fixed-tooltip--preview").count() == 0

        tool_only = rows.nth(1)
        assert "call-summary--tools" in (tool_only.locator(".call-summary").get_attribute("class") or "")
        assert tool_only.locator(".call-output-line").count() == 0
        assert tool_only.locator(".call-tool-list").count() == 1
        assert tool_only.locator(".row-tool-name").all_inner_texts() == ["Bash", "Glob"]
        tool_only_list = tool_only.locator(".call-tool-list")
        assert tool_only_list.evaluate("element => getComputedStyle(element).flexDirection") == "column"
        tool_only_boxes = [
            tool_only.locator(".row-tool").nth(index).bounding_box()
            for index in range(tool_only.locator(".row-tool").count())
        ]
        assert all(box is not None for box in tool_only_boxes)
        assert tool_only_boxes[0]["y"] + tool_only_boxes[0]["height"] <= tool_only_boxes[1]["y"] + 1
        assert abs(tool_only_boxes[0]["x"] - tool_only_boxes[1]["x"]) <= 1
        assert "tool call" not in tool_only.locator(".call-summary").inner_text().lower()
        assert "No model output" not in tool_only.inner_text()

        text_only = rows.nth(2)
        assert "call-summary--text" in (text_only.locator(".call-summary").get_attribute("class") or "")
        assert text_only.locator(".call-output-line").count() == 1
        assert text_only.locator(".call-tool-list").count() == 0

        empty = rows.nth(3)
        assert "call-summary--empty" in (empty.locator(".call-summary").get_attribute("class") or "")
        assert empty.locator(".call-output-line").count() == 0
        assert empty.locator(".call-tool-list").count() == 0
        assert empty.locator(".call-empty-mark").inner_text() == "—"

        for row in [mixed, tool_only, text_only, empty]:
            entry_box = row.locator(".call-entry").bounding_box()
            meta_box = row.locator(".call-meta").bounding_box()
            summary_box = row.locator(".call-summary").bounding_box()
            assert entry_box is not None and meta_box is not None and summary_box is not None
            assert summary_box["y"] + summary_box["height"] <= meta_box["y"] + 1
            assert meta_box["y"] + meta_box["height"] <= entry_box["y"] + entry_box["height"] + 1
            assert row.evaluate("element => element.scrollWidth <= element.clientWidth + 1")
            assert row.locator(".call-status").count() == 0

        browser.close()


def test_stream_error_keeps_request_context_and_uses_quiet_response_status(tmp_path: Path) -> None:
    record = {
        "timestamp": "2026-08-20T09:00:00+08:00",
        "request_id": "req_stream_error",
        "turn": 1,
        "duration_ms": 4500,
        "request": {
            "method": "POST",
            "path": "/v1/messages",
            "headers": {},
            "body": {"model": "claude-test", "messages": [{"role": "user", "content": "hello"}]},
        },
        "response": {
            "status": 200,
            "headers": {"content-type": "text/event-stream"},
            "body": None,
            "sse_events": [
                {
                    "event": "error",
                    "data": {
                        "type": "error",
                        "error": {"type": "overloaded_error", "message": "Overloaded"},
                    },
                }
            ],
        },
    }
    http_error_record = {
        "timestamp": "2026-08-20T09:00:01+08:00",
        "request_id": "req_http_error",
        "turn": 2,
        "duration_ms": 8100,
        "request": {
            "method": "POST",
            "path": "/v1/messages",
            "headers": {},
            "body": {
                "model": "claude-test",
                "messages": [{"role": "user", "content": "keep this request visible"}],
            },
        },
        "response": {
            "status": 529,
            "headers": {"content-type": "application/json"},
            "body": {
                "type": "error",
                "error": {"type": "overloaded_error", "message": "Overloaded"},
            },
        },
    }
    trace = tmp_path / "trace_stream_error.jsonl"
    trace.write_text(
        "\n".join(json.dumps(item) for item in [record, http_error_record]) + "\n",
        encoding="utf-8",
    )
    output = trace.with_suffix(".html")
    assert render_html(trace, output)

    with playwright.sync_playwright() as runtime:
        try:
            browser = runtime.chromium.launch(headless=True)
        except playwright.Error as exc:
            pytest.skip(f"Playwright Chromium is unavailable: {exc}")
        page = browser.new_page(viewport={"width": 1440, "height": 800})
        page.add_init_script("localStorage.setItem('claude-tap-language', 'zh')")
        page.goto(output.as_uri())
        rows = page.locator(".trajectory-row")
        row = rows.nth(0)
        row.wait_for()
        assert "failed" in (row.get_attribute("class") or "")
        assert row.locator(".activity-kind").count() == 0
        assert row.locator(".call-summary-text").inner_text() == "Overloaded"
        assert row.locator(".call-error-note").count() == 0
        assert "hello" not in row.inner_text()
        assert "Overloaded" in row.inner_text()
        assert "Empty model response" not in row.inner_text()
        assert row.locator(".call-status").inner_text() == "OVERLOADED"
        assert "warning" in (row.locator(".call-status").get_attribute("class") or "")
        assert "danger" not in (row.locator(".call-status").get_attribute("class") or "")
        assert row.bounding_box()["height"] == 64
        assert row.evaluate("element => getComputedStyle(element, '::before').content") in {"none", "normal"}

        row.click()
        page.wait_for_function("document.querySelector('.message-role')?.textContent === 'User'")
        assert page.locator(".request-message").count() == 1
        assert page.locator(".request-message").first.get_attribute("data-role") == "user"
        assert page.locator(".message-role").first.text_content() == "User"
        assert page.locator('.request-message-group[data-origin="output"]').count() == 0
        notice = page.locator(".request-error-notice")
        assert notice.get_attribute("role") == "status"
        assert notice.locator(".response-note-label").inner_text() == "RESPONSE"
        assert notice.locator(".response-note-status").inner_text() == "SSE error"
        assert notice.locator(".response-note-message").inner_text() == "Overloaded"
        assert notice.evaluate(
            "element => getComputedStyle(element).backgroundColor !== getComputedStyle(document.documentElement).getPropertyValue('--error-soft').trim()"
        )
        assert "HTTP 200" not in page.locator("#inspector-facts").inner_text()
        assert page.locator("#inspector-facts").inner_text().endswith("SSE error")

        http_row = rows.nth(1)
        assert http_row.locator(".activity-kind").count() == 0
        assert http_row.locator(".call-summary-text").inner_text() == "Overloaded"
        assert http_row.locator(".call-error-note").count() == 0
        assert "keep this request visible" not in http_row.inner_text()
        assert http_row.locator(".call-status").inner_text() == "OVERLOADED"
        assert "warning" in (http_row.locator(".call-status").get_attribute("class") or "")
        http_row.click()
        page.wait_for_function("document.querySelector('#inspector-title').textContent.includes('#2')")
        page.wait_for_function(
            "document.querySelector('.request-message')?.innerText.includes('keep this request visible')"
        )
        assert page.locator(".request-message").count() == 2
        assert "keep this request visible" in page.locator('.request-message:not([data-origin="output"])').inner_text()
        http_output = page.locator('.request-message-group[data-origin="output"]')
        assert http_output.locator('.request-message[data-block-type="response_error"]').count() == 1
        assert "overloaded_error" in http_output.inner_text()
        http_notice = page.locator(".request-error-notice")
        assert http_notice.locator(".response-note-status").inner_text() == "HTTP 529"
        assert http_notice.locator(".response-note-message").inner_text() == "Overloaded"
        assert page.locator("#inspector-facts").inner_text().endswith("HTTP 529")

        browser.close()


def test_switching_calls_inherits_then_restores_each_calls_position(tmp_path: Path) -> None:
    long_messages = [
        {"role": "user", "content": "\n".join(f"first request line {index}" for index in range(120))},
        {"role": "assistant", "content": "first response context"},
        {"role": "user", "content": "\n".join(f"first continuation {index}" for index in range(120))},
    ]

    def record(turn: int, label: str) -> dict[str, object]:
        return {
            "timestamp": f"2026-08-20T09:00:0{turn}+08:00",
            "request_id": f"req_scroll_{turn}",
            "turn": turn,
            "duration_ms": 42,
            "request": {
                "method": "POST",
                "path": "/v1/messages",
                "headers": {},
                "body": {
                    "model": "claude-test",
                    "messages": [
                        *long_messages,
                        {"role": "user", "content": "\n".join(f"{label} line {index}" for index in range(120))},
                        *[
                            {"role": "assistant", "content": f"{label} fixed-card context {index}"}
                            for index in range(4)
                        ],
                    ],
                },
            },
            "response": {"status": 200, "headers": {}, "body": {"content": [{"type": "text", "text": label}]}},
        }

    trace = tmp_path / "trace_scroll_inheritance.jsonl"
    trace.write_text("\n".join(json.dumps(record(index, label)) for index, label in [(1, "alpha"), (2, "beta")]) + "\n")
    output = trace.with_suffix(".html")
    assert render_html(trace, output)

    with playwright.sync_playwright() as runtime:
        try:
            browser = runtime.chromium.launch(headless=True)
        except playwright.Error as exc:
            pytest.skip(f"Playwright Chromium is unavailable: {exc}")
        page = browser.new_page(viewport={"width": 1440, "height": 800})
        page.goto(output.as_uri())
        rows = page.locator(".trajectory-row")
        rows.nth(0).click()
        page.locator('#workspace[data-inspector="open"]').wait_for()
        body = page.locator("#inspector-body")
        page.wait_for_function(
            "document.querySelector('#inspector-body').scrollHeight > document.querySelector('#inspector-body').clientHeight + 300"
        )
        body.evaluate("element => { element.scrollTop = 320; }")
        first_top = body.evaluate("element => element.scrollTop")
        assert first_top >= 300
        first_nested = page.locator(".message-text-block.long-content").first
        assert first_nested.evaluate("element => element.scrollHeight > element.clientHeight")
        first_nested.evaluate("element => { element.scrollTop = 96; }")
        assert first_nested.evaluate("element => element.scrollTop") == 96

        page.locator("#inspector-options-trigger").click()
        page.locator('#inspector-options-menu [data-menu-id="toggle-wrap"]').click()
        page.wait_for_function(
            "expected => Math.abs(document.querySelector('#inspector-body').scrollTop - expected) <= 2",
            arg=first_top,
        )
        assert page.locator(".request-message-body").evaluate_all(
            "elements => elements.every(element => element.scrollTop === 0)"
        )

        rows.nth(1).click()
        page.wait_for_function("document.querySelector('#inspector-title').textContent.includes('#2')")
        page.wait_for_function(
            "expected => Math.abs(document.querySelector('#inspector-body').scrollTop - expected) <= 2",
            arg=first_top,
        )
        page.wait_for_function("document.querySelector('.message-text-block.long-content').scrollTop === 96")

        body.evaluate("element => { element.scrollTop = 140; }")
        page.locator(".message-text-block.long-content").first.evaluate("element => { element.scrollTop = 48; }")
        rows.nth(0).click()
        page.wait_for_function("document.querySelector('#inspector-title').textContent.includes('#1')")
        page.wait_for_function(
            "expected => Math.abs(document.querySelector('#inspector-body').scrollTop - expected) <= 2",
            arg=first_top,
        )
        page.wait_for_function("document.querySelector('.message-text-block.long-content').scrollTop === 96")

        rows.nth(1).click()
        page.wait_for_function("document.querySelector('#inspector-title').textContent.includes('#2')")
        page.wait_for_function("Math.abs(document.querySelector('#inspector-body').scrollTop - 140) <= 2")
        page.wait_for_function("document.querySelector('.message-text-block.long-content').scrollTop === 48")

        browser.close()


def test_short_call_does_not_erase_shared_inspector_scroll_position(tmp_path: Path) -> None:
    def record(turn: int, lines: int) -> dict[str, object]:
        return {
            "timestamp": f"2026-08-20T10:00:0{turn}+08:00",
            "request_id": f"req_scroll_short_{turn}",
            "turn": turn,
            "duration_ms": 42,
            "request": {
                "method": "POST",
                "path": "/v1/messages",
                "headers": {},
                "body": {
                    "model": "claude-test",
                    "messages": [
                        {
                            "role": "user",
                            "content": "\n".join(f"shared prefix line {index}" for index in range(lines)),
                        },
                        *[
                            {"role": "assistant", "content": f"context block {index}"}
                            for index in range(8 if lines > 10 else 0)
                        ],
                    ],
                },
            },
            "response": {"status": 200, "headers": {}, "body": {"content": [{"type": "text", "text": "ok"}]}},
        }

    trace = tmp_path / "trace_scroll_short_bridge.jsonl"
    trace.write_text(
        "\n".join(json.dumps(record(turn, lines)) for turn, lines in [(1, 160), (2, 1), (3, 160)]) + "\n",
        encoding="utf-8",
    )
    output = trace.with_suffix(".html")
    assert render_html(trace, output)

    with playwright.sync_playwright() as runtime:
        try:
            browser = runtime.chromium.launch(headless=True)
        except playwright.Error as exc:
            pytest.skip(f"Playwright Chromium is unavailable: {exc}")
        page = browser.new_page(viewport={"width": 1440, "height": 800})
        page.goto(output.as_uri())
        rows = page.locator(".trajectory-row")
        rows.nth(0).click()
        page.locator('#workspace[data-inspector="open"]').wait_for()
        page.wait_for_function(
            "document.querySelector('#inspector-body').scrollHeight > document.querySelector('#inspector-body').clientHeight + 300"
        )
        body = page.locator("#inspector-body")
        body.evaluate("element => { element.scrollTop = 320; }")
        nested = page.locator(".message-text-block.long-content").first
        nested.evaluate("element => { element.scrollTop = 88; }")

        rows.nth(1).click()
        page.wait_for_function("document.querySelector('#inspector-title').textContent.includes('#2')")
        assert body.evaluate("element => element.scrollTop") < 320

        rows.nth(2).click()
        page.wait_for_function("document.querySelector('#inspector-title').textContent.includes('#3')")
        page.wait_for_function("Math.abs(document.querySelector('#inspector-body').scrollTop - 320) <= 2")
        page.wait_for_function("document.querySelector('.message-text-block.long-content').scrollTop === 88")

        browser.close()


def test_request_preview_preserves_message_and_content_block_order(tmp_path: Path) -> None:
    long_text = "\n".join(f"line {index}" for index in range(30))
    record = {
        "timestamp": "2026-08-20T09:00:00+08:00",
        "request_id": "req_ordered_preview",
        "turn": 1,
        "duration_ms": 42,
        "request": {
            "method": "POST",
            "path": "/v1/messages",
            "headers": {},
            "body": {
                "model": "claude-test",
                "messages": [
                    {"role": "system", "content": "system-only-message"},
                    {"role": "user", "content": long_text},
                    {
                        "role": "assistant",
                        "content": [
                            {"type": "thinking", "thinking": "", "signature": "hidden-signature"},
                            {"type": "thinking", "thinking": "assistant-thinking"},
                            {"type": "text", "text": "assistant-before-tool"},
                            {
                                "type": "tool_use",
                                "id": "toolu_order",
                                "name": "Bash",
                                "input": {"command": "printf ordered", "items": list(range(200))},
                            },
                        ],
                    },
                    {
                        "role": "user",
                        "content": [
                            {"type": "tool_result", "tool_use_id": "toolu_order", "content": "ordered-result"},
                            {"type": "text", "text": "user-after-result"},
                        ],
                    },
                ],
            },
        },
        "response": {
            "status": 200,
            "headers": {},
            "body": {"content": [{"type": "text", "text": "model-response"}]},
        },
    }
    trace = tmp_path / "trace_ordered_preview.jsonl"
    trace.write_text(json.dumps(record) + "\n", encoding="utf-8")
    output = trace.with_suffix(".html")
    assert render_html(trace, output)

    with playwright.sync_playwright() as runtime:
        try:
            browser = runtime.chromium.launch(headless=True)
        except playwright.Error as exc:
            pytest.skip(f"Playwright Chromium is unavailable: {exc}")
        page = browser.new_page(viewport={"width": 1440, "height": 800})
        page.goto(output.as_uri())
        page.locator(".trajectory-row").first.click()
        page.locator('#workspace[data-inspector="open"]').wait_for()

        assert round(page.locator(".inspector-header").bounding_box()["height"]) == 42
        assert round(page.locator(".inspector-tabs").bounding_box()["height"]) == 34
        assert page.locator("#copy-active-view").is_hidden()

        messages = page.locator(".request-message")
        assert messages.count() == 8
        assert [messages.nth(index).get_attribute("data-role") for index in range(8)] == [
            "system",
            "user",
            "assistant",
            "assistant",
            "assistant",
            "user",
            "user",
            "assistant",
        ]
        assert [messages.nth(index).get_attribute("data-block-type") for index in range(8)] == [
            "text",
            "text",
            "thinking",
            "text",
            "tool_use",
            "tool_result",
            "text",
            "text",
        ]
        assert [messages.nth(index).get_attribute("data-block-path") for index in range(8)] == [
            "content.0",
            "content.0",
            "content.1",
            "content.2",
            "content.3",
            "content.0",
            "content.1",
            "content.0",
        ]
        groups = page.locator(".request-message-group")
        assert groups.count() == 5
        assert [groups.nth(index).get_attribute("data-role") for index in range(5)] == [
            "system",
            "user",
            "assistant",
            "user",
            "assistant",
        ]
        assert groups.locator(".request-message").count() == 8
        assert [groups.nth(index).locator(".request-message").count() for index in range(5)] == [1, 1, 3, 2, 1]
        assert groups.nth(2).locator(".message-role").text_content() == "Assistant"
        assert groups.nth(2).locator(".message-type").text_content() == "thinking · text · tool_use"
        assert groups.nth(3).locator(".message-type").text_content() == "tool_result · text"
        _assert_message_headers_accessible_and_contained(page)
        _assert_message_groups_do_not_overlap(page)
        _assert_only_extreme_payloads_own_nested_vertical_scroll(page)
        assert messages.locator(".request-message-body").evaluate_all(
            "elements => elements.every(element => getComputedStyle(element).overflowY === 'visible')"
        )
        assert messages.evaluate_all(
            "elements => elements.every(element => element.querySelectorAll(':scope > .request-message-body > .message-block').length === 1)"
        )
        assert "system-only-message" in page.locator("#inspector-body").inner_text()
        assert page.locator(".flow-boundary").count() == 0
        assert page.locator(".message-index").count() == 0
        response_group = page.locator('.request-message-group[data-origin="output"]')
        assert response_group.count() == 1
        assert response_group.evaluate(
            "element => element === document.querySelector('.request-message-group:last-child')"
        )
        assert response_group.locator('.request-message[data-origin="output"]').count() == 1
        assert response_group.locator(".message-content").text_content() == "model-response"

        thinking_block = messages.nth(2).locator(".request-message-body > *")
        assert thinking_block.get_attribute("data-content-type") == "thinking"
        assert "message-thinking-block" in (thinking_block.get_attribute("class") or "")
        assert thinking_block.evaluate("element => element.tagName") == "SECTION"
        assert messages.nth(2).locator('[data-content-type="thinking"]').count() == 1
        assert "hidden-signature" not in (messages.nth(2).text_content() or "")
        assert messages.nth(3).locator(".request-message-body > *").get_attribute("data-content-type") == "text"
        tool_message = messages.nth(4)
        assert tool_message.get_attribute("data-call-id") == "toolu_order"
        tool_block = tool_message.locator(".request-message-body > *")
        assert "tool-call" in (tool_block.get_attribute("class") or "")
        assert tool_block.evaluate("element => element.tagName") == "SECTION"
        assert tool_block.get_attribute("data-call-id") == "toolu_order"
        assert tool_block.locator(".tool-call-id").text_content() == "toolu_order"
        assert tool_block.locator(".tool-phase").text_content() == "CALL"
        assert tool_block.locator("summary, .tool-chevron").count() == 0
        assert tool_block.locator(".tool-call-summary").count() == 0

        tool_tree = tool_block.locator(".inline-json-tree")
        assert tool_tree.count() == 1
        assert tool_block.locator(".message-tool-text").count() == 0
        assert tool_tree.locator(".json-expanded-top-level").count() == 1
        assert "wrap-lines" in (tool_tree.locator(".json-tree").get_attribute("class") or "")
        tool_value = tool_tree.evaluate("element => element.__claudeTapJsonTree.value")
        assert list(tool_value) == ["command", "items"]
        assert tool_value["command"] == "printf ordered"
        assert tool_value["items"] == list(range(200))
        assert tool_block.locator(".tool-format-badge").text_content() == "JSON"
        tool_data = tool_block.locator(".message-tool-data")
        assert tool_data.locator(".nested-scroll").count() == 0
        assert "long-content" not in (tool_block.get_attribute("class") or "")
        assert tool_data.evaluate("element => getComputedStyle(element).overflowY") == "visible"
        assert tool_data.evaluate("element => element.scrollHeight <= element.clientHeight + 1")

        result_message = messages.nth(5)
        assert result_message.get_attribute("data-call-id") == "toolu_order"
        result_block = result_message.locator(".request-message-body > *")
        assert "tool-result" in (result_block.get_attribute("class") or "")
        assert result_block.get_attribute("data-call-id") == "toolu_order"
        assert result_block.locator(".tool-call-id").text_content() == "toolu_order"
        assert result_block.locator(".tool-phase").text_content() == "RESULT"
        assert result_block.locator(".inline-json-tree").count() == 0
        assert result_block.locator(".message-tool-text").inner_text() == "ordered-result"
        assert messages.nth(6).locator(".request-message-body > *").get_attribute("data-content-type") == "text"

        medium_message_body = messages.nth(1).locator(".request-message-body")
        assert medium_message_body.locator(".long-content").count() == 0
        assert medium_message_body.evaluate("element => getComputedStyle(element).overflowY") == "visible"

        page.locator('[data-tab="system"]').click()
        assert "system-only-message" in page.locator("#inspector-body").inner_text()
        assert page.locator(".system-prompt").count() == 1
        assert page.locator(".system-entry").count() == 0
        assert page.locator(".system-prompt .nested-scroll").count() == 0

        page.locator('[data-tab="request"]').click()
        tree = page.locator(".json-tree")
        tree.wait_for()
        assert tree.locator(".json-root-row .json-expander").count() == 0
        assert tree.locator(".json-root-children").is_visible()
        messages_row = tree.locator(".json-row").filter(has_text="messages:").first
        expander = messages_row.locator(".json-expander")
        assert expander.get_attribute("aria-expanded") == "false"
        assert (
            messages_row.locator(".json-label").evaluate("element => getComputedStyle(element).whiteSpace") == "nowrap"
        )
        assert messages_row.locator("xpath=following-sibling::*[1][contains(@class, 'json-children')]").count() == 0
        messages_row.hover()
        assert tree.locator("[data-json-copy-active]").count() == 1
        copy_button = tree.locator(".json-copy")
        assert copy_button.is_visible()
        assert float(copy_button.evaluate("element => getComputedStyle(element).opacity")) > 0
        assert copy_button.bounding_box()["x"] > tree.bounding_box()["x"] + tree.bounding_box()["width"] / 2
        copy_button.click()
        page.wait_for_function("document.querySelector('.json-copy[data-state=\"copied\"]') !== null")
        messages_row.locator(".json-preview").click()
        assert messages_row.locator(".json-expander").get_attribute("aria-expanded") == "false"
        messages_row.locator(".json-clickable-label").click()
        messages_row = tree.locator(".json-row").filter(has_text="messages:").first
        assert messages_row.locator(".json-expander").get_attribute("aria-expanded") == "true"
        assert messages_row.locator("xpath=following-sibling::*[1][contains(@class, 'json-children')]").count() == 1
        messages_row.locator(".json-expander").click()
        messages_row = tree.locator(".json-row").filter(has_text="messages:").first
        assert messages_row.locator(".json-expander").get_attribute("aria-expanded") == "false"
        messages_row.locator(".json-expander").focus()
        page.keyboard.press("ArrowRight")
        messages_row = tree.locator(".json-row").filter(has_text="messages:").first
        assert messages_row.locator(".json-expander").get_attribute("aria-expanded") == "true"
        assert tree.locator('[role="treeitem"] [tabindex="0"]').count() == 1
        focused_before = page.evaluate(
            "document.activeElement?.dataset.jsonPath || document.activeElement?.dataset.jsonNode"
        )
        page.keyboard.press("ArrowDown")
        focused_after = page.evaluate(
            "document.activeElement?.dataset.jsonPath || document.activeElement?.dataset.jsonNode"
        )
        assert focused_after and focused_after != focused_before
        messages_row.hover()
        copy_button.click(button="right")
        menu = page.locator(".json-context-menu")
        menu.wait_for(state="visible")
        assert menu.locator('[role="menuitem"]').count() == 3
        assert "copy" in menu.inner_text().lower()
        assert "path" in menu.inner_text().lower()
        page.keyboard.press("Escape")
        assert menu.is_hidden()
        assert tree.locator(".json-close").count() == 1
        tree_box = tree.bounding_box()
        inspector_box = page.locator("#inspector-body").bounding_box()
        assert tree_box is not None and inspector_box is not None
        assert tree_box["x"] >= inspector_box["x"] - 1
        assert tree_box["x"] + tree_box["width"] <= inspector_box["x"] + inspector_box["width"] + 1
        assert tree.evaluate("element => getComputedStyle(element).overflowX") in {"auto", "scroll"}
        assert page.locator("#inspector-body").evaluate("element => element.scrollWidth <= element.clientWidth + 1")
        assert page.evaluate("document.documentElement.scrollWidth === innerWidth")
        page.locator("#inspector-filter").fill("assistant-before-tool")
        page.wait_for_timeout(120)
        assert "assistant-before-tool" in tree.inner_text()

        page.set_viewport_size({"width": 820, "height": 800})
        page.locator('[data-tab="conversation"]').click()
        compact_inspector = page.locator("#inspector-body")
        assert compact_inspector.bounding_box()["width"] >= 350
        assert compact_inspector.evaluate("element => element.scrollWidth <= element.clientWidth + 1")
        compact_tool = page.locator(".message-tool-data").first
        compact_tool.scroll_into_view_if_needed()
        assert compact_tool.evaluate("element => getComputedStyle(element).overflowX") in {"clip", "hidden"}
        assert compact_tool.evaluate("element => element.scrollWidth <= element.clientWidth + 1")
        assert compact_tool.bounding_box()["width"] <= compact_inspector.bounding_box()["width"]
        assert page.evaluate("document.documentElement.scrollWidth === innerWidth")

        page.set_viewport_size({"width": 1440, "height": 800})
        page.wait_for_function("document.querySelector('#call-panel').dataset.trajectoryDensity === 'wide'")
        row_box = page.locator(".trajectory-row").first.bounding_box()
        assert row_box is not None
        assert row_box["height"] == 64
        assert page.locator(".trajectory-row").first.get_attribute("data-record-index") == "0"
        assert page.locator(".trajectory-row").first.get_attribute("aria-current") == "true"

        page.set_viewport_size({"width": 320, "height": 568})
        assert page.locator(".inspector").bounding_box()["width"] == 320
        _assert_message_headers_accessible_and_contained(page)
        _assert_message_groups_do_not_overlap(page)
        tabs = page.locator("#inspector-tabs")
        assert tabs.evaluate("element => element.scrollWidth <= element.clientWidth + 1")
        assert messages.nth(1).bounding_box()["height"] > messages.nth(3).bounding_box()["height"]
        assert messages.evaluate_all(
            "elements => elements.every(element => element.scrollWidth <= element.clientWidth + 1)"
        )
        assert messages.locator(".request-message-body").evaluate_all(
            "elements => elements.every(element => element.scrollWidth <= element.clientWidth + 1)"
        )
        assert page.locator("#call-panel").evaluate("element => element.inert")
        assert not page.locator("#inspector").evaluate("element => element.inert")
        assert page.evaluate("document.documentElement.scrollWidth === innerWidth")

        page.locator("#close-inspector").click()
        assert page.locator("#inspector").evaluate("element => element.inert")
        assert not page.locator("#call-panel").evaluate("element => element.inert")

        browser.close()


def test_concurrent_tools_share_one_message_surface_and_response_has_a_boundary(tmp_path: Path) -> None:
    record = {
        "timestamp": "2026-08-25T21:00:00+08:00",
        "request_id": "req_concurrent_tools",
        "turn": 1,
        "duration_ms": 42,
        "request": {
            "method": "POST",
            "path": "/v1/messages",
            "headers": {},
            "body": {
                "model": "claude-test",
                "messages": [
                    {
                        "role": "assistant",
                        "content": [
                            {"type": "text", "text": "Run the checks in parallel."},
                            {
                                "type": "tool_use",
                                "id": "toolu_parallel_1",
                                "name": "Bash",
                                "input": {"command": "uv run ruff check ."},
                            },
                            {
                                "type": "tool_use",
                                "id": "toolu_parallel_2",
                                "name": "Bash",
                                "input": {"command": "uv run pytest -q"},
                            },
                            {
                                "type": "tool_use",
                                "id": "toolu_parallel_3",
                                "name": "Read",
                                "input": {"file_path": "MAINTAINING.md"},
                            },
                        ],
                    }
                ],
            },
        },
        "response": {
            "status": 200,
            "headers": {},
            "body": {
                "role": "assistant",
                "content": [{"type": "text", "text": "The checks are running."}],
            },
        },
    }
    trace = tmp_path / "trace_concurrent_tools.jsonl"
    trace.write_text(json.dumps(record) + "\n", encoding="utf-8")
    output = trace.with_suffix(".html")
    assert render_html(trace, output)

    with playwright.sync_playwright() as runtime:
        try:
            browser = runtime.chromium.launch(headless=True)
        except playwright.Error as exc:
            pytest.skip(f"Playwright Chromium is unavailable: {exc}")
        page = browser.new_page(viewport={"width": 1080, "height": 760})
        page.goto(output.as_uri())
        page.locator(".trajectory-row").click()
        page.locator('#workspace[data-inspector="open"]').wait_for()

        request_group = page.locator(
            '.request-message-group[data-origin="request"][data-role="assistant"][data-tool-count="3"]'
        )
        request_group.wait_for()
        tool_messages = request_group.locator(".request-message-tool")
        tool_blocks = request_group.locator(".message-tool-block")
        tool_payloads = request_group.locator(".message-tool-data")
        assert tool_messages.count() == tool_blocks.count() == tool_payloads.count() == 3
        assert tool_blocks.evaluate_all(
            "elements => elements.every(element => getComputedStyle(element).borderTopWidth === '0px')"
        )
        assert tool_messages.evaluate_all(
            "elements => elements.every(element => getComputedStyle(element).borderTopWidth === '0px')"
        )
        assert tool_payloads.evaluate_all(
            "elements => elements.every(element => getComputedStyle(element).borderTopWidth === '1px')"
        )
        assert request_group.evaluate(
            "element => getComputedStyle(element).backgroundColor !== getComputedStyle(element.querySelector('.message-tool-data')).backgroundColor"
        )

        boundary = page.locator(".response-boundary")
        assert boundary.count() == 1
        assert boundary.inner_text() == "RESPONSE"
        assert boundary.evaluate(
            "element => element.nextElementSibling?.matches('.request-message-group[data-origin=\"output\"]')"
        )
        assert (
            boundary.bounding_box()["y"]
            < page.locator('.request-message-group[data-origin="output"]').bounding_box()["y"]
        )
        assert page.evaluate("document.documentElement.scrollWidth === innerWidth")

        browser.close()


def test_request_preview_normalizes_provider_blocks_and_preserves_unknown_types(tmp_path: Path) -> None:
    def record(request_id: str, turn: int, path: str, body: dict[str, object]) -> dict[str, object]:
        return {
            "timestamp": f"2026-08-20T09:00:0{turn}+08:00",
            "request_id": request_id,
            "turn": turn,
            "duration_ms": 42,
            "request": {"method": "POST", "path": path, "headers": {}, "body": body},
            "response": {"status": 200, "headers": {}, "body": {}},
        }

    records = [
        record(
            "req_anthropic_blocks",
            1,
            "/v1/messages",
            {
                "model": "claude-test",
                "messages": [
                    {
                        "role": "assistant",
                        "content": [
                            {"type": "text", "text": "anthropic-text"},
                            {"type": "thinking", "thinking": "anthropic-thinking"},
                            {
                                "type": "tool_use",
                                "id": "toolu_read",
                                "name": "Read",
                                "input": {"file_path": "/tmp/source.py"},
                            },
                            {
                                "type": "future_anthropic_block",
                                "payload": {"alpha": 1},
                                "note": "future-kept",
                            },
                        ],
                    },
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": "toolu_read",
                                "is_error": True,
                                "content": "permission denied",
                            }
                        ],
                    },
                ],
            },
        ),
        record(
            "req_openai_chat_blocks",
            2,
            "/v1/chat/completions",
            {
                "model": "gpt-test",
                "messages": [
                    {
                        "role": "assistant",
                        "content": "chat-text",
                        "tool_calls": [
                            {
                                "id": "call_lookup",
                                "type": "function",
                                "function": {"name": "lookup", "arguments": '{"query":"needle"}'},
                            }
                        ],
                    },
                    {"role": "tool", "tool_call_id": "call_lookup", "content": '{"answer":42}'},
                ],
            },
        ),
        record(
            "req_openai_responses_blocks",
            3,
            "/v1/responses",
            {
                "model": "gpt-test",
                "input": [
                    {
                        "type": "message",
                        "role": "user",
                        "content": [{"type": "input_text", "text": "responses-text"}],
                    },
                    {
                        "type": "reasoning",
                        "summary": [{"type": "summary_text", "text": "reasoning-summary"}],
                    },
                    {
                        "type": "computer_call",
                        "call_id": "computer_1",
                        "name": "computer",
                        "action": {"type": "click", "x": 12, "y": 34},
                    },
                    {"type": "function_call_output", "call_id": "computer_1", "output": "computer-done"},
                    {"type": "future_response_item", "payload": {"future": True}},
                ],
            },
        ),
        record(
            "req_gemini_blocks",
            4,
            "/v1beta/models/gemini-test:generateContent",
            {
                "contents": [
                    {
                        "role": "model",
                        "parts": [
                            {"text": "gemini-text"},
                            {"text": "gemini-thinking", "thought": True},
                            {
                                "functionCall": {
                                    "id": "gemini_search_1",
                                    "name": "search",
                                    "args": {"query": "gemini-query"},
                                }
                            },
                            {
                                "functionResponse": {
                                    "id": "gemini_search_1",
                                    "name": "search",
                                    "response": {"ok": True},
                                }
                            },
                            {"inlineData": {"mimeType": "image/png", "data": "image-bytes"}},
                        ],
                    }
                ]
            },
        ),
        record(
            "req_long_bash_result",
            5,
            "/v1/messages",
            {
                "model": "claude-test",
                "messages": [
                    {
                        "role": "assistant",
                        "content": [
                            {
                                "type": "tool_use",
                                "id": "toolu_bash_long",
                                "name": "Bash",
                                "input": {"command": "find . -maxdepth 2 -type f"},
                            }
                        ],
                    },
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": "toolu_bash_long",
                                "content": "\n".join(
                                    ["Exit code 1"]
                                    + [
                                        f"long bash output line {index}: viewer_assets/file_{index}.js"
                                        for index in range(36)
                                    ]
                                ),
                            }
                        ],
                    },
                ],
            },
        ),
    ]
    trace = tmp_path / "trace_provider_blocks.jsonl"
    trace.write_text("\n".join(json.dumps(item) for item in records) + "\n", encoding="utf-8")
    output = trace.with_suffix(".html")
    assert render_html(trace, output)

    with playwright.sync_playwright() as runtime:
        try:
            browser = runtime.chromium.launch(headless=True)
        except playwright.Error as exc:
            pytest.skip(f"Playwright Chromium is unavailable: {exc}")
        page = browser.new_page(viewport={"width": 1440, "height": 800})
        page.goto(output.as_uri())

        def wait_for_tool_pair(call_id: str, name: str) -> None:
            page.wait_for_function(
                """({ callId, name }) => {
                  const blocks = Array.from(document.querySelectorAll(".message-tool-block"));
                  const call = blocks.find(
                    (block) => block.dataset.callId === callId && block.classList.contains("tool-call"),
                  );
                  const result = blocks.find(
                    (block) => block.dataset.callId === callId && block.classList.contains("tool-result"),
                  );
                  return call?.querySelector(".tool-call-name")?.innerText.trim() === name
                    && result?.querySelector(".tool-call-name")?.innerText.trim() === name
                    && call.querySelector(".tool-call-id")?.innerText.trim() === callId
                    && result.querySelector(".tool-call-id")?.innerText.trim() === callId;
                }""",
                arg={"callId": call_id, "name": name},
            )

        def tool_json(block):
            tree = block.locator(".inline-json-tree")
            assert tree.count() == 1
            assert tree.locator(".json-expanded-top-level").count() == 1
            assert "wrap-lines" in (tree.locator(".json-tree").get_attribute("class") or "")
            assert block.locator(".message-tool-text").count() == 0
            return tree.evaluate("element => element.__claudeTapJsonTree.value")

        page.locator(".trajectory-row").nth(0).click()
        page.locator('#workspace[data-inspector="open"]').wait_for()
        page.wait_for_function(
            "document.querySelector('[data-content-type=\"text\"]')?.textContent.includes('anthropic-text')"
        )
        wait_for_tool_pair("toolu_read", "Read")
        assert page.locator('[data-content-type="text"] .message-content').inner_text() == "anthropic-text"
        assert "anthropic-thinking" in page.locator('[data-content-type="thinking"]').inner_text()
        assert page.locator(".tool-call-name").first.inner_text() == "Read"
        anthropic_call = page.locator('.tool-call[data-call-id="toolu_read"]')
        anthropic_result = page.locator('.tool-result[data-call-id="toolu_read"]')
        assert anthropic_call.count() == 1
        assert anthropic_result.count() == 1
        assert anthropic_call.locator(".tool-call-id").inner_text() == "toolu_read"
        assert anthropic_result.locator(".tool-call-id").inner_text() == "toolu_read"
        assert anthropic_call.locator(".tool-phase").inner_text() == "CALL"
        assert anthropic_result.locator(".tool-phase").inner_text() == "RESULT"
        assert "error" in (anthropic_result.get_attribute("class") or "")
        assert anthropic_result.get_attribute("role") != "alert"
        assert anthropic_result.get_attribute("data-result-status") == "error"
        assert tool_json(anthropic_call) == {"file_path": "/tmp/source.py"}
        assert anthropic_result.locator(".inline-json-tree").count() == 0
        assert anthropic_result.locator(".message-tool-text").inner_text() == "permission denied"
        future = page.locator('[data-content-type="future_anthropic_block"]')
        assert '{"type":"future_anthropic_block","payload":{"alpha":1},"note":"future-kept"}' in future.inner_text()
        assert future.locator(".tool-format-badge").inner_text() == "JSON"

        page.locator(".trajectory-row").nth(1).click()
        page.wait_for_function("document.querySelector('#inspector-title').textContent.includes('#2')")
        wait_for_tool_pair("call_lookup", "lookup")
        assert page.locator(".tool-call-name").first.inner_text() == "lookup"
        chat_call = page.locator('.tool-call[data-call-id="call_lookup"]')
        chat_result = page.locator('.tool-result[data-call-id="call_lookup"]')
        assert tool_json(chat_call) == {"query": "needle"}
        assert tool_json(chat_result) == {"answer": 42}
        assert chat_call.count() == chat_result.count() == 1
        assert chat_call.locator(".tool-call-id").inner_text() == "call_lookup"
        assert chat_result.locator(".tool-call-id").inner_text() == "call_lookup"
        assert chat_result.get_attribute("data-result-status") == "ok"
        assert chat_result.get_attribute("role") != "alert"

        page.locator(".trajectory-row").nth(2).click()
        page.wait_for_function("document.querySelector('#inspector-title').textContent.includes('#3')")
        wait_for_tool_pair("computer_1", "computer")
        assert "reasoning-summary" in page.locator('[data-content-type="reasoning"]').inner_text()
        computer = page.locator(".message-tool-block").filter(has_text="computer").first
        assert tool_json(computer) == {"type": "click", "x": 12, "y": 34}
        assert page.locator(".tool-result .inline-json-tree").count() == 0
        assert page.locator(".tool-result .message-tool-text").inner_text() == "computer-done"
        responses_call = page.locator('.tool-call[data-call-id="computer_1"]')
        responses_result = page.locator('.tool-result[data-call-id="computer_1"]')
        assert responses_call.count() == responses_result.count() == 1
        assert responses_call.locator(".tool-call-id").inner_text() == "computer_1"
        assert responses_result.locator(".tool-call-id").inner_text() == "computer_1"
        assert '"future":true' in page.locator('[data-content-type="future_response_item"]').inner_text()

        page.locator(".trajectory-row").nth(3).click()
        page.wait_for_function("document.querySelector('#inspector-title').textContent.includes('#4')")
        wait_for_tool_pair("gemini_search_1", "search")
        assert page.locator('[data-content-type="text"] .message-content').inner_text() == "gemini-text"
        assert "gemini-thinking" in page.locator('[data-content-type="thinking"]').inner_text()
        assert page.locator(".tool-call-name").first.inner_text() == "search"
        assert tool_json(page.locator(".tool-call")) == {"query": "gemini-query"}
        assert tool_json(page.locator(".tool-result")) == {"ok": True}
        gemini_call = page.locator('.tool-call[data-call-id="gemini_search_1"]')
        gemini_result = page.locator('.tool-result[data-call-id="gemini_search_1"]')
        assert gemini_call.count() == gemini_result.count() == 1
        assert gemini_call.locator(".tool-call-id").inner_text() == "gemini_search_1"
        assert gemini_result.locator(".tool-call-id").inner_text() == "gemini_search_1"
        assert "image-bytes" in page.locator('[data-content-type="inline_data"]').inner_text()
        assert page.evaluate("document.documentElement.scrollWidth === innerWidth")

        page.locator(".trajectory-row").nth(4).click()
        page.wait_for_function("document.querySelector('#inspector-title').textContent.includes('#5')")
        wait_for_tool_pair("toolu_bash_long", "Bash")
        bash_result_data = page.locator('.tool-result[data-call-id="toolu_bash_long"] .message-tool-data')
        assert "long bash output line 35" in bash_result_data.inner_text()
        assert bash_result_data.evaluate("element => getComputedStyle(element).overflowY") in {"auto", "scroll"}
        assert bash_result_data.evaluate("element => element.scrollHeight > element.clientHeight")
        assert bash_result_data.evaluate("element => element.clientHeight <= 240")
        assert bash_result_data.evaluate("element => parseFloat(getComputedStyle(element).maxHeight) <= 240")

        browser.close()


def test_messages_append_normalized_model_output_after_request_history(tmp_path: Path) -> None:
    def record(
        request_id: str,
        turn: int,
        path: str,
        request_body: dict[str, object],
        response_body: dict[str, object],
        *,
        status: int = 200,
        sse_events: list[dict[str, object]] | None = None,
    ) -> dict[str, object]:
        response: dict[str, object] = {"status": status, "headers": {}, "body": response_body}
        if sse_events is not None:
            response["sse_events"] = sse_events
        return {
            "timestamp": f"2026-08-20T10:00:0{turn}+08:00",
            "request_id": request_id,
            "turn": turn,
            "duration_ms": 42,
            "request": {"method": "POST", "path": path, "headers": {}, "body": request_body},
            "response": response,
        }

    records = [
        record(
            "req_anthropic_output",
            1,
            "/v1/messages",
            {
                "model": "claude-test",
                "messages": [{"role": "user", "content": "anthropic-request-before-output"}],
            },
            {
                "id": "msg_anthropic_output",
                "type": "message",
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "anthropic-output-text"},
                    {"type": "thinking", "thinking": "anthropic-output-thinking"},
                    {
                        "type": "tool_use",
                        "id": "toolu_anthropic_output",
                        "name": "Read",
                        "input": {"file_path": "/tmp/anthropic.py"},
                    },
                ],
                "usage": {
                    "input_tokens": 17,
                    "output_tokens": 9,
                    "service_tier": "ANTHROPIC_USAGE_WRAPPER_MUST_NOT_RENDER",
                },
            },
            sse_events=[
                {
                    "event": "content_block_delta",
                    "data": {"delta": {"text": "SSE_FRAME_MUST_NOT_RENDER"}},
                }
            ],
        ),
        record(
            "req_chat_output",
            2,
            "/v1/chat/completions",
            {
                "model": "gpt-test",
                "messages": [{"role": "user", "content": "chat-request-before-output"}],
            },
            {
                "id": "chatcmpl_output",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": [
                                {"type": "text", "text": "chat-output-text"},
                                {"type": "reasoning", "thinking": "chat-output-thinking"},
                            ],
                            "tool_calls": [
                                {
                                    "id": "call_chat_output",
                                    "type": "function",
                                    "function": {
                                        "name": "lookup",
                                        "arguments": '{"query":"chat-output"}',
                                    },
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
                "usage": {
                    "prompt_tokens": 11,
                    "completion_tokens": 7,
                    "marker": "CHAT_USAGE_WRAPPER_MUST_NOT_RENDER",
                },
            },
        ),
        record(
            "req_responses_output",
            3,
            "/v1/responses",
            {
                "model": "gpt-test",
                "input": [
                    {
                        "type": "message",
                        "role": "user",
                        "content": [{"type": "input_text", "text": "responses-request-before-output"}],
                    }
                ],
            },
            {
                "id": "resp_output",
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "id": "msg_responses_output",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "responses-output-text"}],
                    },
                    {
                        "type": "reasoning",
                        "id": "rs_responses_output",
                        "summary": [{"type": "summary_text", "text": "responses-output-thinking"}],
                    },
                    {
                        "type": "function_call",
                        "id": "fc_responses_output",
                        "call_id": "call_responses_output",
                        "name": "lookup",
                        "arguments": '{"query":"responses-output"}',
                    },
                ],
                "usage": {
                    "input_tokens": 13,
                    "output_tokens": 8,
                    "marker": "RESPONSES_USAGE_WRAPPER_MUST_NOT_RENDER",
                },
            },
        ),
        record(
            "req_gemini_output",
            4,
            "/v1beta/models/gemini-test:generateContent",
            {
                "contents": [{"role": "user", "parts": [{"text": "gemini-request-before-output"}]}],
            },
            {
                "candidates": [
                    {
                        "content": {
                            "role": "model",
                            "parts": [
                                {"text": "gemini-output-text"},
                                {"text": "gemini-output-thinking", "thought": True},
                                {
                                    "functionCall": {
                                        "id": "call_gemini_output",
                                        "name": "search",
                                        "args": {"query": "gemini-output"},
                                    }
                                },
                            ],
                        },
                        "finishReason": "STOP",
                    }
                ],
                "usageMetadata": {
                    "promptTokenCount": 5,
                    "candidatesTokenCount": 4,
                    "marker": "GEMINI_USAGE_WRAPPER_MUST_NOT_RENDER",
                },
            },
        ),
        record(
            "req_unknown_output",
            5,
            "/v1/messages",
            {
                "model": "future-model",
                "messages": [{"role": "user", "content": "unknown-request-before-output"}],
            },
            {
                "mystery_packet": {
                    "marker": "UNKNOWN_RESPONSE_FIELD_MUST_SURVIVE",
                    "nested": [1, {"keep": True}],
                },
                "finish_reason": "future_protocol",
            },
        ),
        record(
            "req_error_output",
            6,
            "/v1/messages",
            {
                "model": "claude-test",
                "messages": [{"role": "user", "content": "error-request-before-output"}],
            },
            {
                "type": "error",
                "error": {
                    "type": "overloaded_error",
                    "message": "Overloaded",
                    "diagnostic": "ERROR_RESPONSE_FIELD_MUST_SURVIVE",
                },
                "request_id": "req_error_body_id",
            },
            status=529,
        ),
    ]
    trace = tmp_path / "trace_model_outputs.jsonl"
    trace.write_text("\n".join(json.dumps(item) for item in records) + "\n", encoding="utf-8")
    output = trace.with_suffix(".html")
    assert render_html(trace, output, strip_events=False)

    with playwright.sync_playwright() as runtime:
        try:
            browser = runtime.chromium.launch(headless=True)
        except playwright.Error as exc:
            pytest.skip(f"Playwright Chromium is unavailable: {exc}")
        page = browser.new_page(viewport={"width": 1440, "height": 800})
        page.goto(output.as_uri())
        rows = page.locator(".trajectory-row")
        assert rows.count() == len(records)

        def select_output(turn: int):
            rows.nth(turn - 1).click()
            page.wait_for_function(
                "turn => document.querySelector('#inspector-title')?.textContent.includes(`#${turn}`)",
                arg=turn,
            )
            output_groups = page.locator('.request-message-group[data-origin="output"]')
            output_groups.first.wait_for()
            groups = page.locator(".request-message-group")
            origins = [groups.nth(index).get_attribute("data-origin") for index in range(groups.count())]
            first_output = origins.index("output")
            assert first_output > 0
            assert all(origin != "output" for origin in origins[:first_output])
            assert all(origin == "output" for origin in origins[first_output:])
            assert output_groups.locator(".request-message").evaluate_all(
                "elements => elements.every(element => element.dataset.origin === 'output')"
            )
            return output_groups

        def assert_output_blocks(expected_types: list[str]) -> None:
            output_blocks = page.locator(
                '.request-message-group[data-origin="output"] .request-message[data-origin="output"]'
            )
            assert output_blocks.count() == len(expected_types)
            assert [
                output_blocks.nth(index).get_attribute("data-block-type") for index in range(len(expected_types))
            ] == expected_types

        anthropic = select_output(1)
        assert anthropic.count() == 1
        assert_output_blocks(["text", "thinking", "tool_use"])
        assert "anthropic-output-text" in anthropic.inner_text()
        assert "anthropic-output-thinking" in anthropic.inner_text()
        anthropic_call = anthropic.locator('.request-message[data-call-id="toolu_anthropic_output"]')
        assert anthropic_call.get_attribute("data-block-type") == "tool_use"
        assert anthropic_call.locator(".tool-call-id").inner_text() == "toolu_anthropic_output"
        assert "SSE_FRAME_MUST_NOT_RENDER" not in page.locator(".conversation-flow").inner_text()
        assert "ANTHROPIC_USAGE_WRAPPER_MUST_NOT_RENDER" not in page.locator(".conversation-flow").inner_text()

        chat = select_output(2)
        assert chat.count() == 1
        assert_output_blocks(["text", "reasoning", "tool_call"])
        assert "chat-output-text" in chat.inner_text()
        assert "chat-output-thinking" in chat.inner_text()
        chat_call = chat.locator('.request-message[data-call-id="call_chat_output"]')
        assert chat_call.get_attribute("data-block-type") == "tool_call"
        assert chat_call.locator(".tool-call-id").inner_text() == "call_chat_output"
        assert "CHAT_USAGE_WRAPPER_MUST_NOT_RENDER" not in page.locator(".conversation-flow").inner_text()

        responses = select_output(3)
        assert responses.count() == 3
        assert_output_blocks(["output_text", "reasoning", "function_call"])
        responses_text = "\n".join(responses.all_inner_texts())
        assert "responses-output-text" in responses_text
        assert "responses-output-thinking" in responses_text
        responses_call = responses.locator('.request-message[data-call-id="call_responses_output"]')
        assert responses_call.get_attribute("data-block-type") == "function_call"
        assert responses_call.locator(".tool-call-id").inner_text() == "call_responses_output"
        assert "RESPONSES_USAGE_WRAPPER_MUST_NOT_RENDER" not in page.locator(".conversation-flow").inner_text()

        gemini = select_output(4)
        assert gemini.count() == 1
        assert gemini.get_attribute("data-role") == "assistant"
        assert_output_blocks(["text", "thinking", "function_call"])
        assert "gemini-output-text" in gemini.inner_text()
        assert "gemini-output-thinking" in gemini.inner_text()
        gemini_call = gemini.locator('.request-message[data-call-id="call_gemini_output"]')
        assert gemini_call.get_attribute("data-block-type") == "function_call"
        assert gemini_call.get_attribute("data-raw-role") == "model"
        assert gemini_call.locator(".tool-call-id").inner_text() == "call_gemini_output"
        assert "GEMINI_USAGE_WRAPPER_MUST_NOT_RENDER" not in page.locator(".conversation-flow").inner_text()

        unknown = select_output(5)
        assert unknown.count() == 1
        assert_output_blocks(["response_fallback"])
        assert "UNKNOWN_RESPONSE_FIELD_MUST_SURVIVE" in unknown.inner_text()
        assert '"nested":[1,{"keep":true}]' in unknown.inner_text()
        assert '"finish_reason":"future_protocol"' in unknown.inner_text()

        error = select_output(6)
        assert error.count() == 1
        assert_output_blocks(["response_error"])
        assert "ERROR_RESPONSE_FIELD_MUST_SURVIVE" in error.inner_text()
        assert "overloaded_error" in error.inner_text()
        assert "req_error_body_id" in error.inner_text()
        assert page.locator(".request-error-notice").count() == 1

        assert (
            page.locator(
                '.request-message[data-origin="output"][data-block-type="usage"], '
                '.request-message[data-origin="output"][data-block-type="sse_event"]'
            ).count()
            == 0
        )
        assert page.evaluate("document.documentElement.scrollWidth === innerWidth")

        browser.close()
