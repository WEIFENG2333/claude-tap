"""Live viewer: serve the HTML and stream new records to the browser.

API surface (everything under ``/api/``, JSON unless noted):

    GET /                                  -> HTML viewer with LIVE_MODE = true
    GET /api/version                       -> {"server": str, "schema": int}
    GET /api/captures                      -> {"current": str|null, "captures": [...]}
    GET /api/captures/{date}/{id}/index    -> metadata + logical sessions
    GET /api/captures/{date}/{id}/records/{index} -> one full record
    GET /api/stream                        -> SSE: hello | record | heartbeat

Capture ids are ``"YYYY-MM-DD/HHMMSS"`` and map 1:1 to JSONL files. Logical
sessions are derived from normalized request identity and can coexist inside
one capture. The browser receives only metadata up front and fetches a full
record by byte offset when it is selected.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from aiohttp import web

from claude_tap import manifest as manifest_mod
from claude_tap._version import __version__
from claude_tap.catalog import TraceCatalog, summarize_sessions
from claude_tap.viewer import INJECT_MARKER, extract_metadata, load_viewer_template

# Bumped when an incompatible change is made to the JSON shape exchanged with
# the viewer. The browser uses this to detect a stale embedded copy.
SCHEMA_VERSION = 2

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TRACE_FILE_RE = re.compile(r"^trace_(\d{6})\.jsonl$")
_NO_STORE_HEADERS = {"Access-Control-Allow-Origin": "*", "Cache-Control": "no-store"}


@dataclass(frozen=True)
class _CaptureInfo:
    id: str
    date: str
    started_at: str
    record_count: int | None


class LiveViewerServer:
    """HTTP server providing the viewer + a thin JSON/SSE API."""

    def __init__(
        self,
        current_jsonl: Path | None,
        *,
        port: int = 0,
        host: str = "127.0.0.1",
        output_dir: Path,
    ) -> None:
        self._current_jsonl = current_jsonl
        self.port = port
        self.host = host
        self.output_dir = output_dir
        self._sse_clients: list[web.StreamResponse] = []
        self._runner: web.AppRunner | None = None
        self._actual_port = 0
        self._shutdown = asyncio.Event()
        self._catalog = TraceCatalog()
        self._catalog_lock = asyncio.Lock()
        self._live_record_index: int | None = None

    # ------------------------------------------------------------------
    # Public surface
    # ------------------------------------------------------------------

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self._actual_port}"

    @property
    def current_capture_id(self) -> str | None:
        if self._current_jsonl is None:
            return None
        return self._jsonl_to_session_id(self._current_jsonl)

    @property
    def current_session_id(self) -> str | None:
        """Backward-compatible alias for the pre-v2 capture terminology."""

        return self.current_capture_id

    async def start(self) -> int:
        self._live_record_index = self._current_record_count()
        app = web.Application()
        app.router.add_get("/", self._handle_index)
        app.router.add_get("/api/version", self._handle_version)
        app.router.add_get("/api/captures", self._handle_captures)
        app.router.add_get("/api/captures/{date}/{hhmmss}/index", self._handle_capture_index)
        app.router.add_get(
            "/api/captures/{date}/{hhmmss}/records/{record_index}",
            self._handle_capture_record,
        )
        # Pre-v2 aliases remain available for saved viewers.
        app.router.add_get("/api/sessions", self._handle_sessions)
        app.router.add_get("/api/sessions/{date}/{hhmmss}", self._handle_session_records)
        app.router.add_get("/api/stream", self._handle_stream)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self.host, self.port)
        await site.start()
        try:
            self._actual_port = site._server.sockets[0].getsockname()[1]
        except (AttributeError, IndexError, OSError):
            self._actual_port = self.port
        return self._actual_port

    async def stop(self) -> None:
        self._shutdown.set()
        for c in list(self._sse_clients):
            try:
                await c.write_eof()
            except Exception:
                pass
        self._sse_clients.clear()
        if self._runner:
            await self._runner.cleanup()

    async def broadcast(self, record: dict) -> None:
        """Push a record to every connected SSE client."""
        capture_id = self.current_capture_id or "live"
        metadata = extract_metadata(
            json.dumps(record, ensure_ascii=False, separators=(",", ":")), capture_id=capture_id
        ) or {"capture_id": capture_id}
        if self._live_record_index is None:
            self._live_record_index = self._current_record_count()
        metadata["record_index"] = self._live_record_index
        self._live_record_index += 1
        event = {
            "schema": SCHEMA_VERSION,
            "type": "record.appended",
            "capture_id": capture_id,
            "metadata": metadata,
            "record": record,
        }
        msg = ("event: record\ndata: " + json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n\n").encode(
            "utf-8"
        )
        dropped: list[web.StreamResponse] = []
        for client in self._sse_clients:
            try:
                await client.write(msg)
            except Exception:
                dropped.append(client)
        for c in dropped:
            if c in self._sse_clients:
                self._sse_clients.remove(c)

    # ------------------------------------------------------------------
    # ID <-> path helpers (with traversal guard)
    # ------------------------------------------------------------------

    def _jsonl_to_session_id(self, p: Path) -> str | None:
        try:
            rel = p.relative_to(self.output_dir)
        except ValueError:
            return None
        parts = rel.parts
        if len(parts) != 2:
            return None
        date_dir, fname = parts
        m = _TRACE_FILE_RE.match(fname)
        if m and _DATE_RE.match(date_dir):
            return f"{date_dir}/{m.group(1)}"
        return None

    def _session_id_to_path(self, date: str, hhmmss: str) -> Path | None:
        if not _DATE_RE.match(date) or not re.match(r"^\d{6}$", hhmmss):
            return None
        path = self.output_dir / date / f"trace_{hhmmss}.jsonl"
        try:
            path.resolve().relative_to(self.output_dir.resolve())
        except (ValueError, OSError):
            return None
        return path if path.is_file() else None

    # ------------------------------------------------------------------
    # Route handlers
    # ------------------------------------------------------------------

    async def _handle_index(self, request: web.Request) -> web.Response:
        html = load_viewer_template()
        if not html:
            return web.Response(status=404, text="viewer.html not found")
        capture_id = self.current_capture_id
        inject = (
            "<script>\n"
            "const LIVE_MODE = true;\n"
            f"const LIVE_SCHEMA = {SCHEMA_VERSION};\n"
            f"const CURRENT_CAPTURE_ID = {json.dumps(capture_id)};\n"
            f"const CURRENT_SESSION_ID = {json.dumps(capture_id)};\n"
            f"const __CLAUDE_TAP_VERSION__ = {json.dumps(__version__)};\n"
            "</script>"
        )
        html = html.replace(INJECT_MARKER, inject + "\n" + INJECT_MARKER, 1)
        return web.Response(text=html, content_type="text/html", headers={"Cache-Control": "no-store"})

    async def _handle_version(self, request: web.Request) -> web.Response:
        return web.json_response(
            {"server": __version__, "schema": SCHEMA_VERSION},
            headers=_NO_STORE_HEADERS,
        )

    async def _handle_sessions(self, request: web.Request) -> web.Response:
        captures = list(self._enumerate_captures())
        return web.json_response(
            {
                "current": self.current_capture_id,
                "sessions": [capture.__dict__ for capture in captures],
            },
            headers=_NO_STORE_HEADERS,
        )

    async def _handle_captures(self, request: web.Request) -> web.Response:
        captures = list(self._enumerate_captures())
        return web.json_response(
            {
                "schema": SCHEMA_VERSION,
                "current": self.current_capture_id,
                "captures": [capture.__dict__ for capture in captures],
            },
            headers=_NO_STORE_HEADERS,
        )

    async def _handle_capture_index(self, request: web.Request) -> web.Response:
        date = request.match_info["date"]
        hhmmss = request.match_info["hhmmss"]
        path = self._session_id_to_path(date, hhmmss)
        if path is None:
            return web.Response(status=404, text="capture not found")
        capture_id = f"{date}/{hhmmss}"
        try:
            async with self._catalog_lock:
                indexed = await asyncio.to_thread(self._catalog.index, path, capture_id)
        except OSError as exc:
            return web.Response(status=500, text=str(exc))
        return web.json_response(
            {
                "schema": SCHEMA_VERSION,
                "capture": {
                    "id": capture_id,
                    "record_count": len(indexed.metadata),
                    "size_bytes": indexed.file_size,
                    "is_live": capture_id == self.current_capture_id,
                },
                "sessions": summarize_sessions(indexed.metadata),
                "metadata": indexed.metadata,
            },
            headers=_NO_STORE_HEADERS,
        )

    async def _handle_capture_record(self, request: web.Request) -> web.Response:
        date = request.match_info["date"]
        hhmmss = request.match_info["hhmmss"]
        path = self._session_id_to_path(date, hhmmss)
        if path is None:
            return web.Response(status=404, text="capture not found")
        try:
            record_index = int(request.match_info["record_index"])
        except ValueError:
            return web.Response(status=404, text="record not found")
        capture_id = f"{date}/{hhmmss}"
        try:
            async with self._catalog_lock:
                record = await asyncio.to_thread(self._catalog.read_record, path, capture_id, record_index)
        except OSError as exc:
            return web.Response(status=500, text=str(exc))
        if record is None:
            return web.Response(status=404, text="record not found")
        return web.json_response(
            {
                "schema": SCHEMA_VERSION,
                "capture_id": capture_id,
                "record_index": record_index,
                "record": record,
            },
            headers=_NO_STORE_HEADERS,
        )

    async def _handle_session_records(self, request: web.Request) -> web.Response:
        date = request.match_info["date"]
        hhmmss = request.match_info["hhmmss"]
        path = self._session_id_to_path(date, hhmmss)
        if path is None:
            return web.Response(status=404, text="session not found")
        records: list[dict] = []
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        except OSError as exc:
            return web.Response(status=500, text=str(exc))
        return web.json_response(
            {"id": f"{date}/{hhmmss}", "records": records},
            headers=_NO_STORE_HEADERS,
        )

    async def _handle_stream(self, request: web.Request) -> web.StreamResponse:
        resp = web.StreamResponse(
            status=200,
            headers={
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
                "Access-Control-Allow-Origin": "*",
            },
        )
        await resp.prepare(request)

        # Hello tells the browser which session this stream is following and
        # which schema version to expect, so it can detect a stale embedded
        # copy and offer a refresh.
        hello = {
            "type": "stream.connected",
            "capture": self.current_capture_id,
            "session": self.current_capture_id,
            "schema": SCHEMA_VERSION,
            "server": __version__,
        }
        try:
            await resp.write(("event: hello\ndata: " + json.dumps(hello) + "\n\n").encode("utf-8"))
        except (ConnectionError, ConnectionResetError):
            return resp

        self._sse_clients.append(resp)
        try:
            while not self._shutdown.is_set():
                try:
                    await asyncio.wait_for(self._shutdown.wait(), timeout=30)
                except asyncio.TimeoutError:
                    pass
                if self._shutdown.is_set():
                    break
                try:
                    await resp.write(b"event: heartbeat\ndata: \n\n")
                except (ConnectionError, ConnectionResetError, RuntimeError):
                    break
        except asyncio.CancelledError:
            pass
        finally:
            if resp in self._sse_clients:
                self._sse_clients.remove(resp)
        return resp

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _enumerate_captures(self):
        """Yield every capture under ``output_dir``, newest first."""
        if not self.output_dir.is_dir():
            return
        manifest_counts = self._manifest_record_counts()
        for date_dir in sorted(self.output_dir.iterdir(), reverse=True):
            if not date_dir.is_dir() or not _DATE_RE.match(date_dir.name):
                continue
            for jsonl in sorted(date_dir.glob("trace_*.jsonl"), reverse=True):
                m = _TRACE_FILE_RE.match(jsonl.name)
                if not m:
                    continue
                try:
                    stat = jsonl.stat()
                except OSError:
                    continue
                rel = str(jsonl.relative_to(self.output_dir))
                count = self._catalog.cached_count(jsonl)
                if count is None and jsonl == self._current_jsonl:
                    count = self._live_record_index
                if count is None:
                    count = manifest_counts.get(rel)
                # Preserve exact counts for small unregistered traces without
                # making the catalog endpoint scan every large historical file.
                if count is None and stat.st_size <= 8 * 1024 * 1024:
                    try:
                        with open(jsonl, "rb") as source:
                            count = sum(1 for line in source if line.strip())
                    except OSError:
                        count = None
                yield _CaptureInfo(
                    id=f"{date_dir.name}/{m.group(1)}",
                    date=date_dir.name,
                    started_at=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
                    record_count=count,
                )

    def _manifest_record_counts(self) -> dict[str, int]:
        try:
            manifest = manifest_mod.load(self.output_dir)
        except OSError:
            return {}
        counts: dict[str, int] = {}
        for entry in manifest.get("traces", []):
            count = entry.get("record_count")
            if not isinstance(count, int):
                continue
            for filename in entry.get("files", []):
                if isinstance(filename, str) and filename.endswith(".jsonl"):
                    counts[filename] = count
        return counts

    def _current_record_count(self) -> int:
        if self._current_jsonl is None or not self._current_jsonl.is_file():
            return 0
        cached = self._catalog.cached_count(self._current_jsonl)
        if cached is not None:
            return cached
        try:
            with open(self._current_jsonl, "rb") as source:
                return sum(1 for line in source if line.strip())
        except OSError:
            return 0


class LiveSink:
    """``EventBus`` sink: forwards every record into a ``LiveViewerServer``."""

    def __init__(self, server: LiveViewerServer) -> None:
        self._server = server

    async def handle(self, record: dict) -> None:
        await self._server.broadcast(record)

    async def close(self) -> None:
        pass
