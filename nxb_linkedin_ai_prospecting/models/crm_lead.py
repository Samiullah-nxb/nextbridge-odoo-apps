# -*- coding: utf-8 -*-
from odoo import fields, models


class CrmLead(models.Model):
    _inherit = "crm.lead"

    li_prospect_id = fields.Many2one("li.prospect", "LinkedIn prospect", index=True, copy=False)

    def action_open_li_prospect(self):
        self.ensure_one()
        return {"type": "ir.actions.act_window", "res_model": "li.prospect", "res_id": self.li_prospect_id.id,
                "view_mode": "form"}
