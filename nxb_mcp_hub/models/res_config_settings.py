# -*- coding: utf-8 -*-
# Copyright 2026 Nextbridge. License OPL-1.
from odoo import api, fields, models

from ..core import server as mcp_server

P = "nxb_mcp_hub."
# checkbox field -> (parameter key, default)
_FLAGS = {
    "nxb_mcp_strict_readonly": ("strict_readonly", False),
    "nxb_mcp_allow_delete": ("allow_delete", True),
    "nxb_mcp_log_arguments": ("log_arguments", True),
    "nxb_mcp_oauth_enabled": ("oauth_enabled", True),
    "nxb_mcp_oauth_dcr": ("oauth_dcr", True),
}


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    nxb_mcp_strict_readonly = fields.Boolean("Read-only for everyone",
                                             help="No assistant may create, change or delete anything, "
                                                  "whatever key or sign-in it uses.")
    nxb_mcp_allow_delete = fields.Boolean("Allow deleting records")
    nxb_mcp_log_arguments = fields.Boolean("Log tool arguments")
    nxb_mcp_oauth_enabled = fields.Boolean("Allow OAuth sign-in",
                                           help="Lets AI apps connect with an Odoo login instead of a key.")
    nxb_mcp_oauth_dcr = fields.Boolean("Allow new apps to register themselves",
                                       help="OAuth dynamic client registration. Required by most AI apps.")
    nxb_mcp_max_records = fields.Integer("Maximum records per answer", config_parameter=P + "max_records",
                                         default=500)
    nxb_mcp_rate_limit = fields.Integer("Calls per minute per key", config_parameter=P + "rate_limit",
                                        default=120, help="0 = unlimited.")
    nxb_mcp_log_days = fields.Integer("Keep activity for (days)", config_parameter=P + "log_days", default=90)
    nxb_mcp_endpoint = fields.Char("Connector address", compute="_compute_nxb_mcp_endpoint")

    @api.depends("company_id")
    def _compute_nxb_mcp_endpoint(self):
        url = mcp_server.endpoint_url(self.env)
        for rec in self:
            rec.nxb_mcp_endpoint = url

    @api.model
    def get_values(self):
        res = super().get_values()
        icp = self.env["ir.config_parameter"].sudo()
        for fname, (key, default) in _FLAGS.items():
            val = icp.get_param(P + key)
            res[fname] = default if val in (None, False, "") else str(val).lower() in ("1", "true", "yes")
        return res

    def set_values(self):
        super().set_values()
        icp = self.env["ir.config_parameter"].sudo()
        for fname, (key, _default) in _FLAGS.items():
            icp.set_param(P + key, "True" if self[fname] else "False")
