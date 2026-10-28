# -*- coding: utf-8 -*-
# Copyright 2026 Nextbridge. License OPL-1.
"""HTTP layer: MCP Streamable HTTP endpoint + OAuth 2.1 authorisation server.

The endpoint opens its own cursor on the hub database, so it works both on a normal
single-database server and on a multi-database server where the module is loaded
server-wide (``server_wide_modules = base,rpc,web,nxb_mcp_hub``) and the database is named in
the URL: ``/mcp/db/<name>``.
"""
import json
import logging
import random
import secrets
import time

import psycopg2
from psycopg2 import errorcodes
from urllib.parse import urlencode

from werkzeug.exceptions import NotFound

from odoo import SUPERUSER_ID, api, http
from odoo.http import request

from ..core import server as mcp_server
from ..core.registry import PARAM_PREFIX
from ..models.mcp_auth import touch_row

_logger = logging.getLogger(__name__)
MODULE = "nxb_mcp_hub"
MAX_TRIES = 5
RETRY_CODES = (errorcodes.SERIALIZATION_FAILURE, errorcodes.DEADLOCK_DETECTED, errorcodes.LOCK_NOT_AVAILABLE)

CORS = [
    ("Access-Control-Allow-Origin", "*"),
    ("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS"),
    ("Access-Control-Allow-Headers", "Authorization, Content-Type, Accept, Mcp-Session-Id, "
                                     "MCP-Protocol-Version, Last-Event-ID"),
    ("Access-Control-Expose-Headers", "Mcp-Session-Id, WWW-Authenticate"),
]


def _json_response(data, status=200, headers=None):
    body = json.dumps(data, ensure_ascii=False, default=str)
    hdrs = [("Content-Type", "application/json")] + CORS + list(headers or [])
    return request.make_response(body, headers=hdrs, status=status)


def _empty(status, headers=None):
    return request.make_response("", headers=CORS + list(headers or []), status=status)


def _base_url():
    return request.httprequest.host_url.rstrip("/")


class _HubDb(object):
    """Context manager: cursor + superuser env on the hub database."""

    def __init__(self, dbname):
        self.dbname = dbname

    def __enter__(self):
        from odoo.modules.registry import Registry
        try:
            # check_signaling(): pick up cache/registry changes made by other workers (settings, packs, rules)
            self.registry = Registry(self.dbname).check_signaling()
        except Exception:  # noqa: BLE001
            raise NotFound()
        self.cr = self.registry.cursor()
        self.env = api.Environment(self.cr, SUPERUSER_ID, {})
        if "nxb.mcp.key" not in self.env:
            self.cr.close()
            raise NotFound()
        return self.env

    def __exit__(self, exc_type, exc, tb):
        try:
            if exc_type is None:
                self.cr.commit()
                self.registry.signal_changes()  # tell other workers about cache invalidations
            else:
                self.cr.rollback()
                self.registry.reset_changes()
        except Exception:
            self.registry.reset_changes()
            raise
        finally:
            self.cr.close()


def _resolve_db(path_db):
    if path_db:
        # respect the server's dbfilter / db_name: never expose a database the server is told to hide
        if path_db not in http.db_list(force=True):
            raise NotFound()
        if request.db and request.db != path_db:
            # The domain is bound to another database by dbfilter: honour the explicit name only if
            # the operator allowed it by loading the module server-wide.
            from odoo.tools import config
            if MODULE not in (config.get("server_wide_modules") or ""):
                raise NotFound()
        return path_db
    if request.db:
        return request.db
    return None


def _bearer():
    auth = (request.httprequest.headers.get("Authorization") or "").strip()
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return None


def _authenticate(env, token):
    """Return a Principal or None."""
    if not token:
        return None
    Key = env["nxb.mcp.key"]
    key = Key._authenticate(token)
    if key:
        key._touch()
        user = key.user_id
        conn_ids = key.connection_ids.ids or None
        return mcp_server.Principal(user, readonly=key.scope == "read", connection_ids=conn_ids, key=key,
                                    label=key.name)
    tok = env["nxb.mcp.oauth.token"]._authenticate(token)
    if tok:
        touch_row(tok.sudo(), "last_used")
        return mcp_server.Principal(tok.user_id, readonly=tok.scope == "read", oauth_token=tok,
                                    label=tok.client_id.name)
    return None


def _challenge(env, path_db):
    settings = mcp_server.get_settings(env)
    headers = []
    if settings["oauth_enabled"]:
        meta = "%s/.well-known/oauth-protected-resource%s" % (_base_url(), _mcp_path(path_db))
        headers.append(("WWW-Authenticate", 'Bearer resource_metadata="%s"' % meta))
    return _json_response({"jsonrpc": "2.0", "id": None, "error": {
        "code": -32001, "message": "Authentication required: add a connector key (Authorization: Bearer "
                                   "nxbm_...) or sign in with OAuth."}}, status=401, headers=headers)


def _mcp_path(path_db):
    return "/mcp/db/%s" % path_db if path_db else "/mcp"


class McpHubController(http.Controller):

    # ------------------------------------------------------------------ MCP endpoint
    @http.route(["/mcp", "/mcp/db/<string:path_db>", "/mcp/key/<string:path_key>",
                 "/mcp/db/<string:path_db>/key/<string:path_key>"],
                type="http", auth="none", csrf=False, save_session=False, readonly=False,
                methods=["GET", "POST", "DELETE", "OPTIONS"])
    def mcp_endpoint(self, path_db=None, path_key=None, **kw):
        method = request.httprequest.method
        if method == "OPTIONS":
            return _empty(204)
        dbname = _resolve_db(path_db)
        if not dbname:
            return _json_response({"error": "Several databases are hosted here. Use /mcp/db/<database name> "
                                            "(the module must be loaded server-wide), or configure dbfilter."},
                                  status=400)
        # Parallel requests of one client may touch the same rows: retry on serialization failures,
        # like Odoo does for its own routes. Each attempt runs in a fresh transaction.
        for attempt in range(1, MAX_TRIES + 1):
            try:
                return self._mcp_handle(dbname, path_db, path_key, method)
            except psycopg2.OperationalError as e:
                if e.pgcode not in RETRY_CODES or attempt == MAX_TRIES:
                    raise
                time.sleep(random.uniform(0.05, 0.3 * attempt))

    def _mcp_handle(self, dbname, path_db, path_key, method):
        with _HubDb(dbname) as env:
            principal = _authenticate(env, _bearer() or path_key)
            if not principal:
                return _challenge(env, path_db)
            session_ref = request.httprequest.headers.get("Mcp-Session-Id")
            if method == "GET":
                # No server-initiated stream: every answer comes back on the POST.
                return _empty(405, [("Allow", "POST, DELETE, OPTIONS")])
            if method == "DELETE":
                if session_ref:
                    env["nxb.mcp.session"].sudo().search([("ref", "=", session_ref)]).write({"closed": True})
                return _empty(200)
            try:
                payload = json.loads(request.httprequest.get_data(as_text=True) or "null")
            except ValueError:
                return _json_response({"jsonrpc": "2.0", "id": None, "error": {
                    "code": mcp_server.PARSE_ERROR, "message": "Parse error"}}, status=400)
            server = mcp_server.McpServer(env, principal, session_id=session_ref)
            messages = payload if isinstance(payload, list) else [payload]
            responses = []
            headers = []
            for msg in messages:
                if isinstance(msg, dict) and msg.get("method") == "initialize":
                    session_ref = secrets.token_urlsafe(24)
                    server.session_id = session_ref
                    params = msg.get("params") or {}
                    client = params.get("clientInfo") or {}
                    env["nxb.mcp.session"].sudo().create({
                        "ref": session_ref, "user_id": principal.user.id,
                        "key_id": principal.key.id if principal.key else False,
                        "client_name": (client.get("name") or "")[:100],
                        "client_version": (client.get("version") or "")[:50],
                        "protocol_version": params.get("protocolVersion")})
                    headers.append(("Mcp-Session-Id", session_ref))
                resp = server.handle(msg)
                if resp is not None:
                    responses.append(resp)
            if session_ref and not headers:
                touch_row(env["nxb.mcp.session"].sudo().search([("ref", "=", session_ref)], limit=1), "last_seen")
            if not responses:
                return _empty(202, headers)
            return _json_response(responses if isinstance(payload, list) else responses[0], headers=headers)

    @http.route(["/mcp/health", "/mcp/db/<string:path_db>/health"], type="http", auth="none", csrf=False,
                save_session=False, methods=["GET"])
    def mcp_health(self, path_db=None, **kw):
        dbname = _resolve_db(path_db)
        return _json_response({"status": "ok", "server": mcp_server.SERVER_NAME,
                               "version": mcp_server.SERVER_VERSION, "database": dbname})

    # ------------------------------------------------------------------ OAuth discovery
    @http.route(["/.well-known/oauth-protected-resource", "/.well-known/oauth-protected-resource/mcp",
                 "/.well-known/oauth-protected-resource/mcp/db/<string:path_db>"],
                type="http", auth="none", csrf=False, save_session=False, methods=["GET", "OPTIONS"])
    def oauth_resource_metadata(self, path_db=None, **kw):
        base = _base_url()
        return _json_response({
            "resource": base + _mcp_path(path_db),
            "authorization_servers": [base + ("/mcp/db/%s" % path_db if path_db else "")],
            "bearer_methods_supported": ["header"],
            "scopes_supported": ["mcp:read", "mcp:write"],
            "resource_name": "Odoo MCP Hub",
        })

    @http.route(["/.well-known/oauth-authorization-server",
                 "/.well-known/oauth-authorization-server/mcp/db/<string:path_db>",
                 "/mcp/db/<string:path_db>/.well-known/oauth-authorization-server"],
                type="http", auth="none", csrf=False, save_session=False, methods=["GET", "OPTIONS"])
    def oauth_server_metadata(self, path_db=None, **kw):
        base = _base_url()
        prefix = base + ("/mcp/db/%s" % path_db if path_db else "/mcp")
        return _json_response({
            "issuer": base + ("/mcp/db/%s" % path_db if path_db else ""),
            "authorization_endpoint": prefix + "/oauth/authorize",
            "token_endpoint": prefix + "/oauth/token",
            "registration_endpoint": prefix + "/oauth/register",
            "revocation_endpoint": prefix + "/oauth/revoke",
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "code_challenge_methods_supported": ["S256"],
            "token_endpoint_auth_methods_supported": ["none"],
            "scopes_supported": ["mcp:read", "mcp:write"],
        })

    # ------------------------------------------------------------------ OAuth endpoints
    @http.route(["/mcp/oauth/register", "/mcp/db/<string:path_db>/oauth/register"], type="http", auth="none",
                csrf=False, save_session=False, readonly=False, methods=["POST", "OPTIONS"])
    def oauth_register(self, path_db=None, **kw):
        if request.httprequest.method == "OPTIONS":
            return _empty(204)
        dbname = _resolve_db(path_db)
        if not dbname:
            raise NotFound()
        try:
            data = json.loads(request.httprequest.get_data(as_text=True) or "{}")
        except ValueError:
            return _json_response({"error": "invalid_client_metadata"}, status=400)
        uris = [u for u in (data.get("redirect_uris") or []) if isinstance(u, str) and u.startswith(
            ("https://", "http://localhost", "http://127.0.0.1"))]
        if not uris:
            return _json_response({"error": "invalid_redirect_uri",
                                   "error_description": "redirect_uris must be https or localhost"}, status=400)
        with _HubDb(dbname) as env:
            if not mcp_server.get_settings(env)["oauth_enabled"] or env["ir.config_parameter"].sudo().get_param(
                    PARAM_PREFIX + "oauth_dcr") == "False":
                return _json_response({"error": "access_denied",
                                       "error_description": "Dynamic client registration is disabled."},
                                      status=403)
            client_id = "nxbc_" + secrets.token_urlsafe(18)
            env["nxb.mcp.oauth.client"].sudo().create({
                "name": (data.get("client_name") or "AI assistant")[:100], "client_id": client_id,
                "redirect_uris": "\n".join(uris)})
        return _json_response({"client_id": client_id, "client_name": data.get("client_name") or "AI assistant",
                               "redirect_uris": uris, "grant_types": ["authorization_code", "refresh_token"],
                               "response_types": ["code"], "token_endpoint_auth_method": "none"}, status=201)

    @http.route(["/mcp/oauth/authorize", "/mcp/db/<string:path_db>/oauth/authorize"], type="http", auth="user",
                methods=["GET", "POST"], csrf=True, sitemap=False)
    def oauth_authorize(self, path_db=None, **kw):
        env = request.env
        params = request.params
        client = env["nxb.mcp.oauth.client"].sudo().search([("client_id", "=", params.get("client_id")),
                                                            ("active", "=", True)], limit=1)
        redirect_uri = params.get("redirect_uri") or ""
        if not client or not client._redirect_allowed(redirect_uri):
            return request.render("nxb_mcp_hub.oauth_error", {"message": "Unknown application or redirect address."})
        if params.get("response_type", "code") != "code" or not params.get("code_challenge") or \
                params.get("code_challenge_method", "S256") != "S256":
            return request.render("nxb_mcp_hub.oauth_error", {"message": "PKCE (S256) is required."})
        if not mcp_server.get_settings(env)["oauth_enabled"]:
            return request.render("nxb_mcp_hub.oauth_error", {"message": "OAuth is disabled by the administrator."})
        state = params.get("state") or ""
        if request.httprequest.method == "POST":
            if params.get("decision") != "allow":
                return request.redirect(_with_query(redirect_uri, {"error": "access_denied", "state": state}),
                                        local=False)
            scope = "read" if params.get("scope_choice") == "read" else "write"
            code = env["nxb.mcp.oauth.code"]._issue(client, env.user, redirect_uri, params["code_challenge"], scope)
            return request.redirect(_with_query(redirect_uri, {"code": code, "state": state}), local=False)
        return request.render("nxb_mcp_hub.oauth_consent", {
            "client": client, "user": env.user, "params": params, "dbname": env.cr.dbname,
            "readonly_default": "read" in (params.get("scope") or "") and "write" not in (params.get("scope") or ""),
        })

    @http.route(["/mcp/oauth/token", "/mcp/db/<string:path_db>/oauth/token"], type="http", auth="none",
                csrf=False, save_session=False, readonly=False, methods=["POST", "OPTIONS"])
    def oauth_token(self, path_db=None, **kw):
        if request.httprequest.method == "OPTIONS":
            return _empty(204)
        dbname = _resolve_db(path_db)
        if not dbname:
            raise NotFound()
        p = request.params
        with _HubDb(dbname) as env:
            client = env["nxb.mcp.oauth.client"].sudo().search([("client_id", "=", p.get("client_id")),
                                                                ("active", "=", True)], limit=1)
            if not client:
                return _json_response({"error": "invalid_client"}, status=401)
            grant = p.get("grant_type")
            if grant == "authorization_code":
                code = env["nxb.mcp.oauth.code"]._redeem(p.get("code"), client, p.get("redirect_uri"),
                                                         p.get("code_verifier"))
                if not code:
                    return _json_response({"error": "invalid_grant"}, status=400)
                return _json_response(env["nxb.mcp.oauth.token"]._issue(client, code.user_id, code.scope),
                                      headers=[("Cache-Control", "no-store")])
            if grant == "refresh_token":
                tokens = env["nxb.mcp.oauth.token"]._refresh(p.get("refresh_token"), client)
                if not tokens:
                    return _json_response({"error": "invalid_grant"}, status=400)
                return _json_response(tokens, headers=[("Cache-Control", "no-store")])
            return _json_response({"error": "unsupported_grant_type"}, status=400)

    @http.route(["/mcp/oauth/revoke", "/mcp/db/<string:path_db>/oauth/revoke"], type="http", auth="none",
                csrf=False, save_session=False, readonly=False, methods=["POST"])
    def oauth_revoke(self, path_db=None, **kw):
        dbname = _resolve_db(path_db)
        if not dbname:
            raise NotFound()
        token = request.params.get("token")
        with _HubDb(dbname) as env:
            rec = env["nxb.mcp.oauth.token"]._authenticate(token)
            if rec:
                rec.action_revoke()
        return _empty(200)


def _with_query(url, params):
    sep = "&" if "?" in url else "?"
    return url + sep + urlencode({k: v for k, v in params.items() if v})
