# -*- coding: utf-8 -*-
# Copyright 2026 Nextbridge. License OPL-1.
from odoo import fields, models

P = "nxb_shared_user_login."


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    nxb_shared_enabled = fields.Boolean("Shared user accounts", config_parameter=P + "enabled",
                                        help="Let several employees use one user account, each with their PIN.")
    nxb_shared_idle_minutes = fields.Integer("Ask for the PIN again after (minutes idle)",
                                             config_parameter=P + "idle_minutes", default=0,
                                             help="0 = never. Useful on shared screens.")
    nxb_shared_max_attempts = fields.Integer("Wrong PINs before locking", config_parameter=P + "max_attempts",
                                             default=5)
    nxb_shared_lock_minutes = fields.Integer("Lock duration (minutes)", config_parameter=P + "lock_minutes",
                                             default=15)
    nxb_shared_allow_api = fields.Boolean("Allow the external API for shared accounts",
                                          config_parameter=P + "allow_api",
                                          help="XML-RPC / JSON-RPC calls cannot ask for a PIN, so they are refused "
                                               "for shared accounts unless you allow them here.")
