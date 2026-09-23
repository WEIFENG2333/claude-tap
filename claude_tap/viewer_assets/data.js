(function () {
  "use strict";

  const {
    EVENTS,
    LruCache,
    sessionSummaries,
    createSessionSummary,
    mergeSessionSummary,
    finalizeSessionSummary,
  } = window.ClaudeTapCore;

  class StaticTraceSource {
    constructor() {
      this.capture =
        typeof EMBEDDED_CAPTURE_INFO !== "undefined"
          ? EMBEDDED_CAPTURE_INFO
          : { id: "standalone", record_count: 0, source_path: "" };
      this.metadata = typeof EMBEDDED_TRACE_META !== "undefined" ? EMBEDDED_TRACE_META : [];
      this.inline = typeof EMBEDDED_TRACE_DATA !== "undefined" ? EMBEDDED_TRACE_DATA : null;
      this.rawText = null;
      this.rawLineOffsets = null;
      this.codec = "raw";
      this.recordCache = new LruCache(24);
      this.pendingRecords = new Map();
    }

    async listCaptures() {
      return {
        current: this.capture.id,
        captures: [
          {
            id: this.capture.id,
            record_count: this.capture.record_count ?? this.metadata.length,
            source_path: this.capture.source_path || "",
            is_live: false,
          },
        ],
      };
    }

    async loadCapture(captureId) {
      if (captureId !== this.capture.id) throw new Error(`Unknown capture: ${captureId}`);
      return {
        capture: { ...this.capture, is_live: false },
        metadata: this.metadata,
        sessions: sessionSummaries(this.metadata),
      };
    }

    _ensureRawOffsets() {
      if (this.rawLineOffsets) return;
      const host = document.getElementById("trace-compressed") || document.getElementById("trace-raw");
      this.rawText = host?.textContent || "";
      this.codec = host?.dataset.codec || "raw";
      this.rawLineOffsets = [];
      let cursor = 0;
      while (cursor < this.rawText.length) {
        const next = this.rawText.indexOf("\n", cursor);
        const end = next === -1 ? this.rawText.length : next;
        if (this.rawText.slice(cursor, end).trim()) this.rawLineOffsets.push([cursor, end]);
        if (next === -1) break;
        cursor = next + 1;
      }
    }

    async loadRecord(captureId, recordIndex) {
      if (captureId !== this.capture.id) throw new Error(`Unknown capture: ${captureId}`);
      const cached = this.recordCache.get(recordIndex);
      if (cached !== undefined) return cached;
      if (this.pendingRecords.has(recordIndex)) return this.pendingRecords.get(recordIndex);
      const pending = (async () => {
        let record = null;
        if (this.inline) {
          record = this.inline[recordIndex] || null;
        } else {
          this._ensureRawOffsets();
          const location = this.rawLineOffsets[recordIndex];
          if (!location) return null;
          const raw = this.rawText.slice(location[0], location[1]);
          if (this.codec !== "gzip-base64") {
            record = JSON.parse(raw);
          } else {
            if (typeof DecompressionStream === "undefined") {
              throw new Error("This static trace requires a browser with gzip DecompressionStream support.");
            }
            const binary = atob(raw);
            const bytes = Uint8Array.from(binary, (character) => character.charCodeAt(0));
            const stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream("gzip"));
            record = JSON.parse(await new Response(stream).text());
          }
        }
        this.recordCache.set(recordIndex, record);
        return record;
      })();
      this.pendingRecords.set(recordIndex, pending);
      try {
        return await pending;
      } finally {
        this.pendingRecords.delete(recordIndex);
      }
    }

    peekRecord(captureId, recordIndex) {
      if (captureId !== this.capture.id) return undefined;
      const cached = this.recordCache.peek(recordIndex);
      if (cached !== undefined) return cached;
      return this.inline ? this.inline[recordIndex] : undefined;
    }

    connect() {}

    close() {}
  }

  class LiveTraceSource {
    constructor(hub) {
      this.hub = hub;
      this.eventSource = null;
      this.recordCache = new LruCache(40);
      this.pendingRecords = new Map();
    }

    async _json(url) {
      const response = await fetch(url, { headers: { Accept: "application/json" } });
      if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
      return response.json();
    }

    async listCaptures() {
      return this._json("/api/captures");
    }

    async loadCapture(captureId) {
      return this._json(`/api/captures/${captureId}/index`);
    }

    async loadRecord(captureId, recordIndex) {
      const key = `${captureId}:${recordIndex}`;
      const cached = this.recordCache.get(key);
      if (cached !== undefined) return cached;
      if (this.pendingRecords.has(key)) return this.pendingRecords.get(key);
      const pending = this._json(`/api/captures/${captureId}/records/${recordIndex}`).then((payload) => {
        this.recordCache.set(key, payload.record);
        return payload.record;
      });
      this.pendingRecords.set(key, pending);
      try {
        return await pending;
      } finally {
        this.pendingRecords.delete(key);
      }
    }

    peekRecord(captureId, recordIndex) {
      return this.recordCache.peek(`${captureId}:${recordIndex}`);
    }

    cacheRecord(captureId, recordIndex, record) {
      this.recordCache.set(`${captureId}:${recordIndex}`, record);
    }

    connect() {
      this.eventSource?.close();
      this.hub.emit(EVENTS.STREAM_STATUS, { state: "reconnecting" });
      this.eventSource = new EventSource("/api/stream");
      this.eventSource.addEventListener("open", () => this.hub.emit(EVENTS.STREAM_STATUS, { state: "live" }));
      this.eventSource.addEventListener("hello", (event) => {
        try {
          this.hub.emit(EVENTS.STREAM_STATUS, { state: "live", payload: JSON.parse(event.data) });
        } catch (_) {
          this.hub.emit(EVENTS.STREAM_STATUS, { state: "live" });
        }
      });
      this.eventSource.addEventListener("record", (event) => {
        try {
          const payload = JSON.parse(event.data);
          if (payload.type !== "record.appended") return;
          this.cacheRecord(payload.capture_id, payload.metadata.record_index, payload.record);
          this.hub.emit(EVENTS.RECORD_APPENDED, payload);
        } catch (error) {
          this.hub.emit(EVENTS.ERROR, error);
        }
      });
      this.eventSource.onerror = () => this.hub.emit(EVENTS.STREAM_STATUS, { state: "reconnecting" });
    }

    close() {
      this.eventSource?.close();
      this.eventSource = null;
    }
  }

  class TraceStore {
    constructor(source, hub) {
      this.source = source;
      this.hub = hub;
      this.captureRequest = 0;
      this.selectionRequest = 0;
      this.sessionItems = new Map();
      this.sessionBuilders = new Map();
      this.prefetchScheduled = new Set();
      this.state = {
        captures: [],
        currentCaptureId: "",
        capture: null,
        metadata: [],
        sessions: [],
        selectedSessionKey: "",
        selectedRecordIndex: null,
        selectedRecord: null,
        filters: { query: "", model: "" },
      };
      this.hub.on(EVENTS.RECORD_APPENDED, (payload) => this._appendRecord(payload));
    }

    async initialize(preferredCaptureId = "") {
      const catalog = await this.source.listCaptures();
      this.state.captures = catalog.captures || [];
      const preferredExists = this.state.captures.some((capture) => capture.id === preferredCaptureId);
      this.state.currentCaptureId = preferredExists ? preferredCaptureId : catalog.current || this.state.captures[0]?.id || "";
      this.hub.emit(EVENTS.CATALOG_LOADED, {
        captures: this.state.captures,
        current: this.state.currentCaptureId,
      });
      if (this.state.currentCaptureId) await this.loadCapture(this.state.currentCaptureId);
      this.source.connect();
    }

    async loadCapture(captureId) {
      const request = ++this.captureRequest;
      this.hub.emit(EVENTS.CAPTURE_LOADING, { captureId });
      const payload = await this.source.loadCapture(captureId);
      if (request !== this.captureRequest) return;
      this.state.currentCaptureId = captureId;
      this.state.capture = payload.capture;
      const capturePosition = this.state.captures.findIndex((capture) => capture.id === captureId);
      if (capturePosition >= 0) {
        this.state.captures[capturePosition] = { ...this.state.captures[capturePosition], ...payload.capture };
      }
      this.state.metadata = payload.metadata || [];
      this.prefetchScheduled.clear();
      this._rebuildIndexes();
      this.state.selectedRecordIndex = null;
      this.state.selectedRecord = null;
      const stillExists = this.state.sessions.some((session) => session.key === this.state.selectedSessionKey);
      this.state.selectedSessionKey = stillExists ? this.state.selectedSessionKey : this.state.sessions[0]?.key || "";
      this.hub.emit(EVENTS.CAPTURE_LOADED, this.snapshot());
      if (this.state.selectedSessionKey) this.hub.emit(EVENTS.SESSION_SELECTED, this.selectedSession());
    }

    _prepareMetadata(item) {
      if (item._search_text) return item;
      item._search_text = [
        item.user_hint,
        item.request_hint,
        item.assistant_hint,
        item.model,
        item.client,
        item.path,
        ...(item.tool_names || []),
        ...(item.response_tool_names || []),
        ...(item.tool_calls || []).flatMap((call) => [call.name, call.input_preview]),
        ...(item.tool_results || []).flatMap((result) => [result.name, result.output_preview]),
      ]
        .filter(Boolean)
        .join("\n")
        .toLowerCase();
      return item;
    }

    _indexMetadata(item) {
      if (item.request_category === "auxiliary") return false;
      this._prepareMetadata(item);
      const sessionKey = item.session_key || `capture:${item.capture_id || "standalone"}`;
      item.session_key = sessionKey;
      if (!this.sessionItems.has(sessionKey)) this.sessionItems.set(sessionKey, []);
      const sessionItems = this.sessionItems.get(sessionKey);
      sessionItems.push(item);
      item._session_call_index = sessionItems.length;
      if (!this.sessionBuilders.has(sessionKey)) this.sessionBuilders.set(sessionKey, createSessionSummary(item));
      mergeSessionSummary(this.sessionBuilders.get(sessionKey), item);
      return true;
    }

    _rebuildIndexes() {
      this.sessionItems.clear();
      this.sessionBuilders.clear();
      for (const item of this.state.metadata) this._indexMetadata(item);
      this.state.sessions = Array.from(this.sessionBuilders.values())
        .sort((left, right) => left.first_record_index - right.first_record_index)
        .map(finalizeSessionSummary);
    }

    selectSession(sessionKey) {
      if (!this.sessionBuilders.has(sessionKey)) return;
      if (sessionKey !== this.state.selectedSessionKey) {
        this.selectionRequest += 1;
        this.state.selectedRecordIndex = null;
        this.state.selectedRecord = null;
      }
      this.state.selectedSessionKey = sessionKey;
      this.hub.emit(EVENTS.SESSION_SELECTED, this.selectedSession());
    }

    setFilter(name, value) {
      if (!(name in this.state.filters)) return;
      this.state.filters[name] = value;
      this.hub.emit(EVENTS.FILTER_CHANGED, { ...this.state.filters });
    }

    selectedSession() {
      return this.state.sessions.find((session) => session.key === this.state.selectedSessionKey) || null;
    }

    sessionMetadata() {
      return this.sessionItems.get(this.state.selectedSessionKey) || [];
    }

    visibleMetadata() {
      const query = this.state.filters.query.trim().toLowerCase();
      const model = this.state.filters.model;
      return this.sessionMetadata().filter((item) => (!model || item.model === model) && (!query || item._search_text.includes(query)));
    }

    cachedRecord(metadata) {
      if (!metadata || typeof this.source.peekRecord !== "function") return undefined;
      return this.source.peekRecord(this.state.currentCaptureId, metadata.record_index);
    }

    prefetchRecord(metadata, idle = false) {
      if (!metadata || metadata.request_category === "auxiliary") return;
      const captureId = this.state.currentCaptureId;
      const key = `${captureId}:${metadata.record_index}`;
      if (this.cachedRecord(metadata) !== undefined || this.prefetchScheduled.has(key)) return;
      this.prefetchScheduled.add(key);
      const load = () => {
        this.prefetchScheduled.delete(key);
        if (captureId !== this.state.currentCaptureId) return;
        this.source.loadRecord(captureId, metadata.record_index).catch(() => {});
      };
      if (idle && typeof requestIdleCallback === "function") requestIdleCallback(load, { timeout: 700 });
      else if (idle) window.setTimeout(load, 32);
      else queueMicrotask(load);
    }

    _prefetchAdjacent(metadata) {
      const items = this.sessionMetadata();
      const index = items.findIndex((item) => item.record_index === metadata.record_index);
      if (index < 0) return;
      for (const offset of [1, -1, 2, -2]) {
        const item = items[index + offset];
        if (item) this.prefetchRecord(item, true);
      }
    }

    async selectRecord(metadata) {
      if (!metadata) return;
      const request = ++this.selectionRequest;
      this.state.selectedRecordIndex = metadata.record_index;
      const cached = this.cachedRecord(metadata);
      this.state.selectedRecord = cached === undefined ? null : cached;
      this.hub.emit(EVENTS.RECORD_SELECTED, metadata);
      this.hub.emit(EVENTS.RECORD_LOADING, metadata);
      try {
        const record = cached === undefined
          ? await this.source.loadRecord(this.state.currentCaptureId, metadata.record_index)
          : cached;
        if (request !== this.selectionRequest || this.state.selectedRecordIndex !== metadata.record_index) return;
        this.state.selectedRecord = record;
        this.hub.emit(EVENTS.RECORD_LOADED, {
          metadata,
          record,
        });
        this._prefetchAdjacent(metadata);
      } catch (error) {
        if (request === this.selectionRequest) this.hub.emit(EVENTS.ERROR, error);
      }
    }

    _appendRecord(payload) {
      if (payload.capture_id !== this.state.currentCaptureId) return;
      const item = payload.metadata;
      this.state.metadata.push(item);
      const indexed = this._indexMetadata(item);
      if (indexed) {
        const sessionKey = item.session_key;
        const builder = this.sessionBuilders.get(sessionKey);
        const nextSummary = finalizeSessionSummary(builder);
        const position = this.state.sessions.findIndex((session) => session.key === sessionKey);
        if (position >= 0) this.state.sessions[position] = nextSummary;
        else this.state.sessions.push(nextSummary);
        if (!this.state.selectedSessionKey) this.state.selectedSessionKey = sessionKey;
      }
      this.state.capture = {
        ...(this.state.capture || {}),
        record_count: this.state.metadata.length,
        is_live: true,
      };
    }

    snapshot() {
      return {
        ...this.state,
        filters: { ...this.state.filters },
        captures: [...this.state.captures],
        metadata: [...this.state.metadata],
        sessions: [...this.state.sessions],
      };
    }
  }

  function createSource(hub) {
    return typeof LIVE_MODE !== "undefined" && LIVE_MODE ? new LiveTraceSource(hub) : new StaticTraceSource();
  }

  window.ClaudeTapData = {
    StaticTraceSource,
    LiveTraceSource,
    TraceStore,
    createSource,
  };
})();
