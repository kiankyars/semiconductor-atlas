"""Loopback-only inspection of an existing, transaction-pinned evidence database."""

from __future__ import annotations

import json
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import date
from http.server import BaseHTTPRequestHandler, HTTPServer
from importlib.resources import files
from urllib.parse import parse_qs, urlsplit

from .database import schema_version
from .evidence_dossier import _clock, build_evidence_dossier, json_bytes, readonly_database
from .evidence_search import search_evidence
from .models import EntityKind


FORMAT = "semiconductor-atlas-local-evidence-viewer-v1"
ASSETS = {"": ("index.html", "text/html; charset=utf-8"),
          "viewer.js": ("viewer.js", "text/javascript; charset=utf-8"),
          "viewer.css": ("viewer.css", "text/css; charset=utf-8")}
CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; "
       "base-uri 'none'; frame-ancestors 'none'; form-action 'self'; object-src 'none'")


def _cutoffs(as_of: str, recorded_at: str) -> tuple[str, str]:
    if not isinstance(as_of, str) or date.fromisoformat(as_of).isoformat() != as_of:
        raise ValueError("as_of must use YYYY-MM-DD")
    try:
        return as_of, _clock(recorded_at).isoformat().replace("+00:00", "Z")
    except OverflowError as error:
        raise ValueError("knowledge cutoff is outside the supported UTC date range") from error


class EvidenceServer(HTTPServer):
    """Serialized requests share one SQLite snapshot, including its committed WAL."""

    allow_reuse_address = False

    def __init__(self, connection, *, as_of, recorded_at, port=0):
        if type(port) is not int or not 0 <= port <= 65535:
            raise ValueError("port must be an integer between 0 and 65535")
        self.as_of, self.recorded_at = _cutoffs(as_of, recorded_at)
        self.connection = connection
        self.version = schema_version(connection)
        if self.version < 5:
            raise ValueError("evidence viewer requires schema 5 or later; no migration performed")
        self.assets = {route: (files("semiconductor_atlas").joinpath("viewer", name).read_bytes(), mime)
                       for route, (name, mime) in ASSETS.items()}
        self.prefix = "/" + secrets.token_urlsafe(32) + "/"
        super().__init__(("127.0.0.1", port), EvidenceHandler)
        self.authority = f"127.0.0.1:{self.server_port}"
        self.origin = "http://" + self.authority
        self.url = self.origin + self.prefix

    def get_request(self):
        sock, address = super().get_request()
        sock.settimeout(5)
        return sock, address


class EvidenceHandler(BaseHTTPRequestHandler):
    server_version = "AtlasLocalViewer"
    sys_version = ""

    def log_message(self, format, *args):
        # Paths contain the session capability; queries may contain private names.
        pass

    def _response(self, status, raw, mime="application/json; charset=utf-8"):
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy", CSP)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(raw)

    def send_error(self, code, message=None, explain=None):
        self._response(code, json_bytes({"error": "Unsupported or malformed request"}))

    def _allowed(self):
        server = self.server
        hosts = self.headers.get_all("Host", [])
        origins = self.headers.get_all("Origin", [])
        sites = self.headers.get_all("Sec-Fetch-Site", [])
        return (hosts == [server.authority]
                and (not origins or origins == [server.origin])
                and (not sites or sites in (["same-origin"], ["none"])))

    def do_GET(self):
        if not self._allowed():
            self._response(403, json_bytes({"error": "Same-origin loopback access required"}))
            return
        try:
            if len(self.path) > 8192 or not self.path.startswith(self.server.prefix):
                self._response(404, json_bytes({"error": "Unknown viewer route"}))
                return
            parsed = urlsplit(self.path)
            route = parsed.path[len(self.server.prefix):]
            if parsed.fragment:
                raise ValueError("fragments are not request parameters")
            params = parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True,
                              max_num_fields=8, encoding="utf-8", errors="strict")
            if any(len(values) != 1 for values in params.values()):
                raise ValueError("duplicate request parameters")
            params = {key: values[0] for key, values in params.items()}
            if route in self.server.assets:
                if params:
                    raise ValueError("assets do not accept parameters")
                raw, mime = self.server.assets[route]
                self._response(200, raw, mime)
                return
            if route == "api/config":
                if params:
                    raise ValueError("config does not accept parameters")
                result = {"format": FORMAT, "schema_version": self.server.version,
                          "as_of": self.server.as_of, "recorded_at": self.server.recorded_at,
                          "entity_kinds": [kind.value for kind in EntityKind],
                          "snapshot": "Fixed read-only database transaction; restart to see later admissions"}
            elif route in {"api/search", "api/dossier"}:
                allowed = {"as_of", "recorded_at"} | (
                    {"query", "kind", "limit", "offset"} if route == "api/search" else {"entity_id"})
                if params.keys() - allowed or not {"as_of", "recorded_at"} <= params.keys():
                    raise ValueError("both cutoffs and only supported parameters are required")
                as_of, recorded_at = _cutoffs(params["as_of"], params["recorded_at"])
                if route == "api/search":
                    result = search_evidence(self.server.connection, as_of=as_of, recorded_at=recorded_at,
                        query=params.get("query"), kind=params.get("kind"),
                        limit=int(params.get("limit", "50")), offset=int(params.get("offset", "0")))
                else:
                    if not params.get("entity_id") or len(params["entity_id"]) > 200:
                        raise ValueError("an exact entity ID of at most 200 characters is required")
                    result = build_evidence_dossier(self.server.connection,
                        subject_entity_id=params["entity_id"], as_of=as_of, recorded_at=recorded_at)
            else:
                self._response(404, json_bytes({"error": "Unknown viewer route"}))
                return
            self._response(200, json_bytes(result))
        except (ValueError, UnicodeError):
            self._response(400, json_bytes({"error": "Invalid request or evidence unavailable at these cutoffs. Check the inputs; inspect the CLI dossier for provenance diagnostics."}))
        except sqlite3.Error:
            self._response(422, json_bytes({"error": "Evidence snapshot could not be read; no partial dossier returned"}))


@contextmanager
def open_viewer(database_path, *, as_of, recorded_at, port=0):
    """Never migrate, copy, checkpoint, acquire sources or bind a network interface."""
    _cutoffs(as_of, recorded_at)
    with readonly_database(database_path) as connection:
        connection.execute("BEGIN")
        try:
            # Establish the snapshot before announcing readiness, not on the first request.
            connection.execute("SELECT count(*) FROM sqlite_schema").fetchone()
            with EvidenceServer(connection, as_of=as_of, recorded_at=recorded_at, port=port) as server:
                yield server
        finally:
            connection.rollback()


def serve_viewer(database_path, *, as_of, recorded_at, port=0):
    with open_viewer(database_path, as_of=as_of, recorded_at=recorded_at, port=port) as server:
        print(json.dumps({"format": FORMAT, "url": server.url, "as_of": server.as_of,
                          "recorded_at": server.recorded_at, "read_only": True,
                          "snapshot": "fixed for this session", "stop": "Ctrl-C"}), flush=True)
        try:
            server.serve_forever(poll_interval=0.2)
        except KeyboardInterrupt:
            pass
    return {"format": FORMAT, "status": "stopped"}
