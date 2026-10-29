# -*- coding: utf-8 -*-
# Copyright 2026 Nextbridge. License OPL-1.
from datetime import timedelta

from odoo import _, fields, models
from odoo.tools import consteq


class HrEmployee(models.Model):
    _inherit = "hr.employee"

    nxb_shared_user_ids = fields.Many2many("res.users", "nxb_shared_user_employee_rel", "employee_id", "user_id",
                                           string="Shared accounts", groups="hr.group_hr_user")
    nxb_pin_failures = fields.Integer(groups="base.group_system", copy=False)
    nxb_pin_locked_until = fields.Datetime("PIN locked until", groups="hr.group_hr_user", copy=False)

    def _nxb_check_pin(self, pin):
        """Return (ok, message). Counts failures and locks the PIN after too many."""
        self.ensure_one()
        emp = self.sudo()
        Users = self.env["res.users"]
        now = fields.Datetime.now()
        if emp.nxb_pin_locked_until and emp.nxb_pin_locked_until > now:
            minutes = int((emp.nxb_pin_locked_until - now).total_seconds() // 60) + 1
            return False, _("Too many wrong PINs. Try again in %(m)s minutes or ask your manager.", m=minutes)
        if not emp.pin:
            return False, _("%(name)s has no PIN yet. Ask your manager to set one in the employee form "
                            "(HR Settings tab).", name=emp.name)
        if pin and consteq(str(pin), emp.pin):
            emp.write({"nxb_pin_failures": 0, "nxb_pin_locked_until": False})
            return True, ""
        failures = emp.nxb_pin_failures + 1
        limit = Users._nxb_int_param("max_attempts", 5)
        vals = {"nxb_pin_failures": failures}
        if limit and failures >= limit:
            vals.update(nxb_pin_failures=0, nxb_pin_locked_until=now + timedelta(
                minutes=Users._nxb_int_param("lock_minutes", 15)))
            emp.write(vals)
            return False, _("Too many wrong PINs: the PIN is locked for %(m)s minutes.",
                            m=Users._nxb_int_param("lock_minutes", 15))
        emp.write(vals)
        left = limit - failures if limit else None
        if left:
            return False, _("Wrong PIN. %(n)s attempts left.", n=left)
        return False, _("Wrong PIN.")

    def action_nxb_unlock_pin(self):
        self.sudo().write({"nxb_pin_failures": 0, "nxb_pin_locked_until": False})
