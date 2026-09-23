(function () {
  "use strict";

  const {
    $,
    LruCache,
    escapeHtml,
    debounce,
    formatDuration,
    formatClock,
    formatNumber,
    safeJson,
    copyText,
    requestToCurl,
    canFocus,
    focusSafely,
    focusNextFrom,
  } = window.ClaudeTapCore;
  const { icon, hydrateIcons, toolIcon } = window.ClaudeTapIcons;

  let fixedTooltipSequence = 0;
  const MESSAGE_LONG_CHARACTER_THRESHOLD = 3_200;
  const MESSAGE_LONG_LINE_THRESHOLD = 40;

  function isLongMessageContent(value) {
    const text = String(value ?? "");
    if (text.length > MESSAGE_LONG_CHARACTER_THRESHOLD) return true;
    let lineBreaks = 0;
    for (let index = text.indexOf("\n"); index !== -1; index = text.indexOf("\n", index + 1)) {
      lineBreaks += 1;
      if (lineBreaks > MESSAGE_LONG_LINE_THRESHOLD) return true;
    }
    return false;
  }

  class FixedVirtualList {
    constructor(viewport, canvas, rowHeight, renderItem, overscan = 6) {
      this.viewport = viewport;
      this.canvas = canvas;
      this.rowHeight = rowHeight;
      this.renderItem = renderItem;
      this.overscan = overscan;
      this.items = [];
      this.renderedRange = "";
      this.frame = 0;
      this.viewport.addEventListener("scroll", () => this.schedule());
      // Width-only layout changes are handled by CSS. Replacing every visible
      // row during a resize briefly detaches the active target and can make
      // clicks or measurements race the responsive transition. A normal
      // refresh still recalculates the range when the viewport height changes.
      new ResizeObserver(() => this.schedule()).observe(this.viewport);
    }

    setItems(items, preserveScroll = true) {
      const previousTop = this.viewport.scrollTop;
      this.items = items || [];
      const contentHeight = this.items.length * this.rowHeight;
      this.canvas.style.height = `${contentHeight}px`;
      const maxScroll = Math.max(0, contentHeight - this.viewport.clientHeight);
      this.viewport.scrollTop = preserveScroll ? Math.min(previousTop, maxScroll) : 0;
      this.refresh(true);
    }

    setRowHeight(rowHeight) {
      if (rowHeight === this.rowHeight) return;
      const anchor = Math.floor(this.viewport.scrollTop / this.rowHeight);
      this.rowHeight = rowHeight;
      const contentHeight = this.items.length * this.rowHeight;
      this.canvas.style.height = `${contentHeight}px`;
      const maxScroll = Math.max(0, contentHeight - this.viewport.clientHeight);
      this.viewport.scrollTop = Math.min(anchor * rowHeight, maxScroll);
      this.refresh(true);
    }

    schedule(force = false) {
      if (force) this.renderedRange = "";
      if (this.frame) return;
      this.frame = requestAnimationFrame(() => {
        this.frame = 0;
        this.refresh(force);
      });
    }

    refresh(force = false) {
      const height = Math.max(this.viewport.clientHeight, this.rowHeight);
      const start = Math.max(0, Math.floor(this.viewport.scrollTop / this.rowHeight) - this.overscan);
      const end = Math.min(
        this.items.length,
        Math.ceil((this.viewport.scrollTop + height) / this.rowHeight) + this.overscan,
      );
      const range = `${start}:${end}:${this.rowHeight}`;
      if (!force && range === this.renderedRange) return;
      this.renderedRange = range;
      const fragment = document.createDocumentFragment();
      for (let index = start; index < end; index += 1) {
        const row = this.renderItem(this.items[index], index);
        row.style.top = `${index * this.rowHeight}px`;
        row.style.height = `${this.rowHeight}px`;
        fragment.appendChild(row);
      }
      this.disposeRendered();
      this.canvas.replaceChildren(fragment);
      hydrateIcons(this.canvas);
    }

    disposeRendered() {
      for (const child of this.canvas.children) {
        if (typeof child.__claudeTapDispose === "function") child.__claudeTapDispose();
      }
    }

    scrollToIndex(index, align = "center") {
      if (index < 0 || index >= this.items.length) return;
      const itemTop = index * this.rowHeight;
      const itemBottom = itemTop + this.rowHeight;
      if (align === "start") this.viewport.scrollTop = itemTop;
      else if (itemTop < this.viewport.scrollTop || itemBottom > this.viewport.scrollTop + this.viewport.clientHeight) {
        this.viewport.scrollTop = Math.max(0, itemTop - (this.viewport.clientHeight - this.rowHeight) / 2);
      }
      this.refresh(true);
    }
  }

  class DropdownMenu {
    static openMenu = null;

    constructor(trigger, host, getItems, onSelect) {
      this.trigger = trigger;
      this.host = host;
      this.getItems = getItems;
      this.onSelect = onSelect;
      this.originalParent = host.parentNode;
      this.originalNextSibling = host.nextSibling;
      this.isOpen = false;
      this.focusFrame = 0;
      this.openSequence = 0;
      this._onDocumentPointerDown = (event) => {
        if (!this.host.contains(event.target) && !this.trigger.contains(event.target)) this.close();
      };
      this._onDocumentKeyDown = (event) => {
        if (event.key === "Escape") {
          event.preventDefault();
          event.stopPropagation();
          this.close({ restoreFocus: true });
        } else if (event.key === "Tab" && this.host.contains(event.target)) {
          event.preventDefault();
          event.stopPropagation();
          this.close();
          focusNextFrom(this.trigger, event.shiftKey);
        }
      };
      this._reposition = () => this._position();
      this.trigger.addEventListener("click", (event) => {
        event.stopPropagation();
        this.toggle();
      });
      this.trigger.addEventListener("keydown", (event) => {
        if (!['ArrowDown', 'ArrowUp'].includes(event.key)) return;
        event.preventDefault();
        event.stopPropagation();
        this.open({ focus: event.key === 'ArrowUp' ? "last" : "first" });
      });
      this.host.addEventListener("keydown", (event) => this._onKeyDown(event));
      if (this.host.id) this.trigger.setAttribute("aria-controls", this.host.id);
    }

    render() {
      const fragment = document.createDocumentFragment();
      for (const item of this.getItems() || []) {
        if (item.separator) {
          const separator = document.createElement("div");
          separator.className = "menu-separator";
          separator.setAttribute("role", "separator");
          fragment.appendChild(separator);
          continue;
        }
        if (item.heading) {
          const label = document.createElement("div");
          label.className = "menu-label";
          label.setAttribute("role", "presentation");
          label.textContent = item.label;
          fragment.appendChild(label);
          continue;
        }
        const button = document.createElement("button");
        button.type = "button";
        button.className = `menu-item${item.danger ? " danger" : ""}`;
        button.dataset.menuId = item.id;
        button.disabled = Boolean(item.disabled);
        button.setAttribute("role", item.radio ? "menuitemradio" : "menuitem");
        if (item.radio) button.setAttribute("aria-checked", String(Boolean(item.checked)));
        button.innerHTML = `${item.icon ? `<span class="menu-item-icon">${icon(item.icon, 16)}</span>` : ""}<span class="menu-item-copy"><span>${escapeHtml(
          item.label,
        )}</span></span>${item.detail ? `<span class="menu-item-meta">${escapeHtml(item.detail)}</span>` : ""}${
          item.checked ? icon("check", 16, "menu-check") : ""
        }`;
        button.addEventListener("click", () => {
          this.close({ restoreFocus: true });
          this.onSelect(item.id, item);
        });
        fragment.appendChild(button);
      }
      this.host.replaceChildren(fragment);
    }

    open({ focus = "first" } = {}) {
      if (!canFocus(this.trigger)) return false;
      if (DropdownMenu.openMenu && DropdownMenu.openMenu !== this) DropdownMenu.openMenu.close();
      DropdownMenu.openMenu = this;
      this._cancelPendingFocus();
      this.openSequence += 1;
      this.render();
      document.body.appendChild(this.host);
      Object.assign(this.host.style, {
        position: "fixed",
        right: "auto",
        bottom: "auto",
      });
      this.host.dataset.portaled = "true";
      this.host.style.visibility = "hidden";
      this.host.hidden = false;
      this.isOpen = true;
      this.trigger.setAttribute("aria-expanded", "true");
      this._bindOpenListeners();
      this._position();
      this.host.style.removeProperty("visibility");
      const sequence = this.openSequence;
      this.focusFrame = requestAnimationFrame(() => {
        this.focusFrame = 0;
        if (!this.isOpen || sequence !== this.openSequence) return;
        const items = this._enabledItems();
        focusSafely(focus === "last" ? items.at(-1) : items[0]);
      });
      return true;
    }

    close({ restoreFocus = false } = {}) {
      this._cancelPendingFocus();
      this.openSequence += 1;
      if (!this.isOpen && this.host.hidden) return;
      this.isOpen = false;
      this.host.hidden = true;
      this.host.style.removeProperty("left");
      this.host.style.removeProperty("top");
      this.host.style.removeProperty("right");
      this.host.style.removeProperty("bottom");
      this.host.style.removeProperty("position");
      this.host.style.removeProperty("visibility");
      this.host.removeAttribute("data-side");
      this.host.removeAttribute("data-portaled");
      this.trigger.setAttribute("aria-expanded", "false");
      this._unbindOpenListeners();
      if (this.originalNextSibling?.parentNode === this.originalParent) {
        this.originalParent.insertBefore(this.host, this.originalNextSibling);
      } else {
        this.originalParent.appendChild(this.host);
      }
      if (DropdownMenu.openMenu === this) DropdownMenu.openMenu = null;
      if (restoreFocus) focusSafely(this.trigger);
    }

    toggle() {
      if (!this.isOpen) this.open();
      else this.close();
    }

    _onKeyDown(event) {
      const items = this._enabledItems();
      if (!items.length || !["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) return;
      event.preventDefault();
      const current = items.indexOf(document.activeElement);
      let next = current;
      if (event.key === "Home") next = 0;
      else if (event.key === "End") next = items.length - 1;
      else if (event.key === "ArrowDown") next = current < 0 ? 0 : (current + 1) % items.length;
      else next = current < 0 ? items.length - 1 : (current - 1 + items.length) % items.length;
      focusSafely(items[next]);
    }

    _enabledItems() {
      return Array.from(this.host.querySelectorAll(".menu-item:not(:disabled)"));
    }

    _cancelPendingFocus() {
      if (!this.focusFrame) return;
      cancelAnimationFrame(this.focusFrame);
      this.focusFrame = 0;
    }

    _bindOpenListeners() {
      document.addEventListener("pointerdown", this._onDocumentPointerDown, true);
      document.addEventListener("keydown", this._onDocumentKeyDown, true);
      window.addEventListener("scroll", this._reposition, true);
      window.addEventListener("resize", this._reposition);
    }

    _unbindOpenListeners() {
      document.removeEventListener("pointerdown", this._onDocumentPointerDown, true);
      document.removeEventListener("keydown", this._onDocumentKeyDown, true);
      window.removeEventListener("scroll", this._reposition, true);
      window.removeEventListener("resize", this._reposition);
    }

    _position() {
      if (!this.isOpen || this.host.hidden) return;
      if (!canFocus(this.trigger)) {
        this.close();
        return;
      }
      const anchor = this.trigger.getBoundingClientRect();
      const width = this.host.offsetWidth;
      const height = this.host.offsetHeight;
      const margin = 12;
      const gap = 4;
      const alignEnd = this.host.classList.contains("align-right");
      const preferTop = this.host.classList.contains("menu-up");
      let side = preferTop ? "top" : "bottom";
      const roomBelow = window.innerHeight - anchor.bottom - margin;
      const roomAbove = anchor.top - margin;
      if (side === "bottom" && height > roomBelow && roomAbove > roomBelow) side = "top";
      if (side === "top" && height > roomAbove && roomBelow > roomAbove) side = "bottom";
      let left = alignEnd ? anchor.right - width : anchor.left;
      let top = side === "top" ? anchor.top - height - gap : anchor.bottom + gap;
      left = Math.min(Math.max(left, margin), Math.max(margin, window.innerWidth - width - margin));
      top = Math.min(Math.max(top, margin), Math.max(margin, window.innerHeight - height - margin));
      this.host.dataset.side = side;
      this.host.style.left = `${Math.round(left)}px`;
      this.host.style.top = `${Math.round(top)}px`;
    }
  }

  /**
   * Fixed-position tooltip controller for icon-only controls in clipped panels.
   * The anchor remains in place: this controller only attaches listeners and
   * appends the visible bubble to document.body.
   */
  class FixedTooltip {
    constructor(anchor, label, options = {}) {
      if (!anchor || typeof anchor.addEventListener !== "function") {
        throw new TypeError("FixedTooltip requires a DOM anchor element");
      }
      this.anchor = anchor;
      this.label = label;
      this.side = ["right", "bottom", "top"].includes(options.side) ? options.side : "right";
      this.delayMs = Math.max(0, Number.isFinite(Number(options.delayMs)) ? Number(options.delayMs) : 500);
      this.maxWidth = Number.isFinite(Number(options.maxWidth)) ? Math.max(0, Number(options.maxWidth)) : null;
      this.variant = options.variant === "preview" ? "preview" : "label";
      this.hideOnClick = options.hideOnClick !== false;
      this.disabled = Boolean(options.disabled);
      this.hovered = false;
      this.focused = false;
      this.timer = 0;
      this.fadeFrame = 0;
      this.bubble = null;
      this.destroyed = false;
      this.tooltipId = `claude-tap-tooltip-${++fixedTooltipSequence}`;

      this.onMouseEnter = () => {
        this.hovered = true;
        this._scheduleHover();
      };
      this.onMouseLeave = () => {
        this.hovered = false;
        this._cancelTimer();
        if (!this.focused) this._hide();
      };
      this.onFocus = () => {
        this.focused = true;
        this._cancelTimer();
        this._show();
      };
      this.onBlur = () => {
        this.focused = false;
        if (!this.hovered && !this.focused) this._hide();
      };
      this.onResize = () => {
        if (this.bubble) this._position();
      };
      this.onClick = () => {
        if (!this.hideOnClick) return;
        this._cancelTimer();
        this._hide();
      };

      this.anchor.addEventListener("mouseenter", this.onMouseEnter);
      this.anchor.addEventListener("mouseleave", this.onMouseLeave);
      this.anchor.addEventListener("focus", this.onFocus);
      this.anchor.addEventListener("blur", this.onBlur);
      this.anchor.addEventListener("click", this.onClick);
      window.addEventListener("resize", this.onResize);
    }

    setLabel(label) {
      this.label = label;
      if (!this.bubble) return;
      this.bubble.textContent = this._resolveLabel();
      this._position();
    }

    setDisabled(disabled) {
      this.disabled = Boolean(disabled);
      if (!this.disabled) return;
      this.hovered = false;
      this.focused = false;
      this._cancelTimer();
      this._hide();
    }

    destroy() {
      if (this.destroyed) return;
      this.destroyed = true;
      this._cancelTimer();
      this._hide();
      this.anchor.removeEventListener("mouseenter", this.onMouseEnter);
      this.anchor.removeEventListener("mouseleave", this.onMouseLeave);
      this.anchor.removeEventListener("focus", this.onFocus);
      this.anchor.removeEventListener("blur", this.onBlur);
      this.anchor.removeEventListener("click", this.onClick);
      window.removeEventListener("resize", this.onResize);
    }

    _resolveLabel() {
      const value = typeof this.label === "function" ? this.label() : this.label;
      return value === null || value === undefined ? "" : String(value);
    }

    _scheduleHover() {
      this._cancelTimer();
      if (this.disabled || this.destroyed) return;
      if (this.delayMs === 0) {
        this._show();
        return;
      }
      this.timer = window.setTimeout(() => {
        this.timer = 0;
        if (this.hovered) this._show();
      }, this.delayMs);
    }

    _cancelTimer() {
      if (!this.timer) return;
      window.clearTimeout(this.timer);
      this.timer = 0;
    }

    _show() {
      const label = this._resolveLabel();
      if (this.disabled || this.destroyed || !label || !canFocus(this.anchor)) return;
      if (!this.bubble) {
        const bubble = document.createElement("span");
        const reducedMotion = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches === true;
        bubble.id = this.tooltipId;
        bubble.className = `fixed-tooltip fixed-tooltip--${this.variant}`;
        bubble.setAttribute("role", "tooltip");
        bubble.style.setProperty("--tooltip-max-width", this.maxWidth === null ? "50vw" : `${this.maxWidth}px`);
        Object.assign(bubble.style, {
          position: "fixed",
          zIndex: "100",
          opacity: reducedMotion ? "1" : "0",
        });
        if (!reducedMotion) {
          bubble.style.transition = "opacity 150ms cubic-bezier(0.4, 0, 0.2, 1)";
        }
        document.body.appendChild(bubble);
        this.bubble = bubble;
        this._addDescription();
      }
      this.bubble.textContent = label;
      this._position();
      if (this.bubble.style.opacity === "1") return;
      window.cancelAnimationFrame(this.fadeFrame);
      this.fadeFrame = window.requestAnimationFrame(() => {
        this.fadeFrame = 0;
        if (this.bubble) this.bubble.style.opacity = "1";
      });
    }

    _hide() {
      window.cancelAnimationFrame(this.fadeFrame);
      this.fadeFrame = 0;
      this._removeDescription();
      this.bubble?.remove();
      this.bubble = null;
    }

    _addDescription() {
      const ids = new Set((this.anchor.getAttribute("aria-describedby") || "").split(/\s+/).filter(Boolean));
      ids.add(this.tooltipId);
      this.anchor.setAttribute("aria-describedby", Array.from(ids).join(" "));
    }

    _removeDescription() {
      const ids = (this.anchor.getAttribute("aria-describedby") || "")
        .split(/\s+/)
        .filter((id) => id && id !== this.tooltipId);
      if (ids.length) this.anchor.setAttribute("aria-describedby", ids.join(" "));
      else this.anchor.removeAttribute("aria-describedby");
    }

    _position() {
      const bubble = this.bubble;
      if (!bubble) return;
      const anchorRect = this.anchor.getBoundingClientRect();
      const edgeMargin = 12;
      let placement = this.side;

      const place = () => {
        const x = placement === "right" ? anchorRect.right + 10 : anchorRect.left + anchorRect.width / 2;
        const y = placement === "right"
          ? anchorRect.top + anchorRect.height / 2
          : placement === "top"
            ? anchorRect.top - 8
            : anchorRect.bottom + 8;
        bubble.dataset.side = placement;
        bubble.style.left = `${x}px`;
        bubble.style.top = `${y}px`;
        bubble.style.transform = placement === "right"
          ? "translateY(-50%)"
          : placement === "top"
            ? "translate(-50%, -100%)"
            : "translateX(-50%)";

        const rect = bubble.getBoundingClientRect();
        let dx = 0;
        if (rect.right > window.innerWidth - edgeMargin) dx = window.innerWidth - edgeMargin - rect.right;
        if (rect.left + dx < edgeMargin) dx = edgeMargin - rect.left;
        bubble.style.left = `${x + dx}px`;
        if (placement === "right") {
          let dy = 0;
          if (rect.bottom > window.innerHeight - edgeMargin) dy = window.innerHeight - edgeMargin - rect.bottom;
          if (rect.top + dy < edgeMargin) dy = edgeMargin - rect.top;
          bubble.style.top = `${y + dy}px`;
        }
        return rect;
      };

      const bubbleRect = place();
      if (this.side === "right") return;
      const fitsBelow = anchorRect.bottom + 8 + bubbleRect.height <= window.innerHeight - edgeMargin;
      const fitsAbove = anchorRect.top - 8 - bubbleRect.height >= edgeMargin;
      if (placement === "bottom" && !fitsBelow && fitsAbove) placement = "top";
      else if (placement === "top" && !fitsAbove && fitsBelow) placement = "bottom";
      if (placement !== this.side) place();
    }
  }

  function contentText(content) {
    if (typeof content === "string") return content;
    if (content === null || content === undefined) return "";
    if (Array.isArray(content)) return content.map(contentText).filter(Boolean).join("\n");
    if (typeof content !== "object") return String(content);
    if (typeof content.text === "string") return content.text;
    if (typeof content.output_text === "string") return content.output_text;
    if (typeof content.thinking === "string") return content.thinking;
    if (content.content !== undefined) return contentText(content.content);
    if (content.parts !== undefined) return contentText(content.parts);
    if (content.output !== undefined) return contentText(content.output);
    return "";
  }

  function systemEntries(body) {
    if (!body || typeof body !== "object") return [];
    const entries = [];
    for (const key of Object.keys(body)) {
      if (!["system", "systemInstruction", "instructions"].includes(key)) continue;
      if (body[key] !== undefined && body[key] !== null) entries.push({ source: key, value: body[key] });
    }
    const messages = Array.isArray(body.messages) ? body.messages : [];
    const input = Array.isArray(body.input) ? body.input : [];
    for (const message of [...messages, ...input]) {
      if (!message || typeof message !== "object" || !["system", "developer"].includes(message.role)) continue;
      entries.push({ source: message.role, value: message.content ?? message });
    }
    return entries;
  }

  function systemText(body) {
    return systemEntries(body).map((entry) => contentText(entry.value)).filter(Boolean).join("\n");
  }

  function requestSequence(body) {
    if (!body || typeof body !== "object") return [];
    let source = [];
    let protocol = "unknown";
    if (Array.isArray(body.messages)) {
      source = body.messages;
      protocol = "messages";
    } else if (Array.isArray(body.input)) {
      source = body.input;
      protocol = "responses";
    } else if (body.input !== undefined && body.input !== null) {
      source = [{ role: "user", type: "input", content: body.input }];
      protocol = "responses";
    } else if (Array.isArray(body.contents)) {
      source = body.contents;
      protocol = "gemini";
    } else if (body.prompt !== undefined && body.prompt !== null) {
      source = [{ role: "user", type: "prompt", content: body.prompt }];
      protocol = "completion";
    }
    return source
      .filter((item) => item !== undefined && item !== null)
      .map((item, index) => {
        const raw = typeof item === "object" ? item : { role: "user", content: item };
        const type = String(raw.type || (protocol === "gemini" ? "content" : "message"));
        let role = raw.role || "context";
        if (role === "model") role = "assistant";
        if (!raw.role && (type === "function_call" || type.endsWith("_call") || type.endsWith("_tool_use"))) {
          role = "assistant";
        }
        if (!raw.role && (type === "function_call_output" || type.endsWith("_call_output") || type.endsWith("_tool_result"))) {
          role = "tool";
        }
        if (type === "additional_tools") role = "context";
        return { index, protocol, role, rawRole: raw.role || role, type, raw };
      });
  }

  function responseSequence(response, forceFailed = false) {
    if (!response || typeof response !== "object") return [];
    const headers = response.headers && typeof response.headers === "object" ? response.headers : {};
    const contentTypeEntry = Object.entries(headers).find(([key]) => key.toLowerCase() === "content-type");
    const contentType = String(contentTypeEntry?.[1] || "").toLowerCase();
    const rawBody = response.body;
    const body = parseMaybeJson(rawBody);
    if (body === null || body === undefined || body === "") return [];
    if (
      typeof body === "string" &&
      (contentType.includes("text/event-stream") || /(?:^|\n)\s*(?:event|data):/.test(body))
    ) {
      return [];
    }

    const failed = forceFailed || Number(response.status || 0) >= 400;
    const makeItem = (raw, index, protocol, defaultRole = "assistant", forcedType = "") => {
      const object =
        raw !== null && typeof raw === "object"
          ? raw
          : { role: defaultRole, type: forcedType || "text", content: raw };
      const type = String(forcedType || object.type || (protocol === "gemini" ? "content" : "message"));
      let rawRole = object.role || defaultRole;
      if (!object.role && (type === "function_call" || type.endsWith("_call") || type.endsWith("_tool_use"))) {
        rawRole = "assistant";
      }
      if (
        !object.role &&
        (type === "function_call_output" || type.endsWith("_call_output") || type.endsWith("_tool_result"))
      ) {
        rawRole = "tool";
      }
      return {
        index,
        protocol,
        role: rawRole === "model" ? "assistant" : rawRole,
        rawRole,
        type,
        raw: object,
        origin: "output",
        fallback: Boolean(forcedType),
        fallbackValue: forcedType ? raw : undefined,
      };
    };

    if (body && typeof body === "object" && !Array.isArray(body)) {
      if (Object.prototype.hasOwnProperty.call(body, "content")) {
        const content = body.content;
        const hasContent = Array.isArray(content) ? content.length > 0 : content !== null && content !== "";
        if (hasContent) {
          return [
            makeItem(
              { role: body.role || "assistant", type: "message", content },
              0,
              "anthropic",
              "assistant",
            ),
          ];
        }
        if (!failed) return [];
      }

      if (Array.isArray(body.choices)) {
        const choices = body.choices
          .map((choice) => choice?.message)
          .filter((message) => message !== undefined && message !== null)
          .map((message, index) => makeItem(message, index, "openai-chat", "assistant"));
        if (choices.length || !failed) return choices;
      }

      if (Array.isArray(body.output)) {
        const output = body.output
          .filter((item) => item !== undefined && item !== null)
          .map((item) => {
            if (!item || typeof item !== "object" || item.type !== "reasoning") return item;
            const summary = item.summary ?? item.content;
            if (summary === undefined || summary === null) return item;
            const values = Array.isArray(summary) ? summary : [summary];
            return {
              role: item.role || "assistant",
              type: "message",
              content: values.map((value) => {
                if (value && typeof value === "object") {
                  return { ...value, type: "reasoning", text: value.text ?? contentText(value) };
                }
                return { type: "reasoning", text: contentText(value) };
              }),
            };
          })
          .map((item, index) => makeItem(item, index, "openai-responses", "assistant"));
        if (output.length || !failed) return output;
      }

      if (Array.isArray(body.candidates)) {
        const candidates = body.candidates
          .map((candidate) => candidate?.content)
          .filter((content) => content !== undefined && content !== null)
          .map((content, index) => makeItem(content, index, "gemini", "model"));
        if (candidates.length || !failed) return candidates;
      }
    }

    if (Array.isArray(body) && body.length === 0) return [];
    if (body && typeof body === "object" && !Array.isArray(body) && Object.keys(body).length === 0) return [];
    if (!failed && body && typeof body === "object" && !Array.isArray(body)) {
      const envelopeOnly = Object.keys(body).every(
        (key) =>
          [
            "id",
            "model",
            "object",
            "created",
            "created_at",
            "status",
            "stop_reason",
            "stop_sequence",
            "system_fingerprint",
            "service_tier",
            "usage",
            "usage_metadata",
            "usageMetadata",
          ].includes(key) || /(?:^|_)(?:input|output|prompt|completion|cached)?_?tokens?$/.test(key),
      );
      if (envelopeOnly) return [];
    }
    return [makeItem(body, 0, "unknown", "assistant", failed ? "response_error" : "response_fallback")];
  }

  function requestTools(body) {
    if (!body || typeof body !== "object") return [];
    const found = [];
    const visit = (tool, namespace = "") => {
      if (!tool || typeof tool !== "object") return;
      const declarations = tool.function_declarations || tool.functionDeclarations;
      if (Array.isArray(declarations)) {
        for (const declaration of declarations) {
          if (!declaration || typeof declaration !== "object") continue;
          found.push({
            name: declaration.name || "function",
            description: declaration.description || "",
            schema: declaration.parameters || declaration.parametersJsonSchema || null,
            kind: "function",
          });
        }
        return;
      }
      if (tool.type === "namespace" && Array.isArray(tool.tools)) {
        const prefix = namespace ? `${namespace}.${tool.name || ""}` : tool.name || "";
        for (const child of tool.tools) visit(child, prefix);
        return;
      }
      const baseName = tool.function?.name || tool.name || tool.type || "tool";
      const name = namespace && !String(baseName).includes(".") ? `${namespace}.${baseName}` : baseName;
      const definition = tool.function && typeof tool.function === "object" ? tool.function : tool;
      let schema = definition.parameters || definition.input_schema || definition.parametersJsonSchema || null;
      if (!schema && (definition.format || definition.grammar)) {
        schema = definition.format || definition.grammar;
      }
      if (!schema) {
        const configuration = {};
        for (const [key, value] of Object.entries(tool)) {
          if (["type", "name", "description", "function", "tools"].includes(key)) continue;
          configuration[key] = value;
        }
        if (Object.keys(configuration).length) schema = configuration;
      }
      found.push({
        name,
        description: definition.description || tool.description || "",
        schema,
        kind: tool.type || (tool.function ? "function" : "tool"),
        strict: definition.strict ?? tool.strict,
      });
    };
    for (const tool of body.tools || []) visit(tool);
    for (const item of body.input || []) {
      if (item?.type === "additional_tools") for (const tool of item.tools || []) visit(tool);
    }
    return found;
  }

  function parseMaybeJson(value) {
    if (typeof value !== "string") return value;
    const trimmed = value.trim();
    if (!trimmed || !["{", "["].includes(trimmed[0])) return value;
    try {
      return JSON.parse(trimmed);
    } catch (_) {
      return value;
    }
  }

  function toolCallFromBlock(block, raw = block) {
    if (!block || typeof block !== "object") return null;
    if (block.functionCall && typeof block.functionCall === "object") {
      return {
        id: block.functionCall.id || block.id || "",
        name: block.functionCall.name || "function",
        value: block.functionCall.args,
        raw,
      };
    }
    const type = String(block.type || "");
    const func = block.function && typeof block.function === "object" ? block.function : {};
    const isCall =
      type === "tool_use" ||
      type === "function_call" ||
      type.endsWith("_call") ||
      type.endsWith("_tool_use") ||
      Boolean(func.name);
    if (!isCall) return null;
    const explicitValue = block.input ?? block.arguments ?? func.arguments ?? block.args ?? block.action;
    const fallbackValue = {};
    for (const [key, value] of Object.entries(block)) {
      if (["type", "id", "call_id", "name", "status", "function"].includes(key)) continue;
      fallbackValue[key] = value;
    }
    return {
      // Responses uses an item id and a separate call_id. The latter is the
      // foreign key carried by function_call_output, so prefer it for pairing.
      id: block.call_id || block.tool_use_id || block.id || "",
      name: block.name || func.name || type.replace(/_(call|tool_use)$/, "") || "tool",
      value: explicitValue ?? fallbackValue,
      raw,
    };
  }

  function toolResultFromBlock(block, role = "") {
    if (!block || typeof block !== "object") return null;
    if (block.functionResponse && typeof block.functionResponse === "object") {
      return {
        id: block.functionResponse.id || block.id || "",
        name: block.functionResponse.name || "",
        value: block.functionResponse.response,
        isError: Boolean(block.functionResponse.error || block.functionResponse.response?.error),
        raw: block,
      };
    }
    const type = String(block.type || "");
    const isResult =
      type === "tool_result" || type.endsWith("_call_output") || type.endsWith("_tool_result") || role === "tool";
    if (!isResult) return null;
    const explicitValue = block.content ?? block.output ?? block.response ?? block.result;
    const fallbackValue = {};
    for (const [key, value] of Object.entries(block)) {
      if (
        ["type", "id", "call_id", "tool_use_id", "tool_call_id", "name", "status", "is_error"].includes(key)
      ) {
        continue;
      }
      fallbackValue[key] = value;
    }
    return {
      id: block.tool_use_id || block.tool_call_id || block.call_id || block.id || "",
      name: block.name || "",
      value: explicitValue ?? fallbackValue,
      isError: Boolean(block.is_error || block.error || block.status === "error" || block.status === "failed"),
      raw: block,
    };
  }

  function roleLabel(role) {
    const normalized = String(role || "context")
      .trim()
      .toLowerCase()
      .replace(/[_-]+/g, " ")
      .replace(/\s+/g, " ");
    return normalized ? `${normalized[0].toUpperCase()}${normalized.slice(1)}` : "Context";
  }

  const JSON_OBJECT_PREVIEW_LIMIT = 4;
  const JSON_ARRAY_PREVIEW_LIMIT = 5;
  const JSON_PREVIEW_DEPTH_LIMIT = 2;

  function isJsonContainer(value) {
    return value !== null && typeof value === "object";
  }

  function jsonEntries(value) {
    if (Array.isArray(value)) return value.map((item, index) => [String(index), item]);
    return Object.keys(value || {}).map((key) => [key, value[key]]);
  }

  function jsonBrackets(value) {
    return Array.isArray(value) ? ["[", "]"] : ["{", "}"];
  }

  function jsonPathKey(path) {
    return JSON.stringify(path);
  }

  function jsonNodeId(path) {
    if (!path.length) return "root";
    return path
      .map((part) => (typeof part === "number" ? `n${part}` : `s${String(part).length}:${String(part)}`))
      .join("/");
  }

  function formattedJsonPath(path) {
    return path.reduce((result, part) => {
      if (typeof part === "number") return `${result}[${part}]`;
      return /^[A-Za-z_$][\w$]*$/.test(part) ? `${result}.${part}` : `${result}[${JSON.stringify(part)}]`;
    }, "$");
  }

  function jsonPrimitiveHtml(value) {
    if (value === null) return '<span class="json-keyword">null</span>';
    if (typeof value === "string") return `<span class="json-string">${escapeHtml(JSON.stringify(value))}</span>`;
    if (typeof value === "number") return `<span class="json-number">${escapeHtml(String(value))}</span>`;
    if (typeof value === "boolean") return `<span class="json-keyword">${String(value)}</span>`;
    return `<span class="json-other">${escapeHtml(String(value))}</span>`;
  }

  function jsonPreviewHtml(value, depth = 0) {
    if (!isJsonContainer(value)) {
      return jsonPrimitiveHtml(value);
    }
    const array = Array.isArray(value);
    const entries = jsonEntries(value);
    const limit = array ? JSON_ARRAY_PREVIEW_LIMIT : JSON_OBJECT_PREVIEW_LIMIT;
    const [open, close] = jsonBrackets(value);
    if (depth >= JSON_PREVIEW_DEPTH_LIMIT) {
      return `<span class="json-punctuation">${open}</span><span class="json-ellipsis">…</span><span class="json-punctuation">${close}</span>`;
    }
    const visible = entries.slice(0, limit).map(([key, item], index) => {
      const prefix = index ? '<span class="json-punctuation">, </span>' : "";
      const field = array
        ? ""
        : `<span class="json-preview-key">${escapeHtml(key)}</span><span class="json-punctuation">: </span>`;
      return `${prefix}${field}${jsonPreviewHtml(item, depth + 1)}`;
    });
    if (entries.length > limit) visible.push('<span class="json-ellipsis">, …</span>');
    return `<span class="json-punctuation">${open}</span>${visible.join("")}<span class="json-punctuation">${close}</span>`;
  }

  let jsonTreeSerial = 0;

  class JsonTree {
    constructor(host, value, options = {}) {
      host.__claudeTapJsonTree?.destroy();
      this.host = host;
      this.value = value;
      this.query = String(options.query || "").trim().toLowerCase();
      this.expanded = options.expanded || new Set();
      this.fullStrings = options.fullStrings || new Set();
      this.wrapLines = options.wrapLines !== false;
      this.labels = options.labels || {};
      this.nodes = new Map();
      this.visiblePaths = new Set();
      this.directPaths = new Set();
      this.descendantPaths = new Set();
      this.treeId = ++jsonTreeSerial;
      this.tabStopId = null;
      this.activeRow = null;
      this.copyTarget = null;
      this.copyMenuOpen = false;
      this.copyResetTimer = 0;
      this._scanMatches();
      this.host.onclick = (event) => this._onClick(event);
      this.host.onkeydown = (event) => this._onKeyDown(event);
      this.host.onfocusin = (event) => this._onFocusIn(event);
      this.host.onmouseover = (event) => this._onMouseOver(event);
      this.host.onmouseleave = () => {
        if (!this.copyMenuOpen) this._clearCopyTarget();
      };
      this.host.oncontextmenu = (event) => this._onContextMenu(event);
      this._repositionCopyButton = () => this._positionCopyButton();
      this.scrollParent = this.host.closest?.("#inspector-body") || this.host;
      this.host.addEventListener("scroll", this._repositionCopyButton, { capture: true, passive: true });
      if (this.scrollParent !== this.host) {
        this.scrollParent.addEventListener("scroll", this._repositionCopyButton, { passive: true });
      }
      window.addEventListener("resize", this._repositionCopyButton);
      this.host.__claudeTapJsonTree = this;
      this.render();
    }

    destroy() {
      window.clearTimeout(this.copyResetTimer);
      if (this._outsideCopyMenu) document.removeEventListener("pointerdown", this._outsideCopyMenu, true);
      this.host.removeEventListener("scroll", this._repositionCopyButton, true);
      if (this.scrollParent !== this.host) {
        this.scrollParent.removeEventListener("scroll", this._repositionCopyButton);
      }
      window.removeEventListener("resize", this._repositionCopyButton);
      this._clearCopyTarget();
      if (this.host.__claudeTapJsonTree === this) {
        this.host.onclick = null;
        this.host.onkeydown = null;
        this.host.onfocusin = null;
        this.host.onmouseover = null;
        this.host.onmouseleave = null;
        this.host.oncontextmenu = null;
        delete this.host.__claudeTapJsonTree;
      }
    }

    _scanMatches() {
      if (!this.query) return;
      let visited = 0;
      const visit = (value, path, field, ancestors, depth) => {
        if (visited >= 50_000 || depth > 80) return false;
        visited += 1;
        const key = jsonPathKey(path);
        const fieldMatches = String(field ?? "").toLowerCase().includes(this.query);
        const valueMatches = !isJsonContainer(value) && String(value ?? "").toLowerCase().includes(this.query);
        if (fieldMatches || valueMatches) this.directPaths.add(key);
        let childMatches = false;
        if (isJsonContainer(value)) {
          for (const [childField, child] of jsonEntries(value)) {
            const childPath = [...path, Array.isArray(value) ? Number(childField) : childField];
            if (visit(child, childPath, childField, [...ancestors, key], depth + 1)) childMatches = true;
          }
        }
        const matched = fieldMatches || valueMatches || childMatches;
        if (matched) {
          this.visiblePaths.add(key);
          for (const ancestor of ancestors) this.visiblePaths.add(ancestor);
        }
        if (childMatches) this.descendantPaths.add(key);
        return matched;
      };
      visit(this.value, [], "", [], 0);
    }

    _register(path, value) {
      const id = jsonNodeId(path);
      this.nodes.set(id, { path, value });
      return id;
    }

    _fieldHtml(field, id = "", expandable = false) {
      if (field === undefined) return "";
      const toggle = expandable ? ` data-json-action="toggle" data-json-node="${escapeHtml(id)}"` : "";
      return `<span class="json-label${expandable ? " json-clickable-label" : ""}" style="white-space:nowrap"${toggle}>${escapeHtml(
        field || '""',
      )}:</span>`;
    }

    _expander(id, expanded) {
      const controlsId = `json-children-${this.treeId}-${encodeURIComponent(id)}`;
      return `<button class="json-expander${expanded ? " expanded" : ""}" type="button" data-json-expander data-json-action="toggle" data-json-node="${escapeHtml(id)}" aria-expanded="${expanded}"${
        expanded ? ` aria-controls="${escapeHtml(controlsId)}"` : ""
      } aria-label="${escapeHtml(
        expanded ? this.labels.collapse || "Collapse JSON node" : this.labels.expand || "Expand JSON node",
      )}" tabindex="${this.tabStopId === id ? "0" : "-1"}"><span data-icon="chevron" data-icon-size="12"></span></button>`;
    }

    _rowHtml(contents, id, { expanded, root = false } = {}) {
      const ariaExpanded = typeof expanded === "boolean" ? ` aria-expanded="${expanded}"` : "";
      return `<div class="json-row${root ? " json-root-row" : ""}"${root ? "" : ' role="treeitem"'}${ariaExpanded} data-json-node="${escapeHtml(
        id,
      )}">${contents}</div>`;
    }

    _nodeHtml(value, path, field, last = true) {
      const pathKey = jsonPathKey(path);
      if (this.query && !this.visiblePaths.has(pathKey)) return "";
      const id = this._register(path, value);
      const comma = last ? "" : '<span class="json-punctuation">,</span>';

      if (!isJsonContainer(value)) {
        const fieldHtml = this._fieldHtml(field);
        return `<div class="json-node json-primitive-node" role="presentation">${this._rowHtml(
          `${fieldHtml}${jsonPrimitiveHtml(value)}${comma}`,
          id,
        )}</div>`;
      }

      const entries = jsonEntries(value);
      const [open, close] = jsonBrackets(value);
      if (!entries.length) {
        const fieldHtml = this._fieldHtml(field);
        return `<div class="json-node json-empty-node" role="presentation">${this._rowHtml(
          `${fieldHtml}<span class="json-punctuation">${open}${close}</span>${comma}`,
          id,
        )}</div>`;
      }
      const expanded = this.expanded.has(pathKey) || Boolean(this.query && this.descendantPaths.has(pathKey));
      const fieldHtml = this._fieldHtml(field, id, true);
      const visibleEntries = this.query
        ? entries.filter(([key]) => {
            const childPath = [...path, Array.isArray(value) ? Number(key) : key];
            return this.visiblePaths.has(jsonPathKey(childPath));
          })
        : entries;
      // Collapsed branches deliberately do not recurse. Besides matching the
      // reference interaction, this keeps huge request bodies cheap to open.
      const children = expanded
        ? visibleEntries
            .map(([key, item], index) =>
              this._nodeHtml(
                item,
                [...path, Array.isArray(value) ? Number(key) : key],
                key,
                index === visibleEntries.length - 1,
              ),
            )
            .join("")
        : "";
      const controlsId = `json-children-${this.treeId}-${encodeURIComponent(id)}`;
      const row = this._rowHtml(
        `${this._expander(id, expanded)}${fieldHtml}<span class="json-preview">${jsonPreviewHtml(value)}</span>${comma}`,
        id,
        { expanded },
      );
      return `<div class="json-node json-container-node" role="presentation">${row}${
        expanded ? `<ul class="json-children" id="${escapeHtml(controlsId)}" role="group">${children}</ul>` : ""
      }</div>`;
    }

    _copyControlsHtml() {
      const copyLabel = this.labels.copyValue || this.labels.copy || "Copy value";
      return `<span class="json-copy-anchor" data-json-copy-anchor hidden>
        <button class="json-copy json-copy-button" type="button" data-json-copy-button data-json-action="copy-default" aria-label="${escapeHtml(
          copyLabel,
        )}" title="${escapeHtml(copyLabel)}; right-click for copy options"><span data-icon="copy" data-icon-size="12"></span></button>
        <div class="json-copy-menu json-context-menu" data-json-copy-menu role="menu" hidden></div>
      </span>`;
    }

    render() {
      this._clearCopyTarget();
      this.nodes.clear();
      const value = this.value;
      if (!isJsonContainer(value)) {
        this.host.innerHTML = `<div class="json-tree${this.wrapLines ? " wrap-lines" : ""}"><div role="tree" aria-label="JSON">${this._nodeHtml(
          value,
          [],
          undefined,
        )}</div>${this._copyControlsHtml()}</div>`;
        hydrateIcons(this.host);
        this._syncTabStop();
        return;
      }
      const rootKey = jsonPathKey([]);
      if (this.query && !this.visiblePaths.has(rootKey)) {
        this.host.innerHTML = `<div class="inspector-empty">${escapeHtml(this.labels.noMatches || "No matches")}</div>`;
        return;
      }
      const rootId = this._register([], value);
      const entries = jsonEntries(value);
      const [open, close] = jsonBrackets(value);
      const visibleEntries = this.query
        ? entries.filter(([key]) => {
            const childPath = [Array.isArray(value) ? Number(key) : key];
            return this.visiblePaths.has(jsonPathKey(childPath));
          })
        : entries;
      const children = visibleEntries
        .map(([key, item], index) =>
          this._nodeHtml(item, [Array.isArray(value) ? Number(key) : key], key, index === visibleEntries.length - 1),
        )
        .join("");
      this.host.innerHTML = `<div class="json-tree${this.wrapLines ? " wrap-lines" : ""}"><div class="json-expanded-top-level">${this._rowHtml(
        `<span class="json-punctuation">${open}</span>`,
        rootId,
        { root: true },
      )}<div class="json-root-children" role="tree" aria-label="JSON">${children}</div><div class="json-row json-close"><span class="json-punctuation">${close}</span></div></div>${this._copyControlsHtml()}</div>`;
      hydrateIcons(this.host);
      this._syncTabStop();
    }

    async _onClick(event) {
      const control = event.target.closest?.("[data-json-action]");
      if (!control || !this.host.contains(control)) return;
      const action = control.dataset.jsonAction;
      if (action === "toggle") {
        const target = this.nodes.get(control.dataset.jsonNode);
        if (!target || !isJsonContainer(target.value)) return;
        const key = jsonPathKey(target.path);
        if (this.expanded.has(key)) this.expanded.delete(key);
        else this.expanded.add(key);
        this.tabStopId = control.dataset.jsonNode;
        this.render();
        requestAnimationFrame(() => {
          const expander = Array.from(this.host.querySelectorAll("[data-json-expander]")).find(
            (item) => item.dataset.jsonNode === control.dataset.jsonNode,
          );
          expander?.focus();
        });
      } else if (action === "copy-default") {
        await this._copy(this._copyTargetIsObject() ? "prettyJson" : "value");
        if (this.copyMenuOpen) this._closeCopyMenu(false);
      } else if (action === "copy-mode") {
        await this._copy(control.dataset.copyMode);
        this._closeCopyMenu(false);
      }
    }

    _onKeyDown(event) {
      const menuItem = event.target.closest?.('[data-json-action="copy-mode"]');
      if (menuItem) {
        const items = Array.from(this.host.querySelectorAll('[data-json-action="copy-mode"]'));
        if (["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) {
          event.preventDefault();
          const current = items.indexOf(menuItem);
          const next =
            event.key === "Home"
              ? 0
              : event.key === "End"
                ? items.length - 1
                : (current + (event.key === "ArrowDown" ? 1 : -1) + items.length) % items.length;
          items[next]?.focus();
        } else if (event.key === "Escape") {
          event.preventDefault();
          event.stopPropagation();
          this._closeCopyMenu(true);
        }
        return;
      }
      const button = event.target.closest?.("[data-json-expander]");
      if (!button) return;
      if (event.key === "ArrowRight" || event.key === "ArrowLeft") {
        event.preventDefault();
        const wantsExpanded = event.key === "ArrowRight";
        if ((button.getAttribute("aria-expanded") === "true") !== wantsExpanded) button.click();
      } else if (event.key === "ArrowUp" || event.key === "ArrowDown") {
        event.preventDefault();
        const buttons = Array.from(this.host.querySelectorAll("[data-json-expander]"));
        const current = buttons.indexOf(button);
        const offset = event.key === "ArrowUp" ? -1 : 1;
        buttons[(current + offset + buttons.length) % buttons.length]?.focus();
      }
    }

    _onFocusIn(event) {
      const expander = event.target.closest?.("[data-json-expander]");
      if (!expander) return;
      this.tabStopId = expander.dataset.jsonNode;
      this._syncTabStop();
    }

    _syncTabStop() {
      const expanders = Array.from(this.host.querySelectorAll("[data-json-expander]"));
      if (!expanders.length) return;
      if (!expanders.some((item) => item.dataset.jsonNode === this.tabStopId)) {
        this.tabStopId = expanders[0].dataset.jsonNode;
      }
      for (const item of expanders) item.tabIndex = item.dataset.jsonNode === this.tabStopId ? 0 : -1;
    }

    _onMouseOver(event) {
      if (this.copyMenuOpen || event.target.closest?.("[data-json-copy-anchor]")) return;
      const row = event.target.closest?.(".json-row[data-json-node]");
      if (!row || !this.host.contains(row) || row === this.activeRow) return;
      this.activeRow?.removeAttribute("data-json-copy-active");
      this.activeRow = row;
      row.setAttribute("data-json-copy-active", "");
      this.copyTarget = this.nodes.get(row.dataset.jsonNode) || null;
      this._setCopyState("idle");
      this._positionCopyButton();
    }

    _positionCopyButton() {
      if (!this.activeRow || !this.copyTarget || !this.activeRow.isConnected) return;
      const anchor = this.host.querySelector("[data-json-copy-anchor]");
      if (!anchor) return;
      const root = this.host;
      const rootRect = root.getBoundingClientRect();
      const rowRect = this.activeRow.getBoundingClientRect();
      anchor.hidden = false;
      anchor.style.left = `${rootRect.left + root.clientWidth - 26}px`;
      anchor.style.top = `${rowRect.top}px`;
      anchor.dataset.side = rowRect.top - rootRect.top > root.clientHeight / 2 ? "top" : "bottom";
      const label = this._copyTargetIsObject()
        ? this.labels.copyPrettyJson || "Copy pretty JSON"
        : this.labels.copyValue || this.labels.copy || "Copy value";
      const button = anchor.querySelector("[data-json-copy-button]");
      button?.setAttribute("aria-label", label);
      button?.setAttribute("title", `${label}; right-click for copy options`);
    }

    _onContextMenu(event) {
      const button = event.target.closest?.("[data-json-copy-button]");
      if (!button || !this.copyTarget) return;
      event.preventDefault();
      event.stopPropagation();
      this._openCopyMenu();
    }

    _openCopyMenu() {
      const menu = this.host.querySelector("[data-json-copy-menu]");
      if (!menu || !this.copyTarget) return;
      const object = this._copyTargetIsObject();
      const items = object
        ? [
            ["prettyJson", this.labels.copyPrettyJson || "Copy pretty JSON"],
            ["json", this.labels.copyCompactJson || "Copy compact JSON"],
            ["path", this.labels.copyPath || "Copy property path"],
          ]
        : [
            ["value", this.labels.copyValue || this.labels.copy || "Copy value"],
            ["json", this.labels.copyJson || "Copy JSON"],
            ["path", this.labels.copyPath || "Copy property path"],
          ];
      menu.innerHTML = items
        .map(
          ([mode, label]) =>
            `<button class="json-copy-menu-item" type="button" role="menuitem" data-json-action="copy-mode" data-copy-mode="${mode}">${escapeHtml(
              label,
            )}</button>`,
        )
        .join("");
      menu.hidden = false;
      this.copyMenuOpen = true;
      requestAnimationFrame(() => menu.querySelector('[role="menuitem"]')?.focus());
      this._outsideCopyMenu = (outsideEvent) => {
        if (!menu.contains(outsideEvent.target) && !outsideEvent.target.closest?.("[data-json-copy-button]")) {
          this._closeCopyMenu(false);
        }
      };
      document.addEventListener("pointerdown", this._outsideCopyMenu, { capture: true, once: true });
    }

    _closeCopyMenu(restoreFocus) {
      const menu = this.host.querySelector("[data-json-copy-menu]");
      if (menu) menu.hidden = true;
      this.copyMenuOpen = false;
      if (restoreFocus) this.host.querySelector("[data-json-copy-button]")?.focus();
    }

    _copyTargetIsObject() {
      return Boolean(this.copyTarget && isJsonContainer(this.copyTarget.value));
    }

    _copyValue(mode) {
      if (!this.copyTarget) return "";
      const { path, value } = this.copyTarget;
      if (mode === "path") return formattedJsonPath(path);
      if (mode === "prettyJson") return safeJson(value);
      if (mode === "json") {
        try {
          return JSON.stringify(value);
        } catch (_) {
          return safeJson(value, 0);
        }
      }
      if (typeof value === "string") return value;
      if (value === undefined) return "undefined";
      if (typeof value === "bigint") return value.toString();
      if (typeof value === "symbol") return value.description || "Symbol";
      if (typeof value === "function") return value.name || "Function";
      try {
        return JSON.stringify(value);
      } catch (_) {
        return String(value);
      }
    }

    async _copy(mode) {
      if (!this.copyTarget) return;
      const value = this._copyValue(mode);
      try {
        await copyText(value);
        this._setCopyState("copied");
      } catch (_) {
        // Clipboard API is commonly exposed but denied on file:// previews.
        // Keep the reference interaction useful by falling back to the same
        // selection-based copy path used by older browsers.
        try {
          const area = document.createElement("textarea");
          area.value = String(value ?? "");
          area.style.position = "fixed";
          area.style.opacity = "0";
          document.body.appendChild(area);
          area.select();
          document.execCommand("copy");
          area.remove();
          this._setCopyState("copied");
        } catch (_) {
          this._setCopyState("failed");
        }
      }
      window.clearTimeout(this.copyResetTimer);
      this.copyResetTimer = window.setTimeout(() => this._setCopyState("idle"), 1_500);
    }

    _setCopyState(state) {
      const button = this.host.querySelector("[data-json-copy-button]");
      if (!button) return;
      button.dataset.state = state;
      const label =
        state === "copied"
          ? this.labels.copied || "Copied"
          : state === "failed"
            ? this.labels.copyFailed || "Copy failed"
            : this._copyTargetIsObject()
              ? this.labels.copyPrettyJson || "Copy pretty JSON"
              : this.labels.copyValue || this.labels.copy || "Copy value";
      button.setAttribute("aria-label", label);
      button.innerHTML = state === "copied" ? icon("check", 12) : icon("copy", 12);
    }

    _clearCopyTarget() {
      window.clearTimeout(this.copyResetTimer);
      this.activeRow?.removeAttribute("data-json-copy-active");
      this.activeRow = null;
      this.copyTarget = null;
      this.copyMenuOpen = false;
      const anchor = this.host.querySelector?.("[data-json-copy-anchor]");
      if (anchor) anchor.hidden = true;
    }
  }

  class Inspector {
    constructor(translator, toast) {
      this.translator = translator;
      this.toast = toast;
      this.metadata = null;
      this.record = null;
      this.activeTab = "conversation";
      this.fullTabs = new Set();
      this.filters = new Map();
      this.scrollPositions = new Map();
      this.sharedScrollPositions = new Map();
      this.nestedScrollPositions = new Map();
      this.sharedNestedScrollPositions = new Map();
      this.appliedScrollTop = null;
      this.appliedNestedScrollTops = new Map();
      this.pendingScrollRestore = null;
      this.scrollRestoreFrame = 0;
      this.scrollRestoreTimers = [];
      this.codeCache = new LruCache(12);
      this.jsonStates = new Map();
      this.inlineJsonStates = new Map();
      this.pendingJsonTrees = [];
      this.inlineJsonInstances = [];
      this.inlineJsonSerial = 0;
      this.wrapLines = localStorage.getItem("claude-tap-wrap-lines") === "true";
      this.onClose = null;
      this.onOpen = null;
      this.optionsMenu = new DropdownMenu(
        $("inspector-options-trigger"),
        $("inspector-options-menu"),
        () => this._optionItems(),
        (id) => this._handleOption(id),
      );
      this._bind();
    }

    get t() {
      return (key) => this.translator.t(key);
    }

    _bind() {
      $("close-inspector").addEventListener("click", () => this.close());
      $("copy-active-view").addEventListener("click", () => this._copy(this.currentViewText()));
      const updateFilter = debounce(({ key, value }) => {
        this._remember(this.filters, key, value, 120);
        if (key === this._viewKey()) this.renderBody(false);
      }, 90);
      $("inspector-filter").addEventListener("input", (event) => {
        updateFilter({ key: this._viewKey(), value: event.target.value });
      });
      const inspectorBody = $("inspector-body");
      const claimScroll = () => this._cancelScrollRestore();
      inspectorBody.addEventListener("wheel", claimScroll, { passive: true });
      inspectorBody.addEventListener("pointerdown", claimScroll, { passive: true });
      inspectorBody.addEventListener("touchstart", claimScroll, { passive: true });
      inspectorBody.addEventListener("keydown", (event) => {
        if (["ArrowUp", "ArrowDown", "PageUp", "PageDown", "Home", "End", " "].includes(event.key)) {
          claimScroll();
        }
      });
      this._bindResize();
    }

    _bindResize() {
      const handle = $("resize-handle");
      let dragging = false;
      const limits = () => {
        const sidebarWidth = $("session-panel")?.getBoundingClientRect().width || 0;
        const max = Math.max(320, window.innerWidth - sidebarWidth - 216);
        return { min: Math.min(420, max), max };
      };
      const defaultWidth = () => {
        const { min, max } = limits();
        return Math.max(min, Math.min(max, Math.round(window.innerWidth * 0.56)));
      };
      const currentWidth = () => {
        const rendered = $("inspector")?.getBoundingClientRect().width || 0;
        const saved = Number(localStorage.getItem("claude-tap-inspector-width"));
        return rendered > 0 ? rendered : Number.isFinite(saved) && saved > 0 ? saved : defaultWidth();
      };
      const applyWidth = (requested) => {
        const { min, max } = limits();
        const width = Math.round(Math.max(min, Math.min(max, requested)));
        // Direct manipulation must track the pointer/key immediately; the
        // shell's open/close easing otherwise leaves ARIA and pixels out of
        // sync for the duration of the grid transition.
        const workspace = $("workspace");
        workspace.style.transition = "none";
        document.documentElement.style.setProperty("--inspector-width", `${width}px`);
        localStorage.setItem("claude-tap-inspector-width", String(width));
        handle.setAttribute("aria-valuemin", String(min));
        handle.setAttribute("aria-valuemax", String(max));
        handle.setAttribute("aria-valuenow", String(width));
        void workspace.offsetWidth;
        if (!dragging) requestAnimationFrame(() => workspace.style.removeProperty("transition"));
        return width;
      };
      const move = (event) => {
        if (!dragging || window.innerWidth < 800) return;
        applyWidth(window.innerWidth - event.clientX);
      };
      handle.tabIndex = 0;
      handle.addEventListener("pointerdown", (event) => {
        if (window.innerWidth < 800) return;
        event.preventDefault();
        dragging = true;
        document.body.classList.add("resizing-panel");
        handle.setPointerCapture(event.pointerId);
      });
      handle.addEventListener("pointermove", move);
      const finish = () => {
        dragging = false;
        document.body.classList.remove("resizing-panel");
        $("workspace").style.removeProperty("transition");
      };
      handle.addEventListener("pointerup", finish);
      handle.addEventListener("pointercancel", finish);
      handle.addEventListener("keydown", (event) => {
        if (window.innerWidth < 800) return;
        const { min, max } = limits();
        let next = null;
        if (event.key === "Home") next = defaultWidth();
        else if (event.key === "End") next = max;
        else if (event.key === "ArrowLeft") next = currentWidth() + (event.shiftKey ? 64 : 16);
        else if (event.key === "ArrowRight") next = currentWidth() - (event.shiftKey ? 64 : 16);
        if (next === null) return;
        event.preventDefault();
        applyWidth(Math.max(min, Math.min(max, next)));
      });
      handle.addEventListener("dblclick", () => {
        if (window.innerWidth < 800) return;
        const { max } = limits();
        const baseline = defaultWidth();
        applyWidth(currentWidth() > baseline + 12 ? baseline : max);
      });
      const saved = Number(localStorage.getItem("claude-tap-inspector-width"));
      applyWidth(Number.isFinite(saved) && saved > 0 ? saved : defaultWidth());
      window.addEventListener("resize", () => applyWidth(currentWidth()));
    }

    _optionItems() {
      return [
        { id: "copy-request", label: this.t("copyRequest"), icon: "copy" },
        { id: "copy-response", label: this.t("copyResponse"), icon: "copy" },
        { id: "copy-curl", label: this.t("copyCurl"), icon: "code" },
        { separator: true },
        { id: "headers", label: this.t("headers"), icon: "code" },
        { id: "toggle-wrap", label: this.wrapLines ? this.t("unwrap") : this.t("wrap"), icon: "code" },
      ];
    }

    _handleOption(id) {
      if (id === "copy-request") this._copy(this.record?.request || {});
      else if (id === "copy-response") this._copy(this.record?.response || {});
      else if (id === "copy-curl") this._copy(requestToCurl(this.record));
      else if (id === "headers") this.selectTab("headers");
      else if (id === "toggle-wrap") {
        this.wrapLines = !this.wrapLines;
        localStorage.setItem("claude-tap-wrap-lines", String(this.wrapLines));
        this._rerenderPreservingScroll();
      }
    }

    async _copy(value) {
      if (!this.record) return;
      try {
        await copyText(typeof value === "string" ? value : safeJson(value));
        this.toast(this.t("copied"));
      } catch (_) {
        this.toast(this.t("copyFailed"), true);
      }
    }

    _viewKey(tab = this.activeTab, metadata = this.metadata) {
      return `${metadata?.capture_id || "capture"}:${metadata?.session_key || "session"}:${metadata?.record_index ?? "none"}:${tab}`;
    }

    _sharedViewKey(tab = this.activeTab, metadata = this.metadata) {
      return `${metadata?.capture_id || "capture"}:${tab}`;
    }

    _savedScrollTop() {
      const recordKey = this._viewKey();
      if (this.scrollPositions.has(recordKey)) return this.scrollPositions.get(recordKey);
      return this.sharedScrollPositions.get(this._sharedViewKey()) || 0;
    }

    _nestedScrollSurfaces() {
      const host = $("inspector-body");
      const selector = [
        ".message-text-block.long-content",
        ".message-thinking-block.long-content",
        ".message-record-block.long-content",
        ".message-tool-data",
      ].join(",");
      return [...host.querySelectorAll(selector)];
    }

    _nestedScrollKey(element) {
      const message = element.closest(".request-message");
      if (!message) return "";
      const kind = element.classList.contains("message-tool-data")
        ? "tool-data"
        : element.dataset.contentType || "content";
      return [
        message.dataset.origin || "request",
        message.dataset.role || "context",
        message.dataset.messageIndex || "0",
        message.dataset.blockPath || message.dataset.blockIndex || "0",
        message.dataset.blockType || "text",
        message.dataset.toolPhase || "",
        message.dataset.callId || "",
        kind,
      ].join(":");
    }

    _nestedRecordKey(surfaceKey) {
      return `${this._viewKey()}:surface:${surfaceKey}`;
    }

    _nestedSharedKey(surfaceKey) {
      return `${this._sharedViewKey()}:surface:${surfaceKey}`;
    }

    _savedNestedScrollTop(surfaceKey) {
      const recordKey = this._nestedRecordKey(surfaceKey);
      if (this.nestedScrollPositions.has(recordKey)) return this.nestedScrollPositions.get(recordKey);
      return this.sharedNestedScrollPositions.get(this._nestedSharedKey(surfaceKey)) || 0;
    }

    _remember(map, key, value, limit = 160) {
      if (map.has(key)) map.delete(key);
      map.set(key, value);
      while (map.size > limit) map.delete(map.keys().next().value);
      return value;
    }

    _saveScroll() {
      if (!this.metadata || !this.record) return;
      const key = this._viewKey();
      // While the grid is settling, scrollTop can be clamped to a temporary
      // maxScroll. Keep the intended position instead of overwriting it with
      // that transient value. Explicit user input cancels the pending restore.
      if (this.pendingScrollRestore?.key === key) return;
      const body = $("inspector-body");
      const top = body.scrollTop;
      this._remember(this.scrollPositions, key, top, 320);
      if (this.appliedScrollTop === null || Math.abs(top - this.appliedScrollTop) > 1) {
        this._remember(this.sharedScrollPositions, this._sharedViewKey(), top, 48);
      }
      for (const surface of this._nestedScrollSurfaces()) {
        const surfaceKey = this._nestedScrollKey(surface);
        if (!surfaceKey) continue;
        const surfaceTop = surface.scrollTop;
        this._remember(this.nestedScrollPositions, this._nestedRecordKey(surfaceKey), surfaceTop, 960);
        const appliedTop = this.appliedNestedScrollTops.get(surfaceKey);
        if (appliedTop === undefined || Math.abs(surfaceTop - appliedTop) > 1) {
          this._remember(this.sharedNestedScrollPositions, this._nestedSharedKey(surfaceKey), surfaceTop, 480);
        }
      }
    }

    _rerenderPreservingScroll() {
      if (!this.record) return;
      this._cancelScrollRestore();
      this._saveScroll();
      this.renderBody(true);
    }

    _restoreScroll() {
      this._cancelScrollRestore();
      const key = this._viewKey();
      const top = this._savedScrollTop();
      const nested = new Map();
      for (const surface of this._nestedScrollSurfaces()) {
        const surfaceKey = this._nestedScrollKey(surface);
        if (surfaceKey) nested.set(surfaceKey, this._savedNestedScrollTop(surfaceKey));
      }
      this.appliedScrollTop = null;
      this.appliedNestedScrollTops = new Map();
      this.pendingScrollRestore = { key, top, nested };
      this._queueScrollRestoreFrame();
      // The shell transition is 200ms. Retry on both sides of that boundary;
      // the final pass also covers content-visibility realizing nearby blocks.
      this.scrollRestoreTimers = [80, 230, 380].map((delay, index, delays) =>
        window.setTimeout(() => this._applyScrollRestore(index === delays.length - 1), delay),
      );
    }

    _queueScrollRestoreFrame() {
      if (this.scrollRestoreFrame) return;
      this.scrollRestoreFrame = requestAnimationFrame(() => {
        this.scrollRestoreFrame = 0;
        this._applyScrollRestore(false);
      });
    }

    _applyScrollRestore(finalAttempt) {
      const pending = this.pendingScrollRestore;
      if (!pending || pending.key !== this._viewKey()) return;
      const body = $("inspector-body");
      const maxScroll = Math.max(0, body.scrollHeight - body.clientHeight);
      const nextTop = Math.min(pending.top, maxScroll);
      body.scrollTop = nextTop;
      this.appliedScrollTop = body.scrollTop;
      const reachedOuter = pending.top <= maxScroll + 1 && Math.abs(body.scrollTop - pending.top) <= 1;
      if (reachedOuter) this._remember(this.sharedScrollPositions, this._sharedViewKey(), pending.top, 48);
      let reachedNested = true;
      for (const surface of this._nestedScrollSurfaces()) {
        const surfaceKey = this._nestedScrollKey(surface);
        if (!surfaceKey) continue;
        const target = pending.nested.get(surfaceKey) || 0;
        const surfaceMax = Math.max(0, surface.scrollHeight - surface.clientHeight);
        surface.scrollTop = Math.min(target, surfaceMax);
        this.appliedNestedScrollTops.set(surfaceKey, surface.scrollTop);
        const reached = target <= surfaceMax + 1 && Math.abs(surface.scrollTop - target) <= 1;
        if (reached) {
          this._remember(this.sharedNestedScrollPositions, this._nestedSharedKey(surfaceKey), target, 480);
        } else {
          reachedNested = false;
        }
      }
      if ((reachedOuter && reachedNested) || finalAttempt) {
        this._cancelScrollRestore();
      } else {
        // Content-visibility commonly publishes the real scrollHeight on the
        // frame after mount. Follow frames while layout is unstable rather
        // than waiting for the coarse transition fallback timer.
        this._queueScrollRestoreFrame();
      }
    }

    _cancelScrollRestore() {
      if (this.scrollRestoreFrame) cancelAnimationFrame(this.scrollRestoreFrame);
      this.scrollRestoreFrame = 0;
      for (const timer of this.scrollRestoreTimers) window.clearTimeout(timer);
      this.scrollRestoreTimers = [];
      this.pendingScrollRestore = null;
    }

    openLoading(metadata) {
      const body = $("inspector-body");
      this._saveScroll();
      this._cancelScrollRestore();
      this.metadata = metadata;
      this.record = null;
      this.appliedScrollTop = null;
      this.appliedNestedScrollTops = new Map();
      this._open();
      this._renderHeader();
      $("inspector-tabs").replaceChildren();
      $("inspector-filter-wrap").hidden = true;
      body.onclick = null;
      body.onkeydown = null;
      body.innerHTML = `<div class="loading-state">${escapeHtml(this.t("loading"))}</div>`;
      body.scrollTop = 0;
      $("copy-active-view").disabled = true;
      $("inspector-options-trigger").disabled = true;
    }

    show(metadata, record) {
      if (this.metadata && this.record) this._saveScroll();
      this.metadata = metadata;
      this.record = record;
      this.appliedScrollTop = null;
      this.appliedNestedScrollTops = new Map();
      this._open();
      this._renderHeader();
      $("copy-active-view").disabled = false;
      $("inspector-options-trigger").disabled = false;
      const tabs = this.availableTabs();
      if (!tabs.some((tab) => tab.id === this.activeTab)) this.activeTab = "conversation";
      this.renderTabs(tabs);
      this.renderBody(true);
    }

    showError() {
      this._cancelScrollRestore();
      this._open();
      const body = $("inspector-body");
      body.onclick = null;
      body.onkeydown = null;
      body.innerHTML = `<div class="loading-state">${escapeHtml(this.t("loadFailed"))}</div>`;
    }

    _open() {
      const workspace = $("workspace");
      const wasClosed = workspace.dataset.inspector !== "open";
      if (wasClosed) workspace.style.transition = "none";
      workspace.dataset.inspector = "open";
      $("inspector").inert = false;
      $("inspector").setAttribute("aria-hidden", "false");
      if (wasClosed) {
        void workspace.offsetWidth;
        requestAnimationFrame(() => workspace.style.removeProperty("transition"));
      }
      if (this.onOpen) this.onOpen();
    }

    reopen() {
      if (!this.metadata) return;
      this._open();
      this._restoreScroll();
    }

    close() {
      this._saveScroll();
      this._cancelScrollRestore();
      this.optionsMenu.close();
      const inspector = $("inspector");
      const focusWasInside = inspector.contains(document.activeElement);
      $("workspace").dataset.inspector = "closed";
      if (this.onClose) this.onClose();
      if (focusWasInside) {
        focusSafely($("reopen-inspector")) ||
          focusSafely(document.querySelector(".trajectory-row[aria-current='true']")) ||
          focusSafely($("trajectory-list"));
      }
      inspector.inert = true;
      inspector.setAttribute("aria-hidden", "true");
    }

    _renderHeader() {
      const meta = this.metadata || {};
      const callNumber = Number(meta._session_call_index || (meta.record_index ?? 0) + 1);
      $("inspector-kicker").textContent = "";
      $("inspector-title").textContent = `Request #${callNumber}`;
      const facts = [meta.model || this.t("unknown"), formatClock(meta.timestamp), formatDuration(meta.duration_ms)];
      const failed = Number(meta.status || 0) >= 400 || meta.error_message;
      if (failed && Number(meta.status || 0) >= 400) facts.push(`HTTP ${meta.status}`);
      else if (failed) facts.push(this.t("streamError"));
      $("inspector-facts").textContent = facts.filter(Boolean).join(" · ");
    }

    availableTabs() {
      const body = this.record?.request?.body || {};
      const tabs = [{ id: "conversation", label: this.t("conversation") }];
      if (systemText(body)) tabs.push({ id: "system", label: this.t("system") });
      if (requestTools(body).length) tabs.push({ id: "tools", label: this.t("tools") });
      tabs.push(
        { id: "request", label: this.t("request") },
        { id: "response", label: this.t("response") },
        { id: "raw", label: this.t("raw") },
      );
      return tabs;
    }

    renderTabs(tabs = this.availableTabs()) {
      const host = $("inspector-tabs");
      const fragment = document.createDocumentFragment();
      tabs.forEach((tab, index) => {
        const button = document.createElement("button");
        const shortLabel = ["request", "response", "raw"].includes(tab.id)
          ? this.t(`${tab.id}Short`)
          : tab.label;
        button.type = "button";
        button.className = `inspector-tab${tab.id === this.activeTab ? " active" : ""}`;
        button.innerHTML = `<span class="tab-label-long">${escapeHtml(tab.label)}</span><span class="tab-label-short">${escapeHtml(
          shortLabel,
        )}</span>`;
        button.id = `inspector-tab-${tab.id}`;
        button.dataset.tab = tab.id;
        button.setAttribute("role", "tab");
        button.setAttribute("aria-label", tab.label);
        button.setAttribute("aria-controls", "inspector-body");
        button.setAttribute("aria-selected", String(tab.id === this.activeTab));
        button.tabIndex = tab.id === this.activeTab ? 0 : -1;
        button.addEventListener("click", () => this.selectTab(tab.id));
        button.addEventListener("keydown", (event) => {
          if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
          event.preventDefault();
          const nextIndex =
            event.key === "Home"
              ? 0
              : event.key === "End"
                ? tabs.length - 1
                : (index + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length;
          const next = tabs[nextIndex];
          this.selectTab(next.id);
          requestAnimationFrame(() => host.querySelector(`[data-tab="${next.id}"]`)?.focus());
        });
        fragment.appendChild(button);
      });
      host.replaceChildren(fragment);
      $("inspector-body").setAttribute("aria-labelledby", `inspector-tab-${this.activeTab}`);
      requestAnimationFrame(() => host.querySelector(".active")?.scrollIntoView({ block: "nearest", inline: "nearest" }));
    }

    selectTab(tabId) {
      if (!this.record || tabId === this.activeTab) return;
      this._saveScroll();
      this.activeTab = tabId;
      this.renderTabs();
      this.renderBody(true);
    }

    renderBody(restoreScroll = true) {
      if (!this.record) return;
      const filterable = ["tools", "request", "response", "headers", "raw"].includes(this.activeTab);
      $("inspector-filter-wrap").hidden = !filterable;
      const mountedToolCount = requestTools(this.record.request?.body || {}).length;
      $("inspector-filter").placeholder =
        this.activeTab === "tools"
          ? this.t("searchToolsCount").replace("{count}", formatNumber(mountedToolCount))
          : this.t("searchJson");
      $("inspector-filter").value = this.filters.get(this._viewKey()) || "";
      const host = $("inspector-body");
      host.__claudeTapJsonTree?.destroy();
      for (const tree of this.inlineJsonInstances) tree.destroy();
      this.inlineJsonInstances = [];
      host.dataset.view = this.activeTab;
      host.onclick = null;
      host.onkeydown = null;
      this.pendingJsonTrees = [];
      this.inlineJsonSerial = 0;
      if (this.activeTab === "conversation") this.renderConversation(host);
      else if (this.activeTab === "system") this.renderSystem(host);
      else if (this.activeTab === "tools") this.renderTools(host);
      else if (this.activeTab === "request") this.renderJson(host, this.record.request?.body ?? {});
      else if (this.activeTab === "response") this.renderJson(host, this.record.response || {});
      else if (this.activeTab === "headers") {
        this.renderJson(host, { request: this.record.request?.headers || {}, response: this.record.response?.headers || {} });
      } else this.renderJson(host, this.record);
      this._mountInlineJsonTrees();
      hydrateIcons(host);
      if (restoreScroll) this._restoreScroll();
    }

    _textContentHtml(text, type = "text") {
      if (text === "" || text === null || text === undefined) return "";
      const value = String(text);
      const long = isLongMessageContent(value);
      const normalizedType = String(type || "text");
      const thinking = ["thinking", "reasoning"].includes(normalizedType);
      return `<section class="message-block message-text-block${thinking ? " message-thinking-block" : ""}${long ? " long-content" : ""}" data-content-type="${escapeHtml(normalizedType)}">
        ${!["text", "input_text", "output_text"].includes(normalizedType) ? `<span class="message-block-label">${escapeHtml(normalizedType)}</span>` : ""}
        <pre class="message-content">${escapeHtml(value)}</pre>
      </section>`;
    }

    _toolPayload(value, fallback = "") {
      if (value === undefined || value === null || value === "") {
        return { text: fallback, format: "TEXT" };
      }
      if (typeof value === "object") return { text: safeJson(value, 0), format: "JSON" };
      const text = String(value);
      const trimmed = text.trim();
      let format = "TEXT";
      if (trimmed && ["{", "["].includes(trimmed[0])) {
        try {
          JSON.parse(trimmed);
          format = "JSON";
        } catch (_) {
          // Keep malformed or partially streamed arguments byte-for-byte as text.
        }
      }
      return { text, format };
    }

    _formatBadge(format) {
      if (format !== "JSON") return "";
      return `<span class="tool-format-badge">JSON</span>`;
    }

    _unknownBlockHtml(block, label = "object") {
      const payload = this._toolPayload(block, this.t("noData"));
      const long = isLongMessageContent(payload.text);
      return `<section class="message-block message-record-block${long ? " long-content" : ""}" data-content-type="${escapeHtml(label)}">
        <div class="message-record-heading"><span class="message-block-label">${escapeHtml(label)}</span>${this._formatBadge(payload.format)}</div>
        <pre class="message-record-text">${escapeHtml(payload.text)}</pre>
      </section>`;
    }

    _inlineJsonHtml(value, slotKey, options = {}) {
      const id = `inline-json-${++this.inlineJsonSerial}`;
      this.pendingJsonTrees.push({ id, value, slotKey, wrapLines: options.wrapLines });
      return `<div class="inline-json-tree" id="${id}"></div>`;
    }

    _mountInlineJsonTrees() {
      for (const item of this.pendingJsonTrees) {
        const host = document.getElementById(item.id);
        if (!host) continue;
        const stateKey = `${this._viewKey()}:inline:${item.slotKey}`;
        if (!this.inlineJsonStates.has(stateKey)) {
          this._remember(this.inlineJsonStates, stateKey, { expanded: new Set(), fullStrings: new Set() }, 120);
        }
        const state = this.inlineJsonStates.get(stateKey);
        const tree = new JsonTree(host, item.value, {
          expanded: state.expanded,
          fullStrings: state.fullStrings,
          wrapLines: item.wrapLines ?? this.wrapLines,
          labels: {
            copy: this.t("copyValue"),
            copyValue: this.t("copyValue"),
            copyJson: "Copy JSON",
            copyPath: "Copy property path",
            copyPrettyJson: "Copy pretty JSON",
            copyCompactJson: "Copy compact JSON",
            copied: this.t("copied"),
            copyFailed: this.t("copyFailed"),
            expand: this.t("expandNode"),
            collapse: this.t("collapseNode"),
          },
        });
        this.inlineJsonInstances.push(tree);
      }
      this.pendingJsonTrees = [];
    }

    _toolIdentityHtml(id) {
      if (!id) return "";
      return `<code class="tool-call-id" title="${escapeHtml(String(id))}">${escapeHtml(String(id))}</code>`;
    }

    _toolCallHtml(call, toolNames = null) {
      if (call?.id && call?.name && toolNames) toolNames.set(String(call.id), String(call.name));
      // The surrounding wire object is useful for protocol detection and call
      // pairing, but it is not the tool payload a developer wants to inspect.
      // Render only the normalized input/arguments/action value.
      const value = parseMaybeJson(call?.value);
      const payload = this._toolPayload(value, this.t("noData"));
      const identity = call?.id ? String(call.id) : "";
      const long = isLongMessageContent(payload.text);
      const body = isJsonContainer(value)
        ? this._inlineJsonHtml(
            value,
            `message:call:${identity || call.name || "tool"}:${this.inlineJsonSerial}`,
            { wrapLines: true },
          )
        : `<pre class="message-tool-text">${escapeHtml(payload.text)}</pre>`;
      return `<section class="message-block message-tool-block tool-call${long ? " long-content" : ""}" data-tool-phase="call"${
        identity ? ` data-tool-call-id="${escapeHtml(identity)}" data-call-id="${escapeHtml(identity)}"` : ""
      }>
        <div class="message-tool-summary"${identity ? ` data-call-id="${escapeHtml(identity)}"` : ""}>
          <span class="tool-glyph">${toolIcon(13)}</span>
          <span class="tool-call-name">${escapeHtml(call.name || "tool")}</span>
          <span class="tool-phase">CALL</span>
          <span class="message-tool-meta">${this._toolIdentityHtml(identity)}${this._formatBadge(payload.format)}</span>
        </div>
        <div class="message-tool-data">${body}</div>
      </section>`;
    }

    _toolResultHtml(result, toolNames = null) {
      // As with calls, keep protocol metadata in data attributes and expose
      // only the normalized content/output/response value in the card body.
      const value = parseMaybeJson(result?.value);
      const output = value ?? this.t("noData");
      const resolvedName = result?.name || (result?.id && toolNames?.get(String(result.id))) || "";
      const label = resolvedName || this.t("toolResult");
      const payload = this._toolPayload(value, String(output));
      const identity = result?.id ? String(result.id) : "";
      const long = isLongMessageContent(payload.text);
      const body = isJsonContainer(value)
        ? this._inlineJsonHtml(
            value,
            `message:result:${identity || result?.name || "tool"}:${this.inlineJsonSerial}`,
            { wrapLines: true },
          )
        : `<pre class="message-tool-text">${escapeHtml(payload.text)}</pre>`;
      return `<section class="message-block message-tool-block tool-result${result?.isError ? " error" : ""}${long ? " long-content" : ""}" data-tool-phase="result" data-result-status="${
        result?.isError ? "error" : "ok"
      }"${identity ? ` data-tool-call-id="${escapeHtml(identity)}" data-call-id="${escapeHtml(identity)}"` : ""}>
        <div class="message-tool-summary"${identity ? ` data-call-id="${escapeHtml(identity)}"` : ""}>
          <span class="tool-glyph">${toolIcon(13)}</span>
          <span class="tool-call-name">${escapeHtml(label)}</span>
          <span class="tool-phase">RESULT</span>
          <span class="message-tool-meta">${this._toolIdentityHtml(identity)}${
            result?.isError ? '<span class="tool-result-status">ERROR</span>' : ""
          }${this._formatBadge(payload.format)}</span>
        </div>
        <div class="message-tool-data">${body}</div>
      </section>`;
    }

    _contentBlockHtml(block, role, toolNames) {
      if (block === null || block === undefined || typeof block !== "object") {
        return this._textContentHtml(contentText(block));
      }
      const type = String(
        block.type ||
          (block.thought === true
            ? "thinking"
            : block.text !== undefined
              ? "text"
              : block.inlineData !== undefined
                ? "inline_data"
                : block.fileData !== undefined
                  ? "file_data"
                  : "object"),
      );
      if (["text", "input_text", "output_text"].includes(type)) {
        return this._textContentHtml(block.text ?? block.output_text ?? "", type);
      }
      if (["thinking", "reasoning"].includes(type)) {
        const text = contentText(block);
        if (text) return this._textContentHtml(text, type);
        const hasVisibleFields = Object.entries(block).some(
          ([key, value]) => !["type", "thinking", "signature"].includes(key) && value !== "" && value != null,
        );
        return hasVisibleFields ? this._unknownBlockHtml(block, type) : "";
      }
      const result = toolResultFromBlock(block, role);
      if (result) return this._toolResultHtml(result, toolNames);
      const call = toolCallFromBlock(block);
      if (call) return this._toolCallHtml(call, toolNames);
      if (block.parts !== undefined) {
        const parts = Array.isArray(block.parts) ? block.parts : [block.parts];
        return parts.map((part) => this._contentBlockHtml(part, role, toolNames)).join("");
      }
      return this._unknownBlockHtml(block, type);
    }

    _requestBlockType(block, role, forcedType = "") {
      const generic = new Set(["", "message", "content", "function"]);
      const rawType = String(forcedType || (block && typeof block === "object" ? block.type || "" : ""));
      if (block && typeof block === "object") {
        if (toolResultFromBlock(block, role)) return generic.has(rawType) ? "tool_result" : rawType;
        if (toolCallFromBlock(block)) {
          if (!generic.has(rawType)) return rawType;
          if (block.functionCall) return "function_call";
          return block.function ? "tool_call" : "tool_use";
        }
        if (block.thought === true) return "thinking";
        if (block.text !== undefined) return rawType || "text";
        if (block.output_text !== undefined) return rawType || "output_text";
        if (block.inlineData !== undefined) return rawType || "inline_data";
        if (block.fileData !== undefined) return rawType || "file_data";
      }
      return generic.has(rawType) ? "text" : rawType || "object";
    }

    _requestItemBlocks(item, toolNames) {
      const raw = item.raw || {};
      const blocks = [];
      const push = (value, path, forcedType = "") => {
        const html = this._contentBlockHtml(value, item.role, toolNames);
        if (!html) return;
        const result = value && typeof value === "object" ? toolResultFromBlock(value, item.role) : null;
        const call = value && typeof value === "object" ? toolCallFromBlock(value) : null;
        blocks.push({
          html,
          path,
          type: this._requestBlockType(value, item.role, forcedType),
          id: result?.id || call?.id || "",
          toolPhase: result ? "result" : call ? "call" : "",
        });
      };

      if (item.fallback) {
        const fallbackValue = item.fallbackValue !== undefined ? item.fallbackValue : raw;
        return [
          {
            html: this._unknownBlockHtml(fallbackValue, item.type),
            path: "body",
            type: item.type,
            id: "",
            toolPhase: "",
          },
        ];
      }

      const standaloneResult = toolResultFromBlock(raw, item.role);
      if (standaloneResult) {
        const html = this._toolResultHtml(standaloneResult, toolNames);
        return [
          {
            html,
            path: "item",
            type: this._requestBlockType(raw, item.role, item.type),
            id: standaloneResult.id,
            toolPhase: "result",
          },
        ];
      }
      const standaloneCall = toolCallFromBlock(raw);
      if (standaloneCall && item.type !== "message" && item.type !== "content") {
        const html = this._toolCallHtml(standaloneCall, toolNames);
        return [
          {
            html,
            path: "item",
            type: this._requestBlockType(raw, item.role, item.type),
            id: standaloneCall.id,
            toolPhase: "call",
          },
        ];
      }

      let sawContent = false;
      for (const key of Object.keys(raw)) {
        if (key === "content" || key === "parts") {
          sawContent = true;
          const values = Array.isArray(raw[key]) ? raw[key] : [raw[key]];
          values.forEach((value, index) => push(value, `${key}.${index}`, item.type === "prompt" ? "prompt" : ""));
        } else if (key === "tool_calls" && Array.isArray(raw.tool_calls)) {
          raw.tool_calls.forEach((value, index) => {
            const call = toolCallFromBlock(value);
            const html = call ? this._toolCallHtml(call, toolNames) : this._unknownBlockHtml(value, "tool_call");
            blocks.push({
              html,
              path: `tool_calls.${index}`,
              type: "tool_call",
              id: call?.id || "",
              toolPhase: call ? "call" : "",
            });
          });
        } else if (key === "function_call" && raw.function_call) {
          const value = { type: "function_call", ...raw.function_call };
          const call = toolCallFromBlock(value, raw.function_call);
          const html = call
            ? this._toolCallHtml(call, toolNames)
            : this._unknownBlockHtml(raw.function_call, "function_call");
          blocks.push({
            html,
            path: "function_call",
            type: "function_call",
            id: call?.id || "",
            toolPhase: call ? "call" : "",
          });
        }
      }
      if (!blocks.length && sawContent) {
        blocks.push({
          html: `<div class="message-empty">${escapeHtml(this.t("noData"))}</div>`,
          path: "content.0",
          type: "text",
          id: "",
          toolPhase: "",
        });
      } else if (!blocks.length) {
        blocks.push({
          html: this._unknownBlockHtml(raw, item.type),
          path: "item",
          type: item.type,
          id: "",
          toolPhase: "",
        });
      }
      return blocks;
    }

    _requestBlockHtml(item, block, blockIndex) {
      const role = item.role || "context";
      const displayRole = item.rawRole || role;
      const origin = item.origin === "output" ? "output" : "request";
      const identity = block.id ? String(block.id) : "";
      const toolClass = block.toolPhase ? " request-message-tool" : "";
      return `<article class="request-message${toolClass}" data-origin="${origin}" data-role="${escapeHtml(role)}" data-protocol="${escapeHtml(
        item.protocol || "unknown",
      )}" data-raw-role="${escapeHtml(displayRole)}" data-message-index="${item.index}" data-block-index="${blockIndex}" data-block-path="${escapeHtml(
        block.path,
      )}" data-block-type="${escapeHtml(block.type)}"${block.toolPhase ? ` data-tool-phase="${block.toolPhase}"` : ""}${identity ? ` data-call-id="${escapeHtml(identity)}"` : ""} aria-label="${escapeHtml(
        `${displayRole} ${block.type}${identity ? ` ${identity}` : ""}`,
      )}">
        <div class="request-message-body">${block.html}</div>
      </article>`;
    }

    _messageTypeSummary(blocks) {
      const counts = new Map();
      for (const block of blocks) counts.set(block.type, (counts.get(block.type) || 0) + 1);
      return [...counts.entries()]
        .map(([type, count]) => `${type}${count > 1 ? ` ×${count}` : ""}`)
        .join(" · ");
    }

    _requestMessageGroupHtml(item, blocks) {
      const role = item.role || "context";
      const displayRole = item.rawRole || role;
      const origin = item.origin === "output" ? "output" : "request";
      const summary = this._messageTypeSummary(blocks);
      const toolCount = blocks.filter((block) => block.toolPhase).length;
      return `<section class="request-message-group" data-origin="${origin}" data-role="${escapeHtml(role)}" data-raw-role="${escapeHtml(displayRole)}" data-protocol="${escapeHtml(
        item.protocol || "unknown",
      )}" data-message-index="${item.index}"${toolCount ? ` data-tool-count="${toolCount}"` : ""} aria-label="${escapeHtml(`${origin === "output" ? "output " : ""}${displayRole} message`)}">
        <header class="request-message-header">
          <span class="message-role">${escapeHtml(roleLabel(displayRole))}</span>
          <span class="message-type">${escapeHtml(summary)}</span>
        </header>
        <div class="request-message-blocks">
          ${blocks.map((block, blockIndex) => this._requestBlockHtml(item, block, blockIndex)).join("")}
        </div>
      </section>`;
    }

    _indexToolNames(sequence) {
      const names = new Map();
      const visit = (block) => {
        if (!block || typeof block !== "object") return;
        const call = toolCallFromBlock(block);
        if (call?.id && call?.name) names.set(String(call.id), String(call.name));
        const nested = [];
        if (Array.isArray(block.content)) nested.push(...block.content);
        if (Array.isArray(block.parts)) nested.push(...block.parts);
        if (Array.isArray(block.tool_calls)) nested.push(...block.tool_calls);
        if (block.function_call && typeof block.function_call === "object") {
          nested.push({ type: "function_call", ...block.function_call });
        }
        for (const child of nested) visit(child);
      };
      for (const item of sequence) visit(item.raw);
      return names;
    }

    _historyHtml() {
      const body = this.record.request?.body || {};
      const requestItems = requestSequence(body).filter((item) => item.type !== "additional_tools");
      const failed = Number(this.metadata?.status || 0) >= 400 || Boolean(this.metadata?.error_message);
      const outputItems = responseSequence(this.record.response || {}, failed);
      const sequence = [...requestItems, ...outputItems];
      const toolNames = this._indexToolNames(sequence);
      let outputStarted = false;
      return sequence
        .map((item) => {
          const isOutput = item.origin === "output";
          const boundary =
            isOutput && !outputStarted
              ? `<div class="response-boundary" role="separator" aria-label="${escapeHtml(this.t("responseKind"))}"><span>${escapeHtml(
                  this.t("responseKind"),
                )}</span></div>`
              : "";
          if (isOutput) outputStarted = true;
          return `${boundary}${this._requestMessageGroupHtml(item, this._requestItemBlocks(item, toolNames))}`;
        })
        .join("");
    }

    renderConversation(host) {
      const history = this._historyHtml();
      const failed = Number(this.metadata?.status || 0) >= 400 || this.metadata?.error_message;
      const failureNotice = failed
        ? `<div class="request-error-notice" role="status"><span class="response-note-label">${escapeHtml(
            this.t("responseKind"),
          )}</span><span class="response-note-status">${escapeHtml(
            Number(this.metadata?.status || 0) >= 400 ? `HTTP ${this.metadata.status}` : this.t("streamError"),
          )}</span><span class="response-note-message">${escapeHtml(
            this.metadata?.error_message || this.t("requestFailed"),
          )}</span></div>`
        : "";
      host.innerHTML = `<div class="conversation-flow message-list">
        ${failureNotice}
        ${history || `<div class="inspector-empty">${escapeHtml(this.t("noData"))}</div>`}
      </div>`;
    }

    renderSystem(host) {
      const body = this.record.request?.body || {};
      const text = systemText(body);
      if (!text) {
        host.innerHTML = `<div class="inspector-empty">${escapeHtml(this.t("noData"))}</div>`;
        return;
      }
      host.innerHTML = `<div class="system-prompt"><pre>${escapeHtml(text)}</pre></div>`;
    }

    renderTools(host) {
      const query = (this.filters.get(this._viewKey()) || "").trim().toLowerCase();
      const definitions = requestTools(this.record.request?.body || {}).filter(
        (tool) => !query || `${tool.name}\n${tool.description}\n${safeJson(tool.schema, 0)}`.toLowerCase().includes(query),
      );
      const definitionHtml = definitions
        .map(
          (tool) => `<details class="tool-definition">
            <summary>
              <span class="tool-chevron" data-icon="chevron" data-icon-size="14"></span>
              <span class="tool-glyph">${toolIcon(13)}</span>
              <span class="tool-call-name">${escapeHtml(tool.name)}</span>
              ${tool.kind && !["tool", "function"].includes(tool.kind) ? `<span class="tool-kind">${escapeHtml(tool.kind)}</span>` : ""}
              ${tool.strict ? '<span class="tool-kind">strict</span>' : ""}
              ${tool.description ? '<span class="tool-separator" aria-hidden="true"></span>' : ""}
              <span class="tool-call-summary">${escapeHtml(tool.description)}</span>
            </summary>
            <div class="tool-definition-body">
              ${tool.description ? `<p class="tool-definition-description">${escapeHtml(tool.description)}</p>` : ""}
              ${this._schemaHtml(tool.schema, `schema:${tool.name}`)}
            </div>
          </details>`,
        )
        .join("");
      host.innerHTML = definitionHtml
        ? `<div class="tool-catalog" data-tool-count="${definitions.length}">${definitionHtml}</div>`
        : `<div class="inspector-empty">${escapeHtml(this.t("noData"))}</div>`;
    }

    _schemaParameterHtml(name, schema, required = false, depth = 0) {
      const value = schema && typeof schema === "object" ? schema : {};
      const rawType = Array.isArray(value.type) ? value.type.join(" | ") : value.type;
      const type = rawType || (value.properties ? "object" : value.items ? "array" : value.$ref || "any");
      const tags = [type ? `<span class="schema-type">${escapeHtml(type)}</span>` : ""];
      if (required) tags.push(`<span class="schema-required">${escapeHtml(this.t("required"))}</span>`);
      if (value.format) tags.push(`<span class="schema-format">${escapeHtml(value.format)}</span>`);
      if (Array.isArray(value.enum)) {
        tags.push(`<span class="schema-enum">${escapeHtml(value.enum.map((item) => String(item)).join(" | "))}</span>`);
      }
      if (value.default !== undefined) {
        tags.push(`<span class="schema-default">default ${escapeHtml(safeJson(value.default, 0))}</span>`);
      }
      let children = "";
      if (depth < 7 && value.properties && typeof value.properties === "object") {
        const requiredChildren = new Set(Array.isArray(value.required) ? value.required : []);
        children = Object.entries(value.properties)
          .map(([childName, child]) => this._schemaParameterHtml(childName, child, requiredChildren.has(childName), depth + 1))
          .join("");
      } else if (depth < 7 && value.items?.properties && typeof value.items.properties === "object") {
        const requiredChildren = new Set(Array.isArray(value.items.required) ? value.items.required : []);
        children = Object.entries(value.items.properties)
          .map(([childName, child]) => this._schemaParameterHtml(childName, child, requiredChildren.has(childName), depth + 1))
          .join("");
      }
      return `<div class="schema-parameter">
        <div class="schema-parameter-line"><span class="schema-name" title="${escapeHtml(name)}">${escapeHtml(name)}</span>${tags.join("")}</div>
        ${value.description ? `<div class="schema-description">${escapeHtml(value.description)}</div>` : ""}
        ${children ? `<div class="schema-children">${children}</div>` : ""}
      </div>`;
    }

    _schemaHtml(schema, slotKey) {
      if (!schema || typeof schema !== "object" || !Object.keys(schema).length) {
        return `<div class="schema-empty">${escapeHtml(this.t("noParameters"))}</div>`;
      }
      if (schema.properties && typeof schema.properties === "object") {
        const required = new Set(Array.isArray(schema.required) ? schema.required : []);
        const rows = Object.entries(schema.properties)
          .map(([name, value]) => this._schemaParameterHtml(name, value, required.has(name)))
          .join("");
        return `<section class="schema-view"><div class="schema-heading">${escapeHtml(
          this.t("parameters"),
        )}<span>${Object.keys(schema.properties).length}</span></div><div class="schema-parameters">${rows}</div></section>`;
      }
      return `<section class="schema-view"><div class="schema-heading">${escapeHtml(
        this.t("configuration"),
      )}</div>${this._inlineJsonHtml(schema, slotKey)}</section>`;
    }

    _rawForTab() {
      if (this.activeTab === "request") return this.record?.request?.body ?? {};
      if (this.activeTab === "response") return this.record?.response || {};
      if (this.activeTab === "headers") {
        return { request: this.record?.request?.headers || {}, response: this.record?.response?.headers || {} };
      }
      if (this.activeTab === "raw") return this.record || {};
      return null;
    }

    currentViewText() {
      const raw = this._rawForTab();
      if (raw !== null) return safeJson(raw);
      return $("inspector-body").innerText;
    }

    renderJson(host, value) {
      const parsed = parseMaybeJson(value);
      if (!isJsonContainer(parsed)) {
        this.renderCode(host, parsed);
        return;
      }
      const stateKey = this._viewKey();
      if (!this.jsonStates.has(stateKey)) {
        this._remember(this.jsonStates, stateKey, { expanded: new Set(), fullStrings: new Set() }, 80);
      }
      const state = this.jsonStates.get(stateKey);
      host.replaceChildren();
      new JsonTree(host, parsed, {
        query: this.filters.get(stateKey) || "",
        expanded: state.expanded,
        fullStrings: state.fullStrings,
        wrapLines: this.wrapLines,
        labels: {
          copy: this.t("copyValue"),
          copyValue: this.t("copyValue"),
          copyJson: "Copy JSON",
          copyPath: "Copy property path",
          copyPrettyJson: "Copy pretty JSON",
          copyCompactJson: "Copy compact JSON",
          copied: this.t("copied"),
          copyFailed: this.t("copyFailed"),
          expand: this.t("expandNode"),
          collapse: this.t("collapseNode"),
          noMatches: this.t("noJsonMatches"),
        },
      });
    }

    renderCode(host, value) {
      const cacheKey = this._viewKey();
      let raw = this.codeCache.get(cacheKey);
      if (raw === undefined) {
        raw = typeof value === "string" ? value : safeJson(value);
        this.codeCache.set(cacheKey, raw);
      }
      const query = (this.filters.get(this._viewKey()) || "").trim().toLowerCase();
      let display = query
        ? raw
            .split("\n")
            .filter((line) => line.toLowerCase().includes(query))
            .join("\n")
        : raw;
      const fullKey = this._viewKey();
      const limit = 300_000;
      const truncated = display.length > limit && !this.fullTabs.has(fullKey);
      if (truncated) display = display.slice(0, limit);
      host.innerHTML = `${
        truncated
          ? `<div class="truncation-note"><span>${escapeHtml(this.t("truncated"))}</span><button class="text-action" id="show-full-value" type="button">${escapeHtml(
              this.t("showFull"),
            )}</button></div>`
          : ""
      }<div class="code-view${this.wrapLines ? " wrap" : ""}"><pre>${escapeHtml(display || this.t("noData"))}</pre></div>`;
      host.querySelector("#show-full-value")?.addEventListener("click", () => {
        this.fullTabs.add(fullKey);
        this._rerenderPreservingScroll();
      });
    }

    refreshLanguage() {
      if (!this.metadata) return;
      if (this.record) {
        this._cancelScrollRestore();
        this._saveScroll();
      }
      this._renderHeader();
      if (this.record) {
        this.renderTabs();
        this.renderBody(true);
      }
    }
  }

  window.ClaudeTapComponents = {
    FixedVirtualList,
    FixedTooltip,
    DropdownMenu,
    Inspector,
    JsonTree,
    requestSequence,
    requestTools,
    contentText,
    systemEntries,
    systemText,
  };
})();
