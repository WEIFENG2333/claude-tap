"""Explicit local JSON responses for capture-only client initialization."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit


@dataclass(frozen=True)
class BootstrapResponses:
    document: dict
    sha256: str

    @classmethod
    def load(cls, path: Path) -> BootstrapResponses:
        document = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(document, dict) or set(document) != {"version", "responses"}:
            raise ValueError("bootstrap responses require version and responses")
        if (
            type(document["version"]) is not int
            or document["version"] != 1
            or not isinstance(document["responses"], list)
        ):
            raise ValueError("bootstrap responses require version 1 and a response list")
        seen = set()
        for route in document["responses"]:
            if not isinstance(route, dict) or set(route) != {"method", "url", "status", "body"}:
                raise ValueError("each bootstrap response requires method, url, status and body")
            method, url, status = route["method"], route["url"], route["status"]
            if not isinstance(method, str) or method not in {
                "GET",
                "POST",
                "PUT",
                "PATCH",
                "DELETE",
                "HEAD",
                "OPTIONS",
            }:
                raise ValueError("invalid bootstrap HTTP method")
            if not isinstance(url, str):
                raise ValueError("bootstrap URL must be a string")
            parsed = urlsplit(url)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username is not None
                or parsed.password is not None
                or parsed.query
                or parsed.fragment
                or not parsed.path.startswith("/")
            ):
                raise ValueError("bootstrap URL must be absolute, without credentials, query or fragment")
            if type(status) is not int or not 200 <= status <= 599 or 300 <= status < 400:
                raise ValueError("bootstrap status must be 200-299 or 400-599")
            key = (method, url)
            if key in seen:
                raise ValueError("duplicate bootstrap route")
            seen.add(key)
        canonical = json.dumps(document, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        return cls(document, hashlib.sha256(canonical).hexdigest())

    def response(self, method: str, url: str) -> tuple[int, object]:
        """Match an exact origin/path, ignoring the query; never forward a miss."""
        parsed = urlsplit(url)
        key = parsed._replace(query="", fragment="").geturl()
        for route in self.document["responses"]:
            if route["method"] == method and route["url"] == key:
                return route["status"], route["body"]
        return 404, {"error": "No local bootstrap response configured"}

    def provenance(self) -> dict:
        return {"kind": "mocked-bootstrap", "sha256": self.sha256, "configuration": self.document}
