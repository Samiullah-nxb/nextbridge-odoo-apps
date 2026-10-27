# -*- coding: utf-8 -*-
# Copyright 2026 Nextbridge. License OPL-1.
from odoo import _, api, models
from odoo.exceptions import AccessError

from ..core.registry import GROUP_TITLES, PACKS, PARAM_PREFIX, TOOLS
from ..core import server as mcp_server
from ..core.server import pack_enabled


class McpPackBoard(models.AbstractModel):
    _name = "nxb.mcp.pack.board"
    _description = "AI Connector: tool pack switchboard"

    @api.model
    def get_board(self):
        Module = self.env["ir.module.module"].sudo()
        installed = set(Module.search([("state", "=", "installed")]).mapped("name"))
        total_on = tokens_on = tokens_all = 0
        groups = []
        for gkey, gtitle in GROUP_TITLES.items():
            cards = []
            for key, pack in PACKS.items():
                if pack.group != gkey:
                    continue
                tools = [t for t in TOOLS.values() if t.pack == key]
                tok = sum(t.token_estimate() for t in tools)
                on = pack_enabled(self.env, key)
                self_on = self._raw(key)
                tokens_all += tok
                if on:
                    total_on += len(tools)
                    tokens_on += tok
                cards.append({
                    "key": key, "title": pack.title, "summary": pack.summary, "on": on, "switch": self_on,
                    "risk": pack.risk, "parent": pack.parent and PACKS[pack.parent].title,
                    "parent_off": bool(pack.parent and not pack_enabled(self.env, pack.parent)),
                    "requires": pack.requires, "app_missing": bool(pack.requires and pack.requires not in installed),
                    "tokens": tok,
                    "tools": [{"name": t.name, "title": t.title, "writes": t.writes, "tokens": t.token_estimate(),
                               "admin_only": t.admin_only, "description": t.description[:220]} for t in tools],
                })
            groups.append({"key": gkey, "title": gtitle, "cards": cards})
        return {"groups": groups, "tools_on": total_on, "tokens_on": tokens_on, "tokens_all": tokens_all,
                "tokens_saved": tokens_all - tokens_on, "can_edit": self.env.user.has_group("base.group_system")}

    def _raw(self, key):
        val = self.env["ir.config_parameter"].sudo().get_param(PARAM_PREFIX + "pack." + key)
        return PACKS[key].default if val in (None, False, "") else str(val).lower() in ("1", "true")

    @api.model
    def set_pack(self, key, on):
        if not self.env.user.has_group("base.group_system"):
            raise AccessError(_("Only administrators can switch tool packs."))
        if key not in PACKS:
            raise ValueError(key)
        self.env["ir.config_parameter"].sudo().set_param(PARAM_PREFIX + "pack." + key, "True" if on else "False")
        return self.get_board()


class McpOverview(models.AbstractModel):
    _name = "nxb.mcp.overview"
    _description = "AI Connector: overview data"

    @api.model
    def get_overview(self):
        from datetime import timedelta
        from odoo import fields
        from odoo.tools import config
        env = self.env
        base = env["ir.config_parameter"].sudo().get_param("web.base.url", "").rstrip("/")
        is_admin = env.user.has_group("nxb_mcp_hub.group_mcp_admin")
        Log = env["nxb.mcp.log"]
        since = fields.Datetime.now() - timedelta(days=1)
        week = fields.Datetime.now() - timedelta(days=7)
        server_wide = "nxb_mcp_hub" in (config.get("server_wide_modules") or "")
        conns = env["nxb.mcp.connection"].sudo().search([("active", "=", True)])
        recent = Log.search([], limit=8)
        per_day = []
        for i in range(6, -1, -1):
            start = fields.Datetime.now().replace(hour=0, minute=0, second=0) - timedelta(days=i)
            end = start + timedelta(days=1)
            per_day.append({"day": start.strftime("%a"), "ok": Log.search_count([("create_date", ">=", start), (
                "create_date", "<", end), ("status", "=", "ok")]), "bad": Log.search_count([
                    ("create_date", ">=", start), ("create_date", "<", end), ("status", "!=", "ok")])})
        top = Log._read_group([("create_date", ">=", week)], ["tool"], ["__count"], order="__count desc", limit=6)
        return {
            "endpoint": mcp_server.endpoint_url(env),
            "endpoint_db": base + "/mcp/db/" + env.cr.dbname,
            "server_wide": server_wide,
            "database": env.cr.dbname,
            "is_admin": is_admin,
            "calls_24h": Log.search_count([("create_date", ">=", since)]),
            "errors_24h": Log.search_count([("create_date", ">=", since), ("status", "!=", "ok")]),
            "keys": env["nxb.mcp.key"].search_count([("active", "=", True)]),
            "apps": env["nxb.mcp.session"].search_count([("last_seen", ">=", week)]),
            "databases": [{"id": c.id, "name": c.name, "code": c.code, "kind": c._kind_label(), "state": c.state,
                           "full": c._full_support()} for c in conns],
            "packs_on": [PACKS[k].title for k in PACKS if pack_enabled(env, k)],
            "tools_on": sum(1 for t in TOOLS.values() if pack_enabled(env, t.pack)),
            "per_day": per_day,
            "top_tools": [{"tool": t, "count": n} for t, n in top],
            "recent": [{"id": r.id, "tool": r.tool, "user": r.user_id.name, "db": r.database_code,
                        "status": r.status, "when": fields.Datetime.to_string(r.create_date)} for r in recent],
        }
