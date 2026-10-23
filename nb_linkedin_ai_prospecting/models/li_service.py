# -*- coding: utf-8 -*-
import secrets

from odoo import api, fields, models


class LiService(models.Model):
    _name = "li.service"
    _description = "LinkedIn Sales: Service"
    _order = "sequence, name"

    name = fields.Char(required=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    description = fields.Text(help="What you sell, in a few sentences. the AI reads this.")
    value_points = fields.Text(help="One value point per line: outcomes, proof, differentiators.")
    tag_ids = fields.Many2many("crm.tag", string="CRM tags", help="Added to CRM leads created for this service.")
    portfolio_file = fields.Binary(attachment=True)
    portfolio_filename = fields.Char()
    portfolio_link = fields.Char(help="External link to a portfolio or case studies.")
    portfolio_token = fields.Char(copy=False, groups="base.group_system")
    portfolio_public_url = fields.Char(compute="_compute_portfolio_public_url")
    contact_phone = fields.Char(help="Given to prospects only when they ask for it.")
    contact_email = fields.Char(help="Given to prospects only when they ask for it.")
    company_id = fields.Many2one("res.company", default=lambda s: s.env.company)

    @api.depends("portfolio_file", "portfolio_link")
    def _compute_portfolio_public_url(self):
        base = self.env["ir.config_parameter"].sudo().get_param("web.base.url", "")
        for rec in self:
            token = rec.sudo().portfolio_token
            if rec.portfolio_file and token:
                rec.portfolio_public_url = "%s/li/portfolio/%s/%s" % (base, rec.id, token)
            else:
                rec.portfolio_public_url = rec.portfolio_link or False

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("portfolio_file"):
                vals["portfolio_token"] = secrets.token_urlsafe(32)
        return super().create(vals_list)

    def write(self, vals):
        if "portfolio_file" in vals:
            # A new file gets a new link; removing the file ends the link.
            vals["portfolio_token"] = secrets.token_urlsafe(32) if vals["portfolio_file"] else False
        return super().write(vals)

    def _ai_context(self):
        self.ensure_one()
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description or "",
            "value_points": [v.strip() for v in (self.value_points or "").splitlines() if v.strip()],
            "portfolio_link": self.portfolio_public_url or "",
            "contact_phone": self.contact_phone or "",
            "contact_email": self.contact_email or "",
            "rules": "Add the portfolio link only when asked for examples or when it clearly helps. "
                     "Give phone or email only when the prospect asks for them.",
        }
