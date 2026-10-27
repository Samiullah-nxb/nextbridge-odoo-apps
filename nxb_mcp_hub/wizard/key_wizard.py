# -*- coding: utf-8 -*-
# Copyright 2026 Nextbridge. License OPL-1.
import json

from odoo import _, fields, models
from odoo.exceptions import AccessError

from ..core import server as mcp_server


class McpKeyWizard(models.TransientModel):
    _name = "nxb.mcp.key.wizard"
    _description = "AI Connector: new connector key"

    name = fields.Char("Used for", required=True, default=lambda s: _("AI assistant"))
    user_id = fields.Many2one("res.users", required=True, default=lambda s: s.env.user,
                              help="The assistant acts as this user.")
    scope = fields.Selection([("read", "Read only"), ("write", "Read and write")], required=True, default="write")
    connection_ids = fields.Many2many("nxb.mcp.connection", string="Limit to databases")
    expires_at = fields.Datetime("Expires")
    token = fields.Char(readonly=True)
    url_header = fields.Char("Connector URL", readonly=True)
    url_with_key = fields.Char("URL with key (for apps without headers)", readonly=True)
    config_desktop = fields.Text("Desktop / IDE configuration", readonly=True)
    done = fields.Boolean()

    def action_generate(self):
        self.ensure_one()
        if self.user_id != self.env.user and not self.env.user.has_group("base.group_system"):
            raise AccessError(_("Only administrators can create keys for other users."))
        key, token = self.env["nxb.mcp.key"]._generate({
            "name": self.name, "user_id": self.user_id.id, "scope": self.scope,
            "connection_ids": [(6, 0, self.connection_ids.ids)], "expires_at": self.expires_at})
        url = mcp_server.endpoint_url(self.env)
        cfg = {"mcpServers": {"odoo": {"command": "npx", "args": [
            "-y", "mcp-remote", url, "--header", "Authorization: Bearer ${ODOO_MCP_KEY}"],
            "env": {"ODOO_MCP_KEY": token}}}}
        self.write({"token": token, "url_header": url, "url_with_key": "%s/key/%s" % (url, token),
                    "config_desktop": json.dumps(cfg, indent=2), "done": True})
        return {"type": "ir.actions.act_window", "res_model": self._name, "res_id": self.id, "view_mode": "form",
                "target": "new", "name": _("Your new connector key")}

    def action_close(self):
        self.write({"token": False, "url_with_key": False, "config_desktop": False})
        return {"type": "ir.actions.act_window_close"}
