(function () {
  "use strict";

  const {
    EVENTS,
    EventHub,
    Translator,
    $,
    escapeHtml,
    debounce,
    formatNumber,
    formatDuration,
    formatClock,
    copyText,
    focusSafely,
    moveFocusBeforeInert,
  } = window.ClaudeTapCore;
  const { icon, hydrateIcons, toolIcon } = window.ClaudeTapIcons;
  const { createSource, TraceStore } = window.ClaudeTapData;
  const { FixedVirtualList, FixedTooltip, DropdownMenu, Inspector } = window.ClaudeTapComponents;
  const SIDEBAR_MOTION_MS = 300;
  const SIDEBAR_SETTLE_GRACE_MS = 40;

  function readableAssistantHint(value) {
    const text = String(value || "").trim();
    if (!text || !["{", "[", '"'].includes(text[0])) return text;
    try {
      const parsed = JSON.parse(text);
      if (typeof parsed === "string") return parsed;
      if (parsed && !Array.isArray(parsed) && typeof parsed.title === "string") return parsed.title.trim();
    } catch (_) {
      // The preview is allowed to be arbitrary model text, not necessarily JSON.
    }
    return text;
  }

  function compactLedgerText(value, limit = 112) {
    const text = String(value || "").replace(/\s+/g, " ").trim();
    return text.length > limit ? `${text.slice(0, limit - 1).trimEnd()}…` : text;
  }

  function compactToolHint(value) {
    let parsed = value;
    if (typeof value === "string") {
      try {
        parsed = JSON.parse(value);
      } catch (_) {
        return compactLedgerText(value, 72);
      }
    }
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return compactLedgerText(parsed, 72);
    if (typeof parsed.description === "string" && parsed.description.trim()) {
      return compactLedgerText(parsed.description, 72);
    }
    for (const key of ["file_path", "path"]) {
      if (typeof parsed[key] !== "string" || !parsed[key].trim()) continue;
      const parts = parsed[key].trim().split(/[\\/]/).filter(Boolean);
      const tail = parts.length > 2 ? `…/${parts.slice(-2).join("/")}` : parsed[key].trim();
      return `${key}: ${compactLedgerText(tail, 72)}`;
    }
    for (const key of ["query", "pattern", "url", "prompt"]) {
      if (parsed[key] === undefined) continue;
      return `${key}: ${compactLedgerText(parsed[key], 72)}`;
    }
    for (const key of ["command", "cmd"]) {
      if (parsed[key] === undefined) continue;
      const command = String(parsed[key] || "").replace(/^cd\s+\S+\s+&&\s*/, "");
      return `command: ${compactLedgerText(command, 72)}`;
    }
    const [first] = Object.entries(parsed);
    if (!first) return "";
    return `${first[0]}: ${compactLedgerText(typeof first[1] === "string" ? first[1] : JSON.stringify(first[1]), 72)}`;
  }

  function latestRequestHint(meta) {
    const results = Array.isArray(meta?.tool_results) ? meta.tool_results : [];
    const latest = [...results].reverse().find((item) => item?.name || item?.output_preview);
    if (latest) {
      const name = String(latest.name || "Tool result").trim();
      const preview = String(latest.output_preview || "").replace(/\s+/g, " ").trim();
      return [name, preview].filter(Boolean).join(" · ").slice(0, 420);
    }
    return String(meta?.user_hint || "").trim();
  }

  function requestStatus(meta) {
    const status = Number(meta?.status || 0);
    const message = String(meta?.error_message || "").trim();
    const normalized = message.toLowerCase();
    if (status === 529 || normalized.includes("overload")) return { kind: "warning", label: "OVERLOADED" };
    if (status === 429 || normalized.includes("rate limit")) return { kind: "warning", label: "RATE LIMITED" };
    if (normalized.includes("timeout") || normalized.includes("timed out")) return { kind: "warning", label: "TIMEOUT" };
    if (status >= 400) return { kind: "danger", label: `HTTP ${status}` };
    if (message) return { kind: "danger", label: "ERROR" };
    return null;
  }

  class TraceApp {
    constructor() {
      this.hub = new EventHub();
      this.translator = new Translator();
      this.source = createSource(this.hub);
      this.store = new TraceStore(this.source, this.hub);
      this.sessionQuery = "";
      this.streamState = typeof LIVE_MODE !== "undefined" && LIVE_MODE ? "reconnecting" : "offline";
      this.renderScheduled = false;
      this.restoringRoute = true;
      this.initialRoute = this.readHash();
      this.viewportMode = "";
      this.sidebarTimers = new Set();
      this.sidebarFrames = new Set();
      this.sidebarTransitionSequence = 0;
      this.sidebarSearchFrame = 0;
      this.sidebarSearchSequence = 0;
      this.sidebarScrollbarTimer = 0;
      this.inspector = new Inspector(this.translator, (message, isError) => this.toast(message, isError));
      this.sessionList = new FixedVirtualList(
        $("session-list"),
        $("session-canvas"),
        34,
        (item) => this.renderSessionRow(item),
        5,
      );
      this.trajectoryList = new FixedVirtualList(
        $("trajectory-list"),
        $("trajectory-canvas"),
        72,
        (item) => this.renderTrajectoryRow(item),
        8,
      );
      this.trajectoryResizeObserver = new ResizeObserver(() => this.syncTrajectoryDensity());
      this.trajectoryResizeObserver.observe($("call-panel"));
      this.captureMenu = new DropdownMenu(
        $("capture-trigger"),
        $("capture-menu"),
        () => this.captureMenuItems(),
        (id) => this.store.loadCapture(id),
      );
      this.modelMenu = new DropdownMenu(
        $("model-trigger"),
        $("model-menu"),
        () => this.modelMenuItems(),
        (id) => this.store.setFilter("model", id === "__all__" ? "" : id),
      );
      this.settingsMenu = new DropdownMenu(
        $("settings-trigger"),
        $("settings-menu"),
        () => this.settingsMenuItems(),
        (id) => this.handleSettings(id),
      );
      this.sessionOptionsMenu = new DropdownMenu(
        $("session-options-trigger"),
        $("session-options-menu"),
        () => this.sessionOptionItems(),
        (id) => this.handleSessionOption(id),
      );
      this.sidebarTooltips = {
        toggle: new FixedTooltip($("toggle-sidebar"), () =>
          this.t($("workspace").dataset.sidebar === "open" ? "closeSessions" : "openSessions"), { delayMs: 500 }),
        capture: new FixedTooltip($("rail-capture"), () => this.t("chooseCapture"), { delayMs: 500 }),
        search: new FixedTooltip($("rail-search"), () => this.t("searchSessions"), { delayMs: 500 }),
        settings: new FixedTooltip($("settings-trigger"), () => this.t("settings"), { delayMs: 500 }),
        headerCapture: new FixedTooltip($("capture-trigger"), () => this.t("chooseCapture"), {
          side: "bottom",
          delayMs: 500,
        }),
        headerSearch: new FixedTooltip($("session-search-toggle"), () => this.t("searchSessions"), {
          side: "bottom",
          delayMs: 500,
        }),
      };
    }

    get t() {
      return (key) => this.translator.t(key);
    }

    async start() {
      try {
        this.applyTheme();
        hydrateIcons();
        this.applyResponsiveLayout(true);
        this.bindEvents();
        this.bindControls();
        this.translateStatic();
        $("app-version").textContent = `v${typeof __CLAUDE_TAP_VERSION__ !== "undefined" ? __CLAUDE_TAP_VERSION__ : "0"}`;
        await this.store.initialize(this.initialRoute.capture);
        await this.restoreHash();
        this.restoringRoute = false;
        this.renderAll(false);
        this.updateHash();
        requestAnimationFrame(() =>
          requestAnimationFrame(() => {
            document.documentElement.dataset.viewerReady = "true";
          }),
        );
      } catch (error) {
        this.handleError(error);
      }
    }

    bindEvents() {
      this.hub.on(EVENTS.CATALOG_LOADED, () => this.renderCapturePicker());
      this.hub.on(EVENTS.CAPTURE_LOADING, () => {
        this.trajectoryList.disposeRendered();
        $("trajectory-canvas").innerHTML = `<div class="loading-state">${escapeHtml(this.t("loading"))}</div>`;
      });
      this.hub.on(EVENTS.CAPTURE_LOADED, () => {
        this.inspector.close();
        this.renderAll(false);
      });
      this.hub.on(EVENTS.SESSION_SELECTED, () => {
        if (this.store.state.selectedRecordIndex === null) this.inspector.close();
        this.renderActiveSession();
        this.renderSessions(true);
        this.renderTrajectory(false);
        this.updateReopenButton();
        this.updateHash();
        if (window.innerWidth < 800) this.setSidebar("collapsed", false);
      });
      this.hub.on(EVENTS.FILTER_CHANGED, () => {
        this.renderModelPicker();
        this.renderTrajectory(false);
      });
      this.hub.on(EVENTS.RECORD_SELECTED, (metadata) => {
        const cached = this.store.state.selectedRecord;
        if (cached) this.inspector.show(metadata, cached);
        else this.inspector.openLoading(metadata);
        this.trajectoryList.refresh(true);
        this.updateReopenButton();
        this.updateHash();
      });
      this.hub.on(EVENTS.RECORD_LOADED, ({ metadata, record }) => {
        const alreadyVisible =
          this.inspector.record === record && this.inspector.metadata?.record_index === metadata.record_index;
        if (!alreadyVisible) this.inspector.show(metadata, record);
        this.updateReopenButton();
      });
      this.hub.on(EVENTS.RECORD_APPENDED, () => this.scheduleLiveRender());
      this.hub.on(EVENTS.STREAM_STATUS, ({ state }) => this.setStreamStatus(state));
      this.hub.on(EVENTS.ERROR, (error) => this.handleError(error));
      this.inspector.onClose = () => {
        this.trajectoryList.refresh(true);
        this.updateReopenButton();
        this.syncSurfaceAccessibility();
        if (window.innerWidth < 900 && this.store.state.selectedRecordIndex !== null) {
          requestAnimationFrame(() => $("reopen-inspector").focus());
        }
      };
      this.inspector.onOpen = () => {
        this.updateReopenButton();
        this.syncSurfaceAccessibility();
        if (window.innerWidth < 900) requestAnimationFrame(() => $("close-inspector").focus());
      };
    }

    bindControls() {
      $("session-search").addEventListener(
        "input",
        debounce((event) => {
          this.sessionQuery = event.target.value.trim().toLowerCase();
          this.renderSessions(false);
        }, 90),
      );
      $("session-search").addEventListener("input", () => this.syncSidebarSearchControls());
      $("session-search-toggle").addEventListener("click", () => this.setSidebarSearchExpanded(true, true));
      $("sidebar-search-shell").addEventListener("click", (event) => {
        if (event.target.closest?.("#session-search-clear")) return;
        this.setSidebarSearchExpanded(true, true);
      });
      $("session-search-clear").addEventListener("click", (event) => {
        event.stopPropagation();
        $("session-search").value = "";
        this.sessionQuery = "";
        this.renderSessions(false);
        this.setSidebarSearchExpanded(false, false);
        $("session-search-toggle").focus({ preventScroll: true });
      });
      $("session-search").addEventListener("keydown", (event) => {
        if (event.key !== "Escape") return;
        event.preventDefault();
        $("session-search").value = "";
        this.sessionQuery = "";
        this.renderSessions(false);
        this.setSidebarSearchExpanded(false, false);
        $("session-search-toggle").focus({ preventScroll: true });
      });
      document.addEventListener("pointerdown", (event) => {
        if (
          $("sidebar-browser").dataset.searchExpanded === "true" &&
          !$("sidebar-search-shell").contains(event.target) &&
          !$("session-search").value
        ) {
          this.setSidebarSearchExpanded(false, false);
        }
      });
      $("session-panel").addEventListener("pointerenter", () => {
        window.clearTimeout(this.sidebarScrollbarTimer);
        this.sidebarScrollbarTimer = 0;
        $("session-panel").classList.remove("quiet-bars");
      });
      $("session-panel").addEventListener("pointerleave", () => {
        if (this.sidebarScrollbarTimer) return;
        this.sidebarScrollbarTimer = window.setTimeout(() => {
          this.sidebarScrollbarTimer = 0;
          $("session-panel").classList.add("quiet-bars");
        }, 2000);
      });
      $("trace-search").addEventListener(
        "input",
        debounce((event) => this.store.setFilter("query", event.target.value), 80),
      );
      $("toggle-sidebar").addEventListener("click", () => {
        const next = $("workspace").dataset.sidebar === "open" ? "collapsed" : "open";
        this.setSidebar(next, true, true);
      });
      $("rail-capture").addEventListener("click", () => {
        this.setSidebar("open", true);
        this.afterSidebarOpen(() => this.captureMenu.open());
      });
      $("rail-search").addEventListener("click", () => {
        this.setSidebar("open", true);
        this.afterSidebarOpen(() => this.setSidebarSearchExpanded(true, true));
      });
      $("open-sidebar").addEventListener("click", () => this.setSidebar("open", false, true));
      $("reopen-inspector").addEventListener("click", () => this.inspector.reopen());
      $("mobile-scrim").addEventListener("click", () => {
        if ($("workspace").dataset.inspector === "open") this.inspector.close();
        else this.setSidebar("collapsed", false, true);
      });
      $("trajectory-list").addEventListener("keydown", (event) => this.handleTrajectoryKey(event));
      window.addEventListener("resize", debounce(() => this.applyResponsiveLayout(false), 80));
      document.addEventListener("keydown", (event) => {
        if (event.key !== "Escape" || event.target.closest?.(".menu-popover")) return;
        if ($("workspace").dataset.inspector === "open") {
          event.preventDefault();
          this.inspector.close();
        } else if (window.innerWidth < 800 && $("workspace").dataset.sidebar === "open") {
          event.preventDefault();
          this.setSidebar("collapsed", false, true);
        }
      });
    }

    translateStatic() {
      document.documentElement.lang = this.translator.language === "zh" ? "zh-CN" : "en";
      $("capture-label").textContent = this.t("capture");
      $("session-heading").textContent = this.t("sessions");
      $("session-search").placeholder = this.t("searchSessions");
      $("trace-search").placeholder = this.t("searchTrace");
      $("empty-title").textContent = this.t("noMatches");
      $("empty-copy").textContent = this.t("clearFilter");
      const headings = $("call-list-head").children;
      $("call-head-call").textContent = this.t("call");
      $("call-head-time").textContent = this.t("time");
      headings[1].textContent = this.t("activity");
      headings[2].textContent = this.t("modelUsage");
      headings[3].textContent = this.t("timeStatus");
      $("settings-label").textContent = this.t("settings");
      $("capture-trigger").ariaLabel = this.t("chooseCapture");
      $("session-search-toggle").ariaLabel = this.t("searchSessions");
      $("session-search-clear").ariaLabel = this.t("clearSearch");
      $("rail-capture").ariaLabel = this.t("chooseCapture");
      $("rail-search").ariaLabel = this.t("searchSessions");
      $("open-sidebar").ariaLabel = this.t("openSessions");
      $("reopen-inspector").ariaLabel = this.t("openInspector");
      $("close-inspector").ariaLabel = this.t("closeInspector");
      this.syncSidebarControls();
      this.renderAll(true);
      this.inspector.refreshLanguage();
    }

    applyTheme() {
      const stored = localStorage.getItem("claude-tap-theme");
      const theme = stored || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
      document.documentElement.dataset.theme = theme;
      this.syncThemeColor(theme);
    }

    toggleTheme() {
      const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
      document.documentElement.dataset.theme = next;
      localStorage.setItem("claude-tap-theme", next);
      this.syncThemeColor(next);
    }

    syncThemeColor(theme) {
      document.querySelector('meta[name="theme-color"]')?.setAttribute("content", theme === "dark" ? "#151517" : "#f9fafb");
    }

    applyResponsiveLayout(initial) {
      const nextMode = window.innerWidth < 800 ? "mobile" : window.innerWidth <= 1120 ? "compact" : "desktop";
      const changed = nextMode !== this.viewportMode;
      this.viewportMode = nextMode;
      if (changed || initial) {
        const stored = localStorage.getItem("claude-tap-sidebar");
        const state = nextMode === "desktop" ? stored || "open" : "collapsed";
        this.setSidebar(state, false);
      }
      this.syncTrajectoryDensity();
      this.syncSurfaceAccessibility();
    }

    syncTrajectoryDensity() {
      const width = $("call-panel").getBoundingClientRect().width || window.innerWidth;
      const density = width <= 520 ? "compact" : width <= 720 ? "medium" : "wide";
      $("call-panel").dataset.trajectoryDensity = density;
      this.trajectoryList.setRowHeight(density === "compact" ? 52 : density === "medium" ? 58 : 64);
    }

    setSidebar(state, persist, moveFocus = false) {
      const shell = $("workspace");
      const previousState = shell.dataset.sidebar;
      this.cancelSidebarAsync();
      delete shell.dataset.sidebarPhase;
      if (state === "collapsed") {
        this.captureMenu.close();
        this.settingsMenu.close();
      }

      const animate =
        document.documentElement.dataset.viewerReady === "true" &&
        window.innerWidth >= 800 &&
        !window.matchMedia("(prefers-reduced-motion: reduce)").matches &&
        previousState !== state;
      shell.dataset.sidebar = state;
      if (animate && state === "collapsed") {
        shell.dataset.sidebarPhase = "collapsing";
        this.queueSidebarTimer(() => {
          shell.dataset.sidebarPhase = "rail-entering";
          this.sessionList.refresh();
        }, SIDEBAR_MOTION_MS / 2);
        this.queueSidebarTimer(() => {
          delete shell.dataset.sidebarPhase;
          this.sessionList.refresh();
          this.trajectoryList.refresh();
        }, SIDEBAR_MOTION_MS + SIDEBAR_SETTLE_GRACE_MS);
      } else if (animate && state === "open") {
        shell.dataset.sidebarPhase = "expanding";
        this.queueSidebarTimer(() => {
          delete shell.dataset.sidebarPhase;
          this.sessionList.refresh();
          this.trajectoryList.refresh();
        }, SIDEBAR_MOTION_MS + SIDEBAR_SETTLE_GRACE_MS);
      }

      if (persist && window.innerWidth >= 800) localStorage.setItem("claude-tap-sidebar", state);
      if (moveFocus && window.innerWidth < 800 && state === "open") {
        this.setSidebarSearchExpanded(true, false);
      }
      this.syncSurfaceAccessibility();
      if (moveFocus && window.innerWidth < 800) {
        focusSafely(state === "open" ? $("session-search") : $("open-sidebar"));
      }
      this.queueSidebarFrame(() => {
        // Sidebar width is a CSS concern; retain mounted rows so selection,
        // focus, and in-flight pointer interactions survive the transition.
        this.sessionList.refresh();
        this.trajectoryList.refresh();
      });
    }

    cancelSidebarAsync() {
      this.sidebarTransitionSequence += 1;
      for (const timer of this.sidebarTimers) window.clearTimeout(timer);
      this.sidebarTimers.clear();
      for (const frame of this.sidebarFrames) window.cancelAnimationFrame(frame);
      this.sidebarFrames.clear();
      this.cancelSidebarSearchFocus();
    }

    queueSidebarTimer(callback, delay) {
      const sequence = this.sidebarTransitionSequence;
      const timer = window.setTimeout(() => {
        this.sidebarTimers.delete(timer);
        if (sequence === this.sidebarTransitionSequence) callback();
      }, delay);
      this.sidebarTimers.add(timer);
      return timer;
    }

    queueSidebarFrame(callback) {
      const sequence = this.sidebarTransitionSequence;
      const frame = window.requestAnimationFrame(() => {
        this.sidebarFrames.delete(frame);
        if (sequence === this.sidebarTransitionSequence) callback();
      });
      this.sidebarFrames.add(frame);
      return frame;
    }

    afterSidebarOpen(callback) {
      const animated =
        document.documentElement.dataset.viewerReady === "true" &&
        window.innerWidth >= 800 &&
        !window.matchMedia("(prefers-reduced-motion: reduce)").matches;
      this.queueSidebarTimer(() => {
        if ($("workspace").dataset.sidebar === "open") callback();
      }, animated ? SIDEBAR_MOTION_MS + SIDEBAR_SETTLE_GRACE_MS + 5 : 0);
    }

    setSidebarSearchExpanded(expanded, focus = false) {
      this.cancelSidebarSearchFocus();
      const browser = $("sidebar-browser");
      browser.dataset.searchExpanded = String(Boolean(expanded));
      $("session-search-toggle").setAttribute("aria-expanded", String(Boolean(expanded)));
      $("session-search").tabIndex = expanded ? 0 : -1;
      this.syncSidebarSearchControls();
      this.sidebarTooltips?.headerSearch.setDisabled(Boolean(expanded));
      if (focus && expanded) {
        const sequence = this.sidebarSearchSequence;
        this.sidebarSearchFrame = window.requestAnimationFrame(() => {
          this.sidebarSearchFrame = 0;
          if (
            sequence === this.sidebarSearchSequence &&
            $("workspace").dataset.sidebar === "open" &&
            browser.dataset.searchExpanded === "true"
          ) {
            focusSafely($("session-search"));
          }
        });
      }
    }

    cancelSidebarSearchFocus() {
      this.sidebarSearchSequence += 1;
      if (!this.sidebarSearchFrame) return;
      window.cancelAnimationFrame(this.sidebarSearchFrame);
      this.sidebarSearchFrame = 0;
    }

    syncSidebarSearchControls() {
      const expanded = $("sidebar-browser").dataset.searchExpanded === "true";
      $("session-search-clear").hidden = !expanded;
      $("session-search").tabIndex = expanded ? 0 : -1;
    }

    syncSidebarControls() {
      const open = $("workspace").dataset.sidebar === "open";
      $("toggle-sidebar").setAttribute("aria-expanded", String(open));
      $("toggle-sidebar").ariaLabel = this.t(open ? "closeSessions" : "openSessions");
      $("open-sidebar").setAttribute("aria-expanded", String(open));
      $("sidebar-browser").inert = !open && window.innerWidth >= 800;
      this.sidebarTooltips?.toggle.setLabel(() => this.t(open ? "closeSessions" : "openSessions"));
      this.sidebarTooltips?.capture.setDisabled(open || window.innerWidth < 800);
      this.sidebarTooltips?.search.setDisabled(open || window.innerWidth < 800);
      this.sidebarTooltips?.settings.setDisabled(open || window.innerWidth < 800);
      this.sidebarTooltips?.toggle.setDisabled(window.innerWidth < 800);
      this.sidebarTooltips?.headerCapture.setDisabled(!open || window.innerWidth < 800);
      this.sidebarTooltips?.headerSearch.setDisabled(
        !open || window.innerWidth < 800 || $("sidebar-browser").dataset.searchExpanded === "true",
      );
    }

    syncSurfaceAccessibility() {
      const sidebarOpen = $("workspace").dataset.sidebar === "open";
      const inspectorOpen = $("workspace").dataset.inspector === "open";
      const mobileDrawerOpen = window.innerWidth < 800 && sidebarOpen;
      const overlayInspectorOpen = window.innerWidth < 900 && inspectorOpen;
      const callPanel = $("call-panel");
      const sessionPanel = $("session-panel");
      const sidebarBrowser = $("sidebar-browser");
      const callPanelInert = mobileDrawerOpen || overlayInspectorOpen;
      const sessionPanelInert = (window.innerWidth < 800 && !sidebarOpen) || overlayInspectorOpen;
      const sidebarBrowserInert = !sidebarOpen && window.innerWidth >= 800;

      // Make the destination interactive before moving focus out of a surface
      // that is about to become inert. This avoids leaving document focus in
      // hidden desktop sidebar content or an off-canvas mobile drawer.
      if (!callPanelInert) callPanel.inert = false;
      if (!sessionPanelInert) sessionPanel.inert = false;
      const openMenu = DropdownMenu.openMenu;
      if (
        openMenu &&
        ((callPanelInert && callPanel.contains(openMenu.trigger)) ||
          (sessionPanelInert && sessionPanel.contains(openMenu.trigger)) ||
          (sidebarBrowserInert && sidebarBrowser.contains(openMenu.trigger)))
      ) {
        openMenu.close();
      }
      if (overlayInspectorOpen) {
        moveFocusBeforeInert(callPanel, $("close-inspector"));
        moveFocusBeforeInert(sessionPanel, $("close-inspector"));
      } else if (mobileDrawerOpen) {
        moveFocusBeforeInert(callPanel, $("session-search"), $("toggle-sidebar"));
      } else if (sessionPanelInert) {
        moveFocusBeforeInert(sessionPanel, $("open-sidebar"));
      } else if (sidebarBrowserInert) {
        moveFocusBeforeInert(sidebarBrowser, $("toggle-sidebar"));
      }

      callPanel.inert = callPanelInert;
      sessionPanel.inert = sessionPanelInert;
      sessionPanel.setAttribute("aria-hidden", String(sessionPanelInert));
      this.syncSidebarControls();
    }

    captureMenuItems() {
      return this.store.state.captures.map((capture) => ({
        id: capture.id,
        label: this.captureLabel(capture),
        detail: `${formatNumber(capture.record_count || 0)} ${this.t("calls")}`,
        radio: true,
        checked: capture.id === this.store.state.currentCaptureId,
      }));
    }

    modelMenuItems() {
      const current = this.store.state.filters.model;
      const models = Array.from(new Set(this.store.sessionMetadata().map((item) => item.model).filter(Boolean))).sort();
      return [
        { id: "__all__", label: this.t("allModels"), radio: true, checked: !current },
        ...models.map((model) => ({ id: model, label: model, radio: true, checked: model === current })),
      ];
    }

    settingsMenuItems() {
      const dark = document.documentElement.dataset.theme === "dark";
      const streamLabels = {
        live: this.t("connected"),
        reconnecting: this.t("reconnecting"),
        error: this.t("disconnected"),
        offline: this.t("staticCapture"),
      };
      return [
        { id: "stream", label: streamLabels[this.streamState] || streamLabels.offline, heading: true },
        { id: "theme", label: this.t("theme"), icon: dark ? "light" : "dark" },
        { id: "language", label: this.t("language"), icon: "globe" },
      ];
    }

    sessionOptionItems() {
      const selected = this.store.state.selectedRecordIndex !== null;
      return [
        { id: "copy-session", label: this.t("copySession"), icon: "copy", disabled: !this.store.selectedSession() },
        { id: "toggle-inspector", label: this.t("openInspector"), icon: "panel", disabled: !selected },
      ];
    }

    handleSettings(id) {
      if (id === "theme") this.toggleTheme();
      else if (id === "language") {
        this.translator.toggle();
        this.translateStatic();
      }
    }

    async handleSessionOption(id) {
      if (id === "toggle-inspector") {
        this.inspector.reopen();
        return;
      }
      const session = this.store.selectedSession();
      if (id !== "copy-session" || !session) return;
      try {
        await copyText(session.session_id || session.key);
        this.toast(this.t("copied"));
      } catch (_) {
        this.toast(this.t("copyFailed"), true);
      }
    }

    captureLabel(capture) {
      const text = String(capture.id || "capture");
      const match = text.match(/^(\d{4}-\d{2}-\d{2})\/(\d{2})(\d{2})(\d{2})$/);
      return match ? `${match[1]} · ${match[2]}:${match[3]}:${match[4]}` : text;
    }

    renderCapturePicker() {
      const capture = this.store.state.captures.find((item) => item.id === this.store.state.currentCaptureId);
      $("capture-value").textContent = capture ? this.captureLabel(capture) : this.t("staticCapture");
      $("capture-trigger").disabled = this.store.state.captures.length === 0;
    }

    renderAll(preserveScroll = true) {
      this.renderCapturePicker();
      this.renderSessions(preserveScroll);
      this.renderActiveSession();
      this.renderTrajectory(preserveScroll);
      this.renderCaptureFoot();
      this.updateReopenButton();
    }

    filteredSessions() {
      if (!this.sessionQuery) return this.store.state.sessions;
      return this.store.state.sessions.filter((session) =>
        [session.title, session.client, session.protocol, ...(session.models || [])]
          .filter(Boolean)
          .join("\n")
          .toLowerCase()
          .includes(this.sessionQuery),
      );
    }

    renderSessions(preserveScroll = true) {
      const sessions = this.filteredSessions();
      $("session-count").textContent = String(sessions.length);
      this.sessionList.setItems(sessions, preserveScroll);
    }

    renderSessionRow(session) {
      const row = document.createElement("button");
      row.type = "button";
      row.className = `session-row${session.key === this.store.state.selectedSessionKey ? " selected" : ""}`;
      row.dataset.sessionKey = session.key;
      row.setAttribute("aria-current", String(session.key === this.store.state.selectedSessionKey));
      const title = session.title;
      row.innerHTML = `
        <span class="session-row-head"><span class="session-row-title">${escapeHtml(title)}</span><time>${escapeHtml(
          formatClock(session.started_at),
        )}</time></span>`;
      row.title = title;
      row.addEventListener("click", () => this.store.selectSession(session.key));
      return row;
    }

    renderActiveSession() {
      const session = this.store.selectedSession();
      $("session-options-trigger").disabled = !session;
      if (!session) {
        $("active-session-title").textContent = this.t("selectSession");
        $("active-session-facts").textContent = "";
        this.renderModelPicker();
        return;
      }
      $("active-session-title").textContent = session.title;
      // This header identifies the session. Aggregated token totals are not
      // actionable here; request-level usage remains beside each model call.
      $("active-session-facts").textContent = "";
      this.renderModelPicker();
    }

    renderModelPicker() {
      const current = this.store.state.filters.model;
      const models = Array.from(new Set(this.store.sessionMetadata().map((item) => item.model).filter(Boolean)));
      $("model-value").textContent = current || this.t("allModels");
      const wrapper = $("model-trigger").closest(".model-picker");
      wrapper.hidden = models.length <= 1;
      if (current && !models.includes(current)) this.store.setFilter("model", "");
    }

    renderTrajectory(preserveScroll = true) {
      const metadata = this.store.visibleMetadata();
      this.trajectoryList.setItems(metadata, preserveScroll);
      $("result-count").textContent = `${formatNumber(metadata.length)} ${this.t(metadata.length === 1 ? "call" : "calls")}`;
      $("empty-state").hidden = metadata.length > 0;
    }

    renderTrajectoryRow(meta) {
      const row = document.createElement("button");
      row.type = "button";
      row.className = `trajectory-row${meta.record_index === this.store.state.selectedRecordIndex ? " selected" : ""}`;
      row.dataset.recordIndex = String(meta.record_index);
      const calls = meta.tool_calls || [];
      const failed = Number(meta.status || 0) >= 400 || meta.error_message;
      const special =
        meta.task_kind === "title-generation"
          ? this.t("titleGeneration")
          : meta.task_kind === "compaction"
            ? this.t("compaction")
            : "";
      const errorOutput = meta.error_message || (failed ? this.t("requestFailed") : "");
      const assistantOutput = readableAssistantHint(meta.assistant_hint);
      const requestOutput = latestRequestHint(meta);
      const output = special && assistantOutput
        ? `${special} · ${assistantOutput}`
        : assistantOutput || special || "";
      const outputPreview = compactLedgerText(output, 720);
      const visibleCalls = calls.slice(0, 2);
      const toolPreview = visibleCalls
        .map((call) => {
          const hint = compactToolHint(call.input_preview);
          return `<span class="row-tool"><span class="row-tool-icon">${toolIcon(
            12,
          )}</span><span class="row-tool-name">${escapeHtml(call.name || "tool")}</span>${
            hint ? `<span class="row-tool-args">${escapeHtml(hint)}</span>` : ""
          }</span>`;
        })
        .join("");
      const freshInput = Number(meta.input_tokens || 0);
      const cacheRead = Number(meta.cache_read_input_tokens || 0);
      const cacheWrite = Number(meta.cache_creation_input_tokens || 0);
      const totalInput = freshInput + cacheRead + cacheWrite;
      const cachePercent = totalInput > 0 && cacheRead > 0 ? Math.round((cacheRead / totalInput) * 100) : 0;
      const usageDetail = [
        `Fresh input  ${formatNumber(freshInput)}`,
        cacheRead ? `Cache read   ${formatNumber(cacheRead)}` : "",
        cacheWrite ? `Cache write  ${formatNumber(cacheWrite)}` : "",
        `Output       ${formatNumber(meta.output_tokens || 0)}`,
      ]
        .filter(Boolean)
        .join("\n");
      const toolFact = calls.length
        ? `<span class="call-tool-fact${calls.length > visibleCalls.length ? " has-overflow" : ""}">${escapeHtml(
            `${calls.length} ${calls.length === 1 ? "tool" : "tools"}`,
          )}</span>`
        : "";
      const taskFact = meta.task_kind === "title-generation"
        ? '<span class="call-kind">Title</span>'
        : meta.task_kind === "compaction"
          ? '<span class="call-kind">Compaction</span>'
          : "";
      const usageItems = totalInput || meta.output_tokens
        ? `<span class="usage-item"><span>${escapeHtml(formatNumber(totalInput))}</span><span class="usage-label">input</span></span><span class="usage-item usage-output"><span>${escapeHtml(
            formatNumber(meta.output_tokens || 0),
          )}</span><span class="usage-label">output</span></span>`
        : "";
      const usageAttributes = usageItems
        ? ` data-usage-detail="${escapeHtml(usageDetail)}" aria-label="${escapeHtml(
            `${this.t("inputTokens")}: ${formatNumber(totalInput)} · ${this.t("outputTokens")}: ${formatNumber(meta.output_tokens || 0)}`,
          )}"`
        : "";
      const usage = usageItems ? `<span class="call-usage"${usageAttributes}>${usageItems}</span>` : "";
      const status = requestStatus(meta);
      const statusText = status?.label || "";
      const primaryOutput = outputPreview || (failed ? compactLedgerText(errorOutput || this.t("requestFailed")) : "");
      const hasOutput = Boolean(primaryOutput);
      const summaryKind = hasOutput ? (calls.length ? "mixed" : "text") : calls.length ? "tools" : "empty";
      // A tool-bearing response is identified by the work it initiated. Keep
      // assistant prose in the accessible/hover preview instead of spending a
      // permanent ledger line on it. Text-only responses still lead with text.
      const activityHtml = `<span class="call-summary call-summary--${summaryKind}${failed ? " is-error" : ""}">${
        hasOutput && !calls.length
          ? `<span class="call-output-line"><span class="call-summary-text">${escapeHtml(primaryOutput)}</span></span>`
          : ""
      }${calls.length ? `<span class="call-tool-list">${toolPreview}</span>` : ""}${
        summaryKind === "empty" ? '<span class="call-empty-mark" aria-hidden="true">—</span>' : ""
      }</span>`;
      const spokenOutput = failed
        ? `${requestOutput || assistantOutput || this.t("requestSent")}; ${errorOutput || this.t("requestFailed")}`
        : calls.length
          ? `${calls.map((call) => call.name).join(", ")}${output ? `; ${output}` : ""}`
          : output;
      const callNumber = Number(meta._session_call_index || meta.record_index + 1);
      const occurredAt = formatClock(meta.timestamp);
      const occurredAtShort = formatClock(meta.timestamp, false);
      row.classList.toggle("failed", Boolean(failed));
      row.classList.toggle("has-tools", calls.length > 0);
      row.classList.toggle("has-prose", hasOutput);
      row.setAttribute("aria-current", String(meta.record_index === this.store.state.selectedRecordIndex));
      row.setAttribute(
        "aria-label",
        [
          `${this.t("call")} ${callNumber}, ${occurredAt}: ${spokenOutput}`,
          meta.model || this.t("unknown"),
          totalInput ? `${this.t("inputTokens")}: ${formatNumber(totalInput)}` : "",
          meta.output_tokens ? `${this.t("outputTokens")}: ${formatNumber(meta.output_tokens)}` : "",
          formatDuration(meta.duration_ms),
          statusText,
        ]
          .filter(Boolean)
          .join("; "),
      );
      row.innerHTML = `<span class="call-entry">
        <span class="call-index"><time class="call-clock" datetime="${escapeHtml(
          meta.timestamp || "",
        )}" title="${escapeHtml(meta.timestamp || "")}"><span class="call-clock-full">${escapeHtml(
          occurredAt,
        )}</span><span class="call-clock-short">${escapeHtml(occurredAtShort)}</span></time><span class="call-sequence" aria-label="${escapeHtml(
          `${this.t("call")} ${callNumber}`,
        )}">#${escapeHtml(String(callNumber))}</span></span>
        <span class="call-body">
          <span class="call-activity">${activityHtml}</span>
          <span class="call-meta">
            <span class="call-model-cell" title="${escapeHtml(meta.model || "")}"><span class="call-model">${escapeHtml(
              meta.model || this.t("unknown"),
            )}</span>${usage}${cachePercent ? `<span class="call-cache">${cachePercent}% cached</span>` : ""}${toolFact}${taskFact}</span>
          </span>
        </span>
        <span class="call-timing"><span class="call-duration">${escapeHtml(formatDuration(meta.duration_ms))}</span>${
          status ? `<span class="call-status ${escapeHtml(status.kind)}">${escapeHtml(statusText)}</span>` : ""
        }</span>
      </span>`;
      const activityPreview = [
        ...calls.map((call) => {
          const hint = compactToolHint(call.input_preview);
          return [call.name || "tool", hint].filter(Boolean).join("  ");
        }),
        output || (failed ? errorOutput : ""),
      ]
        .filter(Boolean)
        .join("\n");
      let activityTooltip = null;
      if (activityPreview) {
        activityTooltip = new FixedTooltip(row.querySelector(".call-summary"), activityPreview, {
          side: "bottom",
          delayMs: 320,
          maxWidth: 440,
          variant: "preview",
        });
      }
      const usageTarget = row.querySelector(".call-usage");
      const usageTooltip = usageTarget?.dataset.usageDetail
        ? new FixedTooltip(usageTarget, usageTarget.dataset.usageDetail, { side: "bottom", delayMs: 320, maxWidth: 280, variant: "preview" })
        : null;
      row.__claudeTapDispose = () => {
        activityTooltip?.destroy();
        usageTooltip?.destroy();
      };
      row.addEventListener("click", () => {
        if (meta.record_index === this.store.state.selectedRecordIndex && this.store.state.selectedRecord) this.inspector.reopen();
        else this.store.selectRecord(meta);
      });
      row.addEventListener("pointerenter", () => this.store.prefetchRecord(meta), { once: true });
      row.addEventListener("focus", () => this.store.prefetchRecord(meta), { once: true });
      return row;
    }

    renderCaptureFoot() {
      // Capture totals already exist where they can be acted on: the session
      // row and the filter-sensitive call count. Repeating them in the footer
      // only spends scarce sidebar space. Connection detail remains available
      // from the settings menu.
      $("capture-foot").textContent = "";
      $("capture-foot").hidden = true;
    }

    setStreamStatus(state) {
      this.streamState = state;
      this.renderCaptureFoot();
    }

    scheduleLiveRender() {
      if (this.renderScheduled) return;
      this.renderScheduled = true;
      requestAnimationFrame(() => {
        this.renderScheduled = false;
        this.renderSessions(true);
        this.renderActiveSession();
        this.renderTrajectory(true);
        this.renderCaptureFoot();
      });
    }

    updateReopenButton() {
      const selected = this.store.state.selectedRecordIndex !== null;
      const closed = $("workspace").dataset.inspector !== "open";
      $("reopen-inspector").hidden = !(selected && closed);
    }

    handleTrajectoryKey(event) {
      if (!["ArrowDown", "ArrowUp", "j", "k", "Enter"].includes(event.key)) return;
      const items = this.store.visibleMetadata();
      if (!items.length) return;
      const current = items.findIndex((item) => item.record_index === this.store.state.selectedRecordIndex);
      if (event.key === "Enter" && current >= 0) {
        this.store.selectRecord(items[current]);
        return;
      }
      const direction = event.key === "ArrowUp" || event.key === "k" ? -1 : 1;
      const next = Math.max(0, Math.min(items.length - 1, current < 0 ? 0 : current + direction));
      event.preventDefault();
      this.trajectoryList.scrollToIndex(next);
      this.store.selectRecord(items[next]);
    }

    updateHash() {
      if (this.restoringRoute) return;
      const params = new URLSearchParams();
      if (this.store.state.currentCaptureId) params.set("capture", this.store.state.currentCaptureId);
      if (this.store.state.selectedSessionKey) params.set("session", this.store.state.selectedSessionKey);
      if (this.store.state.selectedRecordIndex !== null) params.set("record", String(this.store.state.selectedRecordIndex));
      history.replaceState(null, "", `${location.pathname}${location.search}#${params.toString()}`);
    }

    readHash() {
      const params = new URLSearchParams(location.hash.replace(/^#/, ""));
      const recordValue = params.get("record");
      const record = recordValue === null ? Number.NaN : Number(recordValue);
      return {
        capture: params.get("capture") || "",
        session: params.get("session") || "",
        record: Number.isInteger(record) && record >= 0 ? record : null,
      };
    }

    async restoreHash() {
      const route = this.initialRoute;
      if (route.session && this.store.state.sessions.some((session) => session.key === route.session)) {
        this.store.selectSession(route.session);
      }
      if (route.record !== null) {
        const metadata = this.store.sessionMetadata().find((item) => item.record_index === route.record);
        if (metadata) await this.store.selectRecord(metadata);
      }
    }

    toast(message, isError = false) {
      const toast = document.createElement("div");
      toast.className = `toast${isError ? " error" : ""}`;
      toast.textContent = message;
      $("toast-region").appendChild(toast);
      setTimeout(() => toast.remove(), 2200);
    }

    handleError(error) {
      console.error(error);
      this.toast(error?.message || String(error), true);
      this.inspector.showError();
    }
  }

  window.addEventListener("DOMContentLoaded", () => {
    const app = new TraceApp();
    window.claudeTapApp = app;
    app.start();
  });
})();
