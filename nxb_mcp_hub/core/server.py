# -*- coding: utf-8 -*-
# Copyright 2026 Nextbridge. License OPL-1.
"""MCP message handling, independent of HTTP so it can be unit tested.

``McpServer(hub_env, principal).handle(message)`` returns the JSON-RPC response dict
(or None for notifications).
"""
import json
import logging
import time
import traceback
import urllib.request

from .context import LocalOrm, ToolContext, ToolError
from .registry import PACKS, PARAM_PREFIX, TOOLS

_logger = logging.getLogger(__name__)

SERVER_NAME = "nextbridge-odoo-mcp-hub"
SERVER_VERSION = "19.0.1.0.0"
PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
MAX_RESULT_CHARS = 120000

# JSON-RPC error codes
PARSE_ERROR, INVALID_REQUEST, METHOD_NOT_FOUND, INVALID_PARAMS, INTERNAL_ERROR = \
    -32700, -32600, -32601, -32602, -32603


def endpoint_url(env):
    """Public MCP address of this database: /mcp, or /mcp/db/<name> when the server hosts several databases."""
    base = env["ir.config_parameter"].sudo().get_param("web.base.url", "").rstrip("/")
    try:
        from odoo import http
        dbs = http.db_list(force=True)
    except Exception:  # noqa: BLE001
        dbs = [env.cr.dbname]
    if len(dbs) > 1:
        return "%s/mcp/db/%s" % (base, env.cr.dbname)
    return base + "/mcp"


def get_settings(env):
    icp = env["ir.config_parameter"].sudo()

    def flag(key, default):
        val = icp.get_param(PARAM_PREFIX + key)
        if val in (None, False, ""):
            return default
        return str(val).lower() in ("1", "true", "yes")

    def num(key, default):
        try:
            return int(icp.get_param(PARAM_PREFIX + key) or default)
        except ValueError:
            return default

    return {
        "strict_readonly": flag("strict_readonly", False),
        "allow_delete": flag("allow_delete", True),
        "max_records": num("max_records", 500),
        "rate_limit": num("rate_limit", 120),
        "log_arguments": flag("log_arguments", True),
        "oauth_enabled": flag("oauth_enabled", True),
    }


def pack_enabled(env, key):
    pack = PACKS[key]
    val = env["ir.config_parameter"].sudo().get_param(PARAM_PREFIX + "pack." + key)
    on = pack.default if val in (None, False, "") else str(val).lower() in ("1", "true")
    if on and pack.parent:
        on = pack_enabled(env, pack.parent)
    return on


class Principal(object):
    """Who is calling: an Odoo user of the hub database plus the limits of the credential used."""

    def __init__(self, user, readonly=False, connection_ids=None, key=None, oauth_token=None, label=""):
        self.user = user
        self.readonly = readonly
        self.connection_ids = connection_ids  # None = all connections the user may use
        self.key = key
        self.oauth_token = oauth_token
        self.label = label

    @property
    def is_admin(self):
        return self.user.has_group("base.group_system")


class McpServer(object):

    def __init__(self, hub_env, principal, session_id=None):
        self.principal = principal
        self.hub_env = hub_env(user=principal.user.id)
        self.settings = get_settings(hub_env)
        self.session_id = session_id

    # ------------------------------------------------------------------ connections
    def connections(self):
        Conn = self.hub_env["nxb.mcp.connection"].sudo()
        domain = [("active", "=", True)]
        conns = Conn.search(domain, order="sequence, id")
        if self.principal.connection_ids is not None:
            conns = conns.filtered(lambda c: c.id in self.principal.connection_ids or c.kind == "this")
        conns = conns.filtered(lambda c: c._user_allowed(self.principal.user))
        return conns

    def default_connection(self):
        conns = self.connections()
        this = conns.filtered(lambda c: c.kind == "this")[:1]
        return this or conns[:1]

    # ------------------------------------------------------------------ tools
    def visible_tools(self):
        is_admin = self.principal.is_admin
        readonly = self.principal.readonly or self.settings["strict_readonly"]
        out = []
        for t in TOOLS.values():
            if not pack_enabled(self.hub_env, t.pack):
                continue
            if t.admin_only and not is_admin:
                continue
            if t.writes and readonly:
                continue
            if t.destructive and not self.settings["allow_delete"]:
                continue
            out.append(t)
        return out

    def instructions(self):
        conns = self.connections()
        lines = [
            "You are connected to Odoo through the Nextbridge MCP Hub.",
            "Databases available (use the 'database' argument to choose; the first is the default):",
        ]
        for c in conns:
            lines.append("- %s: %s (%s%s)" % (c.code, c.name, c._kind_label(),
                                               "" if c.kind != "remote" or c.federated else ", basic tools only"))
        lines += [
            "Before changing or deleting data, tell the user what you will do. Deleting requires confirm=true.",
            "Use the model and field discovery tools instead of guessing technical names.",
        ]
        if self.principal.readonly or self.settings["strict_readonly"]:
            lines.append("This connection is READ-ONLY: you cannot create, change or delete anything.")
        disabled = [p.title for k, p in PACKS.items() if not pack_enabled(self.hub_env, k)]
        if disabled:
            lines.append("Switched off by the administrator (do not offer these): %s." % ", ".join(disabled))
        return "\n".join(lines)

    # ------------------------------------------------------------------ JSON-RPC
    def handle(self, msg):
        if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or "method" not in msg:
            if isinstance(msg, dict) and ("result" in msg or "error" in msg):
                return None  # a response from the client; nothing to do
            return _error(msg.get("id") if isinstance(msg, dict) else None, INVALID_REQUEST, "Invalid request")
        mid = msg.get("id")
        method = msg["method"]
        params = msg.get("params") or {}
        if mid is None:  # notification
            return None
        try:
            if method == "initialize":
                return _result(mid, self._initialize(params))
            if method == "ping":
                return _result(mid, {})
            if method == "tools/list":
                dbs = [c.code for c in self.connections()]
                dbs = dbs if len(dbs) > 1 else None
                return _result(mid, {"tools": [t.definition(dbs) for t in self.visible_tools()]})
            if method == "tools/call":
                return _result(mid, self.call_tool(params.get("name"), params.get("arguments") or {}))
            if method == "resources/list":
                return _result(mid, {"resources": []})
            if method == "resources/templates/list":
                return _result(mid, {"resourceTemplates": []})
            if method == "prompts/list":
                return _result(mid, {"prompts": PROMPTS_LIST})
            if method == "prompts/get":
                return _result(mid, _get_prompt(params))
            if method == "logging/setLevel":
                return _result(mid, {})
            if method == "completion/complete":
                return _result(mid, {"completion": {"values": [], "hasMore": False}})
            return _error(mid, METHOD_NOT_FOUND, "Method not found: %s" % method)
        except Exception as e:  # noqa: BLE001
            _logger.exception("MCP method %s failed", method)
            return _error(mid, INTERNAL_ERROR, str(e))

    def _initialize(self, params):
        asked = params.get("protocolVersion")
        version = asked if asked in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0]
        return {
            "protocolVersion": version,
            "capabilities": {"tools": {"listChanged": False}, "prompts": {"listChanged": False},
                             "resources": {}, "logging": {}},
            "serverInfo": {"name": SERVER_NAME, "title": "Odoo MCP Hub by Nextbridge",
                           "version": SERVER_VERSION},
            "instructions": self.instructions(),
        }

    # ------------------------------------------------------------------ tools/call
    def call_tool(self, name, arguments):
        started = time.time()
        arguments = dict(arguments or {})
        db_code = None
        conn = None
        status, error, result = "ok", None, None
        try:
            self._check_rate_limit()
            tool = TOOLS.get(name)
            if not tool:
                raise ToolError("Unknown tool %r." % name)
            if tool.pack != "hub":
                db_code = arguments.pop("database", None)
            if tool not in self.visible_tools():
                raise ToolError(self._refusal(tool))
            conn = self._resolve_connection(db_code)
            if tool.destructive and arguments.get("confirm") is not True:
                raise ToolError("This action deletes data. Describe exactly what will be removed to the user, "
                                "then call again with confirm=true.")
            result = self._run(tool, conn, arguments)
        except ToolError as e:
            status, error = "refused" if "switched off" in str(e) or "not allowed" in str(e) else "error", str(e)
        except Exception as e:  # noqa: BLE001
            status, error = "error", _clean_exception(e)
            _logger.warning("MCP tool %s failed: %s", name, traceback.format_exc())
        self._log(name, conn, arguments, status, error, started, db_code)
        if error:
            return {"content": [{"type": "text", "text": error}], "isError": True}
        return _tool_result(result)

    def _refusal(self, tool):
        pack = PACKS[tool.pack]
        if not pack_enabled(self.hub_env, tool.pack):
            return ("The '%s' feature is switched off by the Odoo administrator, so %s is not available. "
                    "Tell the user it can be enabled in AI Connector → Tool packs." % (pack.title, tool.name))
        if tool.admin_only and not self.principal.is_admin:
            return "%s is only allowed for Odoo administrators." % tool.name
        if tool.writes:
            return ("This connection is read-only, so %s is not allowed. Changes must be made by a user with "
                    "a read-write key." % tool.name)
        return "Deleting records is disabled by the administrator, so %s is not allowed." % tool.name

    def _check_rate_limit(self):
        limit = self.settings["rate_limit"]
        if not limit:
            return
        Log = self.hub_env["nxb.mcp.log"].sudo()
        dom = [("create_date", ">=", _ago(60))]
        if self.principal.key:
            dom.append(("key_id", "=", self.principal.key.id))
        elif self.principal.oauth_token:
            dom.append(("oauth_token_id", "=", self.principal.oauth_token.id))
        else:
            dom.append(("user_id", "=", self.principal.user.id))
        if Log.search_count(dom) >= limit:
            raise ToolError("Rate limit reached (%d calls per minute). Wait a moment and try again." % limit)

    def _resolve_connection(self, code):
        conns = self.connections()
        if not code:
            conn = self.default_connection()
            if not conn:
                raise ToolError("No database is available to this key.")
            return conn
        conn = conns.filtered(lambda c: c.code == code)[:1]
        if not conn:
            raise ToolError("Database %r is not available. Available: %s." % (
                code, ", ".join(conns.mapped("code")) or "none"))
        return conn

    def _run(self, tool, conn, arguments):
        pack = PACKS[tool.pack]
        if conn.kind == "this":
            env = self.hub_env
            ctx = ToolContext(LocalOrm(env), env, self.hub_env, conn._describe(), self.principal, self.settings)
            ctx.server = self
            if pack.requires:
                ctx.require_module(pack.requires, pack.title)
            with env.cr.savepoint():
                result = tool.func(ctx, **arguments)
                env.flush_all()
            return result
        if conn.kind == "local":
            return conn._run_local(self, tool, arguments)
        # remote
        if conn.federated:
            return conn._run_federated(tool.name, arguments)
        if not tool.remote_ok:
            raise ToolError("Database %r is on another Odoo server without the AI Connector app, so only basic "
                            "record tools work there. Install the app on that server and add its connector URL "
                            "to the connection to unlock %s." % (conn.code, tool.name))
        orm = conn._remote_orm()
        ctx = ToolContext(orm, None, self.hub_env, conn._describe(), self.principal, self.settings)
        ctx.server = self
        if pack.requires and not orm.installed(pack.requires):
            raise ToolError("The %s app is not installed in database %r." % (pack.title, conn.code))
        return tool.func(ctx, **arguments)

    def _log(self, name, conn, arguments, status, error, started, db_code):
        try:
            args_text = ""
            if self.settings["log_arguments"]:
                args_text = json.dumps(_redact(arguments), ensure_ascii=False, default=str)[:4000]
            self.hub_env["nxb.mcp.log"].sudo().create({
                "tool": name or "?",
                "user_id": self.principal.user.id,
                "key_id": self.principal.key.id if self.principal.key else False,
                "oauth_token_id": self.principal.oauth_token.id if self.principal.oauth_token else False,
                "connection_id": conn.id if conn else False,
                "database_code": (conn.code if conn else db_code) or False,
                "status": status,
                "error": (error or "")[:2000] or False,
                "arguments": args_text or False,
                "duration_ms": int((time.time() - started) * 1000),
                "session_ref": self.session_id or False,
            })
        except Exception:  # noqa: BLE001
            _logger.exception("Could not write MCP log")


def _redact(args):
    out = {}
    for k, v in (args or {}).items():
        if any(s in k.lower() for s in ("password", "secret", "token", "api_key", "apikey")):
            out[k] = "***"
        else:
            out[k] = v
    return out


def _ago(seconds):
    from datetime import datetime, timedelta
    return datetime.utcnow() - timedelta(seconds=seconds)


def _clean_exception(e):
    """User-facing message for Odoo exceptions (access errors, validation errors, ...)."""
    msg = getattr(e, "args", None) and e.args[0] or str(e)
    if not isinstance(msg, str):
        msg = str(msg)
    kind = type(e).__name__
    if kind in ("AccessError", "UserError", "ValidationError", "MissingError", "AccessDenied"):
        return "%s: %s" % (kind.replace("Error", " error").strip(), msg)
    if kind in ("ValueError", "KeyError", "TypeError"):
        return "Invalid request: %s" % msg
    return "%s: %s" % (kind, msg)


def _tool_result(result):
    if isinstance(result, str):
        text = result
    else:
        text = json.dumps(result, ensure_ascii=False, default=str, indent=1)
    if len(text) > MAX_RESULT_CHARS:
        text = text[:MAX_RESULT_CHARS] + "\n…(result truncated; ask for fewer records or fields)"
    out = {"content": [{"type": "text", "text": text}], "isError": False}
    if isinstance(result, dict) and len(text) <= MAX_RESULT_CHARS:
        out["structuredContent"] = json.loads(json.dumps(result, default=str))
    return out


def _result(mid, result):
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def _error(mid, code, message):
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}


def forward_mcp(url, key, name, arguments, timeout=60):
    """Call a tool on another server running this app (federation)."""
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                       "params": {"name": name, "arguments": arguments}}).encode()
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Content-Type": "application/json", "Accept": "application/json, text/event-stream",
        "Authorization": "Bearer %s" % key, "MCP-Protocol-Version": PROTOCOL_VERSIONS[0]})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode() or "{}")
    except Exception as e:  # noqa: BLE001
        raise ToolError("Could not reach the remote MCP endpoint %s: %s" % (url, e))
    if "error" in data:
        raise ToolError("Remote server error: %s" % data["error"].get("message"))
    res = data.get("result") or {}
    text = "\n".join(c.get("text", "") for c in res.get("content", []) if c.get("type") == "text")
    if res.get("isError"):
        raise ToolError("Remote: %s" % text)
    return res.get("structuredContent") or _maybe_json(text)


def _maybe_json(text):
    try:
        return json.loads(text)
    except ValueError:
        return text


PROMPTS_LIST = [
    {"name": "business_overview", "title": "Business overview",
     "description": "Summarise this month's sales, open invoices, pipeline and stock alerts.",
     "arguments": [{"name": "database", "description": "Database code (optional)", "required": False}]},
    {"name": "build_dashboard", "title": "Build a dashboard",
     "description": "Design and create an Odoo dashboard for a team or topic.",
     "arguments": [{"name": "topic", "description": "e.g. 'sales team performance'", "required": True}]},
    {"name": "compare_databases", "title": "Compare databases",
     "description": "Compare the same figures across all connected databases.",
     "arguments": [{"name": "question", "description": "e.g. 'revenue this quarter'", "required": True}]},
]


def _get_prompt(params):
    name = params.get("name")
    args = params.get("arguments") or {}
    if name == "business_overview":
        db = args.get("database")
        text = ("Give me a short business overview%s: this month's confirmed sales, unpaid customer invoices, "
                "the CRM pipeline by stage and products below their reorder level. Use the discovery tools to "
                "check which apps are installed first, and skip what is not installed.") % (
            " for database %s" % db if db else "")
    elif name == "build_dashboard":
        text = ("Design an Odoo dashboard about: %s. First read the dashboard guide tool, check which models "
                "and fields exist, then create the dashboard with 4-8 useful widgets and tell me how to open "
                "it.") % args.get("topic", "")
    elif name == "compare_databases":
        text = ("Answer this across every connected database and present a comparison table: %s. List the "
                "databases first, then query each one.") % args.get("question", "")
    else:
        raise ValueError("Unknown prompt %r" % name)
    return {"description": name, "messages": [{"role": "user", "content": {"type": "text", "text": text}}]}
