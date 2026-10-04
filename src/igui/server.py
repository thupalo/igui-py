"""Local HTTP API (replaces the Nancy module Nancy.vb).

Security model (the API drives SAP with the user's authorisations):

* listens on loopback only; the client address and the Host header are checked
  (blocks remote callers and DNS-rebinding attacks);
* action endpoints (/go, /ctx) accept POST/PUT; GET only in legacy mode;
* browser requests are accepted only from ``security.allowed_origins``
  (Origin / Referer / Sec-Fetch-Site), CORS is answered only for those origins;
* optional shared-secret token (constant-time comparison);
* body size, command count and every command value are validated before
  anything reaches SAP; the SAP queue is bounded (503 when full);
* responses carry no-store / nosniff / CSP headers, no wildcard CORS.
"""

from __future__ import annotations

import hmac
import ipaddress
import json
import logging
import threading
from typing import Callable, Protocol
from urllib.parse import parse_qsl, urlsplit

from flask import Flask, Response, request

from . import APP_NAME, AUTHOR, __version__
from .commands import Command, CommandError, parse
from .config import Config, SecurityConfig
from .macros import MacroError, MacroSet

log = logging.getLogger(__name__)

TOKEN_HEADER = "X-iGui-Token"
ACTION_METHODS = ("GET", "POST", "PUT", "OPTIONS")


class CommandSink(Protocol):
    def run_commands(self, commands: list[Command], source: str = "") -> bool: ...


class Rejected(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def _origin_of(url: str) -> str | None:
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    if not parts.scheme or not parts.hostname:
        return None
    host = f"[{parts.hostname}]" if ":" in parts.hostname else parts.hostname
    default_port = 443 if parts.scheme == "https" else 80
    port = f":{parts.port}" if parts.port and parts.port != default_port else ""
    return f"{parts.scheme}://{host}{port}".lower()


class Guard:
    def __init__(self, port: int, security: SecurityConfig) -> None:
        self.port = port
        self.sec = security
        self.allowed_hosts = {f"{h}:{port}" for h in ("localhost", "127.0.0.1", "[::1]")}
        self.own_origins = {f"http://{h}" for h in self.allowed_hosts}

    def check_transport(self) -> None:
        """Every endpoint: loopback client + expected Host header."""
        try:
            loopback = ipaddress.ip_address((request.remote_addr or "").split("%")[0]).is_loopback
        except ValueError:
            loopback = False
        if not loopback:
            raise Rejected(403, "only local clients are allowed")
        if (request.host or "").lower() not in self.allowed_hosts:
            raise Rejected(403, "invalid Host header")

    def cors_origin(self) -> str | None:
        origin = request.headers.get("Origin")
        if origin and origin.lower() in self.sec.allowed_origins:
            return origin
        return None

    def check_browser_origin(self) -> None:
        """CSRF protection: a browser request must come from an allowed origin."""
        origin = request.headers.get("Origin")
        fetch_site = request.headers.get("Sec-Fetch-Site", "").lower()
        if origin is not None:
            norm = origin.lower()
            if norm in self.sec.allowed_origins or norm in self.own_origins:
                return
            raise Rejected(403, "origin not allowed")
        if fetch_site in ("", "none", "same-origin"):
            referer = request.headers.get("Referer")
            if not referer or fetch_site:
                return  # native client, or a URL typed by the user
            ref_origin = _origin_of(referer)
            if ref_origin in self.sec.allowed_origins or ref_origin in self.own_origins:
                return
            raise Rejected(403, "referer not allowed")
        # cross-site / same-site navigation without Origin: decide by Referer
        ref_origin = _origin_of(request.headers.get("Referer", ""))
        if ref_origin and ref_origin in self.sec.allowed_origins:
            return
        raise Rejected(403, "cross-site request not allowed")

    def check_token(self, form_pairs: list[tuple[str, str]], query_pairs: list[tuple[str, str]]) -> None:
        expected = self.sec.api_token
        if not expected:
            return
        supplied = request.headers.get(TOKEN_HEADER, "")
        auth = request.headers.get("Authorization", "")
        if not supplied and auth.lower().startswith("bearer "):
            supplied = auth[7:].strip()
        if not supplied:
            supplied = next((v for k, v in form_pairs if k == "_token"), "")
        if not supplied and self.sec.allow_get_actions:
            supplied = next((v for k, v in query_pairs if k == "_token"), "")
        if not hmac.compare_digest(supplied.encode(), expected.encode()):
            raise Rejected(401, "missing or invalid token")

    def check_method(self) -> None:
        if request.method == "GET" and not self.sec.allow_get_actions:
            raise Rejected(405, "use POST (GET is disabled, see security.allow_get_actions)")


def _request_pairs(max_body: int) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """(body pairs, query pairs) preserving order and repeated keys."""
    query = parse_qsl(request.query_string.decode("utf-8", "replace"), keep_blank_values=True)
    if request.content_length is not None and request.content_length > max_body:
        raise Rejected(413, "request body too large")
    raw = request.get_data(cache=True)
    if len(raw) > max_body:
        raise Rejected(413, "request body too large")
    if not raw:
        return [], query
    ctype = (request.mimetype or "").lower()
    text = raw.decode("utf-8", "replace")
    if ctype == "application/json":
        try:
            data = json.loads(text)
        except ValueError:
            raise Rejected(400, "invalid JSON") from None
        if isinstance(data, dict):
            data = data.get("commands", data)
        if isinstance(data, dict):
            return [(str(k), str(v)) for k, v in data.items()], query
        if isinstance(data, list) and all(isinstance(p, list) and len(p) in (1, 2) for p in data):
            return [(str(p[0]), str(p[1]) if len(p) == 2 else "") for p in data], query
        raise Rejected(400, "JSON body must be an object or a list of [key, value] pairs")
    if ctype in ("application/x-www-form-urlencoded", "text/plain", ""):
        return parse_qsl(text, keep_blank_values=True), query
    if ctype == "multipart/form-data":
        return [(k, v) for k, v in request.form.items(multi=True)], query
    raise Rejected(415, f"unsupported content type {ctype}")


def _json(payload: dict, status: int = 200) -> Response:
    return Response(json.dumps(payload, ensure_ascii=False), status=status, mimetype="application/json")


def create_app(cfg: Config, sink: CommandSink, macros: Callable[[], MacroSet], help_text: str = "") -> Flask:
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = cfg.security.max_body_bytes
    guard = Guard(cfg.port, cfg.security)

    @app.before_request
    def _transport() -> Response | None:
        try:
            guard.check_transport()
        except Rejected as rej:
            log.warning("Rejected %s %s from %s: %s", request.method, request.path, request.remote_addr, rej.message)
            return _json({"error": rej.message}, rej.status)
        return None

    @app.after_request
    def _headers(resp: Response) -> Response:
        resp.headers["Cache-Control"] = "no-store, max-age=0"
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers["Cross-Origin-Resource-Policy"] = "same-origin"
        origin = guard.cors_origin()
        if origin:
            resp.headers["Access-Control-Allow-Origin"] = origin
            resp.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, OPTIONS"
            resp.headers["Access-Control-Allow-Headers"] = f"Content-Type, Authorization, {TOKEN_HEADER}"
            resp.headers["Access-Control-Max-Age"] = "600"
            if request.headers.get("Access-Control-Request-Private-Network") == "true":
                resp.headers["Access-Control-Allow-Private-Network"] = "true"
            resp.headers["Cross-Origin-Resource-Policy"] = "cross-origin"
        resp.headers.add("Vary", "Origin")
        return resp

    def preflight() -> Response:
        if guard.cors_origin() is None:
            return _json({"error": "origin not allowed"}, 403)
        return Response(status=204)

    def run_action(build: Callable[[list[tuple[str, str]]], list[tuple[str, str]]]) -> Response:
        if request.method == "OPTIONS":
            return preflight()
        try:
            guard.check_method()
            guard.check_browser_origin()
            body, query = _request_pairs(cfg.security.max_body_bytes)
            guard.check_token(body, query)
            pairs = body if request.method in ("POST", "PUT") else query
            if request.method in ("POST", "PUT") and not body:
                pairs = query  # POST /go?cmd... with empty body (iGUI 2.x style)
            commands = parse(build(pairs), cfg.security)
        except Rejected as rej:
            log.warning("Rejected %s %s (origin=%s): %s", request.method, request.path,
                        request.headers.get("Origin"), rej.message)
            return _json({"error": rej.message}, rej.status)
        except (CommandError, MacroError) as exc:
            log.info("Invalid request %s: %s", request.path, exc)
            return _json({"error": str(exc)}, 400)
        source = request.headers.get("Origin") or request.headers.get("User-Agent", "")[:60]
        if not sink.run_commands(commands, source):
            return _json({"error": "busy, try again later"}, 503)
        return _json({"status": "queued", "commands": len(commands)}, 202)

    @app.get("/")
    def index() -> Response:
        return Response(APP_NAME, mimetype="text/plain")

    @app.get("/version")
    def version() -> Response:
        return Response(f"{APP_NAME} v{__version__} by {AUTHOR}", mimetype="text/plain")

    @app.get("/help")
    def help_page() -> Response:
        return Response(help_text, mimetype="text/plain")

    @app.get("/get")
    def echo() -> Response:  # diagnostic: shows how parameters are received (JSON, not HTML)
        pairs = parse_qsl(request.query_string.decode("utf-8", "replace"), keep_blank_values=True)
        return _json({"params": pairs})

    @app.get("/iGUI/context.json")
    def context_json() -> Response:
        return Response(macros().to_json(), mimetype="application/json")

    @app.route("/go", methods=ACTION_METHODS)
    def go() -> Response:
        return run_action(lambda pairs: pairs)

    @app.route("/ctx/<macro_id>", methods=ACTION_METHODS)
    def ctx(macro_id: str) -> Response:
        def build(pairs: list[tuple[str, str]]) -> list[tuple[str, str]]:
            macro = macros().get(macro_id)
            if macro is None:
                raise Rejected(404, f"unknown macro {macro_id}")
            return macro.render(dict(pairs))
        return run_action(build)

    @app.errorhandler(404)
    def not_found(_exc) -> Response:
        return _json({"error": "not found"}, 404)

    @app.errorhandler(405)
    def not_allowed(_exc) -> Response:
        return _json({"error": "method not allowed"}, 405)

    @app.errorhandler(413)
    def too_large(_exc) -> Response:
        return _json({"error": "request body too large"}, 413)

    return app


class ApiServer:
    """waitress server on loopback (IPv4 + IPv6 when available) in a background thread."""

    def __init__(self, app: Flask, port: int) -> None:
        self.app = app
        self.port = port
        self._server = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        from waitress.server import create_server

        common = dict(threads=4, ident=APP_NAME, connection_limit=50, channel_timeout=30,
                      max_request_body_size=1024 * 1024, clear_untrusted_proxy_headers=True)
        try:
            self._server = create_server(self.app, listen=f"127.0.0.1:{self.port} [::1]:{self.port}", **common)
        except OSError as exc:
            log.info("IPv6 loopback unavailable (%s), listening on 127.0.0.1 only", exc)
            self._server = create_server(self.app, listen=f"127.0.0.1:{self.port}", **common)
        self._thread = threading.Thread(target=self._server.run, name="http-server", daemon=True)
        self._thread.start()
        log.info("API listening on http://localhost:%d", self.port)

    def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            self._server = None
