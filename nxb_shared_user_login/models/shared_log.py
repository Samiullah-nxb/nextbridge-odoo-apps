# -*- coding: utf-8 -*-
# Copyright 2026 Nextbridge. License OPL-1.
from datetime import timedelta

from odoo import api, fields, models
from odoo.http import request


class SharedLoginLog(models.Model):
    _name = "nxb.shared.login.log"
    _description = "Shared account sign-in"
    _order = "id desc"
    _rec_name = "event"

    create_date = fields.Datetime("Date", readonly=True)
    user_id = fields.Many2one("res.users", "Shared account", required=True, ondelete="cascade", index=True)
    employee_id = fields.Many2one("hr.employee", ondelete="set null", index=True)
    event = fields.Selection([("sign_in", "Signed in"), ("switch", "Switched employee"),
                              ("timeout", "Locked after inactivity"), ("wrong_pin", "Wrong PIN"),
                              ("locked", "PIN locked"), ("pin_changed", "PIN changed")], required=True, index=True)
    ip = fields.Char("IP address")

    @api.model
    def _add(self, user, employee, event):
        ip = False
        try:
            ip = request.httprequest.remote_addr if request else False
        except RuntimeError:
            pass
        self.sudo().create({"user_id": user.id, "employee_id": employee.id if employee else False,
                            "event": event, "ip": ip})

    @api.autovacuum
    def _gc_old(self):
        self.sudo().search([("create_date", "<", fields.Datetime.now() - timedelta(days=365))]).unlink()
