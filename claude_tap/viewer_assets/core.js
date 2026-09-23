(function () {
  "use strict";

  const EVENTS = Object.freeze({
    CATALOG_LOADED: "catalog.loaded",
    CAPTURE_LOADING: "capture.loading",
    CAPTURE_LOADED: "capture.loaded",
    SESSION_SELECTED: "session.selected",
    FILTER_CHANGED: "filter.changed",
    RECORD_SELECTED: "record.selected",
    RECORD_LOADING: "record.loading",
    RECORD_LOADED: "record.loaded",
    RECORD_APPENDED: "record.appended",
    STREAM_STATUS: "stream.status",
    ERROR: "app.error",
  });

  class EventHub {
    constructor() {
      this.listeners = new Map();
    }

    on(eventName, listener) {
      if (!this.listeners.has(eventName)) this.listeners.set(eventName, new Set());
      this.listeners.get(eventName).add(listener);
      return () => this.listeners.get(eventName)?.delete(listener);
    }

    emit(eventName, payload) {
      for (const listener of this.listeners.get(eventName) || []) {
        try {
          listener(payload);
        } catch (error) {
          if (eventName !== EVENTS.ERROR) this.emit(EVENTS.ERROR, error);
        }
      }
    }
  }

  class LruCache {
    constructor(limit = 32) {
      this.limit = limit;
      this.values = new Map();
    }

    get(key) {
      if (!this.values.has(key)) return undefined;
      const value = this.values.get(key);
      this.values.delete(key);
      this.values.set(key, value);
      return value;
    }

    peek(key) {
      return this.values.get(key);
    }

    set(key, value) {
      if (this.values.has(key)) this.values.delete(key);
      this.values.set(key, value);
      while (this.values.size > this.limit) this.values.delete(this.values.keys().next().value);
    }

  }

  const TEXT = {
    en: {
      capture: "Capture",
      sessions: "Sessions",
      searchSessions: "Search sessions",
      clearSearch: "Clear search",
      chooseCapture: "Choose capture",
      settings: "Settings",
      searchTrace: "Search output, tools, prompts",
      searchTools: "Filter tools",
      searchToolsCount: "Filter {count} mounted tools",
      searchJson: "Filter JSON fields or values",
      staticCapture: "Static capture",
      connected: "Live",
      reconnecting: "Reconnecting",
      disconnected: "Disconnected",
      selectSession: "Select a session",
      allModels: "All models",
      noMatches: "No model calls match",
      clearFilter: "Try another search or session.",
      call: "call",
      calls: "calls",
      duration: "duration",
      inputTokens: "input",
      outputTokens: "output",
      errors: "errors",
      model: "Model",
      time: "Time",
      modelUsage: "Model / usage",
      timeStatus: "Duration",
      inputShort: "input",
      outputShort: "output",
      error: "Error",
      activity: "Activity",
      conversation: "Messages",
      system: "System",
      tools: "Tools",
      request: "Request JSON",
      requestShort: "Request",
      response: "Response JSON",
      responseShort: "Response",
      headers: "Headers",
      raw: "Raw",
      rawShort: "Raw",
      loading: "Loading model call…",
      loadFailed: "Could not load this model call",
      noData: "No data for this view.",
      copied: "Copied",
      copyFailed: "Clipboard access failed",
      copyView: "Copy current view",
      copyRequest: "Copy request JSON",
      copyResponse: "Copy response JSON",
      copyCurl: "Copy as cURL",
      copySession: "Copy session ID",
      theme: "Switch theme",
      language: "中文",
      wrap: "Wrap long lines",
      unwrap: "Keep original lines",
      showFull: "Show full value",
      truncated: "Large value truncated to keep the viewer responsive.",
      input: "Input",
      output: "Output",
      assistant: "Assistant",
      user: "User",
      context: "Context",
      auxiliary: "Auxiliary",
      titleGeneration: "Session title",
      compaction: "Context compaction",
      noResponse: "No model output",
      openSessions: "Open sidebar",
      closeSessions: "Collapse sidebar",
      openInspector: "Open selected model call",
      closeInspector: "Close inspector",
      modelCall: "MODEL CALL",
      unknown: "Unknown",
      toolResult: "Tool result",
      result: "result",
      requestFailed: "Request failed",
      requestSent: "Request sent",
      responseKind: "RESPONSE",
      streamError: "SSE error",
      toolDefinitions: "Tool definitions",
      parameters: "Parameters",
      configuration: "Configuration",
      required: "Required",
      noParameters: "No parameters",
      copyValue: "Copy value",
      expandNode: "Expand JSON node",
      collapseNode: "Collapse JSON node",
      noJsonMatches: "No JSON fields match this search.",
      inspectHint: "Select a model call to inspect its original request and response.",
    },
    zh: {
      capture: "抓取",
      sessions: "会话",
      searchSessions: "搜索会话",
      clearSearch: "清除搜索",
      chooseCapture: "选择抓取",
      settings: "设置",
      searchTrace: "搜索输出、工具、提示词",
      searchTools: "筛选工具",
      searchToolsCount: "筛选 {count} 个已挂载工具",
      searchJson: "筛选 JSON 字段或值",
      staticCapture: "静态抓取",
      connected: "实时",
      reconnecting: "重连中",
      disconnected: "已断开",
      selectSession: "请选择会话",
      allModels: "全部模型",
      noMatches: "没有匹配的模型调用",
      clearFilter: "换个关键词，或选择其他会话。",
      call: "Call",
      calls: "次调用",
      duration: "耗时",
      inputTokens: "Input",
      outputTokens: "Output",
      errors: "错误",
      model: "Model",
      time: "Time",
      modelUsage: "Model / usage",
      timeStatus: "Duration",
      inputShort: "Input",
      outputShort: "Output",
      error: "Error",
      activity: "Activity",
      conversation: "Messages",
      system: "System",
      tools: "Tools",
      request: "Request JSON",
      requestShort: "Request",
      response: "Response JSON",
      responseShort: "Response",
      headers: "Headers",
      raw: "Raw",
      rawShort: "Raw",
      loading: "正在加载模型调用…",
      loadFailed: "无法加载这次模型调用",
      noData: "当前视图没有数据。",
      copied: "已复制",
      copyFailed: "无法访问剪贴板",
      copyView: "复制当前视图",
      copyRequest: "复制请求 JSON",
      copyResponse: "复制响应 JSON",
      copyCurl: "复制为 cURL",
      copySession: "复制 Session ID",
      theme: "切换主题",
      language: "English",
      wrap: "长行换行",
      unwrap: "保持原始行",
      showFull: "显示完整内容",
      truncated: "内容过大，已截断以保持页面流畅。",
      input: "Input",
      output: "Output",
      assistant: "Assistant",
      user: "User",
      context: "Context",
      auxiliary: "辅助请求",
      titleGeneration: "会话标题",
      compaction: "上下文压缩",
      noResponse: "No model output",
      openSessions: "打开侧边栏",
      closeSessions: "收起侧边栏",
      openInspector: "打开已选模型调用",
      closeInspector: "关闭详情",
      modelCall: "MODEL CALL",
      unknown: "Unknown",
      toolResult: "Tool result",
      result: "result",
      requestFailed: "Request failed",
      requestSent: "Request sent",
      responseKind: "RESPONSE",
      streamError: "SSE error",
      toolDefinitions: "Tool definitions",
      parameters: "Parameters",
      configuration: "Configuration",
      required: "Required",
      noParameters: "No parameters",
      copyValue: "复制值",
      expandNode: "展开 JSON 节点",
      collapseNode: "折叠 JSON 节点",
      noJsonMatches: "没有匹配的 JSON 字段。",
      inspectHint: "选择一次模型调用，查看它的原始请求与响应。",
    },
  };

  class Translator {
    constructor() {
      const stored = localStorage.getItem("claude-tap-language");
      this.language = stored || (navigator.language.toLowerCase().startsWith("zh") ? "zh" : "en");
    }

    t(key) {
      return TEXT[this.language]?.[key] || TEXT.en[key] || key;
    }

    toggle() {
      this.language = this.language === "zh" ? "en" : "zh";
      localStorage.setItem("claude-tap-language", this.language);
      document.documentElement.lang = this.language === "zh" ? "zh-CN" : "en";
      return this.language;
    }
  }

  function $(id) {
    return document.getElementById(id);
  }

  const FOCUSABLE_SELECTOR = [
    "a[href]",
    "area[href]",
    "button:not([disabled])",
    "input:not([disabled]):not([type='hidden'])",
    "select:not([disabled])",
    "textarea:not([disabled])",
    "iframe",
    "[contenteditable='true']",
    "[tabindex]:not([tabindex='-1'])",
  ].join(",");

  function canFocus(element) {
    if (!(element instanceof HTMLElement) || !element.isConnected) return false;
    if (element.hidden || element.closest("[inert], [aria-hidden='true']")) return false;
    if ("disabled" in element && element.disabled) return false;
    const style = window.getComputedStyle(element);
    return style.display !== "none" && style.visibility !== "hidden" && element.getClientRects().length > 0;
  }

  function focusSafely(element) {
    if (!canFocus(element)) return false;
    element.focus({ preventScroll: true });
    return document.activeElement === element;
  }

  function focusNextFrom(anchor, backwards = false) {
    if (!(anchor instanceof HTMLElement)) return false;
    if (backwards) return focusSafely(anchor);
    const candidates = Array.from(document.querySelectorAll(FOCUSABLE_SELECTOR)).filter(
      (element) => element.tabIndex >= 0 && canFocus(element),
    );
    const index = candidates.indexOf(anchor);
    for (let offset = 1; offset <= candidates.length; offset += 1) {
      const candidate = candidates[(Math.max(index, -1) + offset) % candidates.length];
      if (candidate !== anchor && focusSafely(candidate)) return true;
    }
    return focusSafely(anchor);
  }

  function moveFocusBeforeInert(container, ...fallbacks) {
    if (!(container instanceof HTMLElement) || !container.contains(document.activeElement)) return false;
    for (const fallback of fallbacks.flat()) {
      if (focusSafely(fallback)) return true;
    }
    if (document.activeElement instanceof HTMLElement) document.activeElement.blur();
    return false;
  }

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  function debounce(callback, delay = 120) {
    let timer = 0;
    return (...args) => {
      clearTimeout(timer);
      timer = window.setTimeout(() => callback(...args), delay);
    };
  }

  function formatNumber(value) {
    const number = Number(value || 0);
    if (number >= 1_000_000) return `${(number / 1_000_000).toFixed(number >= 10_000_000 ? 0 : 1)}M`;
    if (number >= 1_000) return `${(number / 1_000).toFixed(number >= 10_000 ? 0 : 1)}k`;
    return String(number);
  }

  function formatDuration(value) {
    const milliseconds = Number(value || 0);
    if (!milliseconds) return "—";
    if (milliseconds < 1000) return `${Math.round(milliseconds)}ms`;
    if (milliseconds < 60_000) return `${(milliseconds / 1000).toFixed(milliseconds < 10_000 ? 1 : 0)}s`;
    const minutes = Math.floor(milliseconds / 60_000);
    const seconds = Math.round((milliseconds % 60_000) / 1000);
    return `${minutes}m ${seconds}s`;
  }

  function formatClock(value, includeSeconds = true) {
    if (!value) return "";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value);
    const parts = [date.getHours(), date.getMinutes()];
    if (includeSeconds) parts.push(date.getSeconds());
    return parts.map((part) => String(part).padStart(2, "0")).join(":");
  }

  function safeJson(value, indentation = 2) {
    try {
      return JSON.stringify(value, null, indentation);
    } catch (_) {
      return String(value ?? "");
    }
  }

  async function copyText(value) {
    const text = String(value ?? "");
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return;
    }
    const area = document.createElement("textarea");
    area.value = text;
    area.style.position = "fixed";
    area.style.opacity = "0";
    document.body.appendChild(area);
    area.select();
    document.execCommand("copy");
    area.remove();
  }

  function shellQuote(value) {
    return `'${String(value).replaceAll("'", `'"'"'`)}'`;
  }

  function requestToCurl(record) {
    const request = record?.request || {};
    const base = record?.upstream_base_url || "https://api.example.com";
    const path = String(request.path || "");
    const url = path.startsWith("http") ? path : `${String(base).replace(/\/$/, "")}/${path.replace(/^\//, "")}`;
    const parts = ["curl", "-X", shellQuote(request.method || "POST"), shellQuote(url)];
    for (const [name, value] of Object.entries(request.headers || {})) {
      if (["content-length", "host"].includes(name.toLowerCase())) continue;
      parts.push("-H", shellQuote(`${name}: ${value}`));
    }
    if (request.body !== undefined && request.body !== null) {
      parts.push("--data-raw", shellQuote(typeof request.body === "string" ? request.body : safeJson(request.body, 0)));
    }
    return parts.join(" \\\n  ");
  }

  function createSessionSummary(item) {
    return {
      key: item.session_key || `capture:${item.capture_id || "standalone"}`,
      session_id: item.session_id || "",
      client: item.client || "unknown",
      client_version: item.client_version || "",
      protocol: item.protocol || "unknown",
      title: "",
      started_at: item.timestamp || "",
      ended_at: item.timestamp || "",
      first_record_index: item.record_index ?? 0,
      last_record_index: item.record_index ?? 0,
      record_count: 0,
      error_count: 0,
      duration_ms: 0,
      input_tokens: 0,
      output_tokens: 0,
      models: [],
      threads: new Set(),
      agents: new Set(),
      request_category: item.request_category || "model",
    };
  }

  function mergeSessionSummary(summary, item) {
    summary.record_count += 1;
    if (item.request_category === "model") summary.request_category = "model";
    summary.last_record_index = item.record_index ?? summary.last_record_index;
    summary.ended_at = item.timestamp || summary.ended_at;
    summary.duration_ms += Number(item.duration_ms || 0);
    summary.input_tokens +=
      Number(item.input_tokens || 0) +
      Number(item.cache_read_input_tokens || 0) +
      Number(item.cache_creation_input_tokens || 0);
    summary.output_tokens += Number(item.output_tokens || 0);
    if (Number(item.status || 0) >= 400 || item.error_message) summary.error_count += 1;
    if (item.model && !summary.models.includes(item.model)) summary.models.push(item.model);
    if (item.thread_id) summary.threads.add(item.thread_id);
    if (item.agent_id) summary.agents.add(item.agent_id);
    if (!summary.title && item.user_hint && !["title-generation", "compaction"].includes(item.task_kind)) {
      summary.title = item.user_hint;
    }
    return summary;
  }

  function finalizeSessionSummary(summary) {
    return {
      ...summary,
      title:
        summary.request_category === "auxiliary"
          ? "Auxiliary traffic"
          : summary.title || `${summary.client} session`,
      thread_count: summary.threads.size,
      agent_count: summary.agents.size,
    };
  }

  function sessionSummaries(metadata) {
    const groups = new Map();
    for (const item of metadata || []) {
      const key = item.session_key || `capture:${item.capture_id || "standalone"}`;
      if (!groups.has(key)) groups.set(key, createSessionSummary(item));
      mergeSessionSummary(groups.get(key), item);
    }
    return Array.from(groups.values())
      .sort((left, right) => left.first_record_index - right.first_record_index)
      .map(finalizeSessionSummary);
  }

  window.ClaudeTapCore = {
    EVENTS,
    EventHub,
    LruCache,
    Translator,
    $,
    canFocus,
    focusSafely,
    focusNextFrom,
    moveFocusBeforeInert,
    escapeHtml,
    debounce,
    formatNumber,
    formatDuration,
    formatClock,
    safeJson,
    copyText,
    requestToCurl,
    sessionSummaries,
    createSessionSummary,
    mergeSessionSummary,
    finalizeSessionSummary,
  };
})();
