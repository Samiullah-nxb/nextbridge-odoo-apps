# -*- coding: utf-8 -*-
import hashlib
import secrets

from odoo import _, api, fields, models


def hash_token(token):
    return hashlib.sha256((token or "").encode()).hexdigest()


class LiConnectorToken(models.Model):
    _name = "li.connector.token"
    _description = "LinkedIn Sales: AI connector URL"
    _order = "id desc"

    name = fields.Char(required=True, default=lambda s: _("AI connector"))
    token_hash = fields.Char(required=True, index=True, groups="base.group_system", copy=False)
    token_hint = fields.Char("Ends with", readonly=True)
    active = fields.Boolean(default=True)
    profile_ids = fields.Many2many("li.profile", string="Limited to accounts",
                                   help="Empty = every LinkedIn account. When set, this connector sees, counts "
                                        "and reports only these accounts.")
    last_used = fields.Datetime(readonly=True)
    call_count = fields.Integer(readonly=True)
    user_id = fields.Many2one("res.users", "Runs as", readonly=True,
                              default=lambda s: s.env.ref("nxb_linkedin_ai_prospecting.user_li_connector",
                                                          raise_if_not_found=False))

    @api.model
    def _generate(self, name=None, profile_ids=None):
        token = secrets.token_urlsafe(32)
        rec = self.sudo().create({
            "name": name or _("AI connector"),
            "token_hash": hash_token(token),
            "token_hint": token[-4:],
            "profile_ids": [(6, 0, profile_ids or [])],
        })
        base = self.env["ir.config_parameter"].sudo().get_param("web.base.url", "").rstrip("/")
        return rec, "%s/li/mcp/%s" % (base, token)

    @api.model
    def _from_token(self, token):
        if not token or len(token) < 20:
            return self.browse()
        return self.sudo().search([("token_hash", "=", hash_token(token)), ("active", "=", True)], limit=1)

    def action_revoke(self):
        self.write({"active": False})

    def _profiles(self):
        """LinkedIn accounts this connector may see."""
        self.ensure_one()
        Profile = self.env["li.profile"].sudo()
        return self.profile_ids.filtered("active") if self.profile_ids else Profile.search([])
