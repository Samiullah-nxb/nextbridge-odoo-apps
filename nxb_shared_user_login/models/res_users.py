# -*- coding: utf-8 -*-
# Copyright 2026 Nextbridge. License OPL-1.
import contextlib
import time

from odoo import _, api, fields, models
from odoo.exceptions import AccessDenied, ValidationError
from odoo.http import request

PARAM = "nxb_shared_user_login."
SESSION_EMP = "nxb_shared_employee_id"
SESSION_UID = "nxb_shared_uid"
SESSION_SEEN = "nxb_shared_seen"
SESSION_TIMEOUT = "nxb_shared_timeout"


class ResUsers(models.Model):
    _inherit = "res.users"

    nxb_shared = fields.Boolean("Shared account", copy=False,
                                help="Several employees use this login. After the login, each one picks their "
                                     "name and types their PIN; their name is shown on messages and e-mails.")
    nxb_employee_ids = fields.Many2many("hr.employee", "nxb_shared_user_employee_rel", "user_id", "employee_id",
                                        string="Employees using this account", copy=False)

    nxb_feature_on = fields.Boolean(compute="_compute_nxb_feature_on")

    def _compute_nxb_feature_on(self):
        on = self._nxb_feature_on()
        for user in self:
            user.nxb_feature_on = on

    @api.constrains("nxb_shared", "nxb_employee_ids", "groups_id", "share")
    def _check_nxb_shared(self):
        for user in self.filtered("nxb_shared"):
            if user.share:
                raise ValidationError(_("Only internal users can be shared (%s is a portal or public user).",
                                        user.name))
            if user._is_system():
                raise ValidationError(_("Administrator accounts cannot be shared (%s). Create a user with "
                                        "only the rights the team needs.", user.name))

    @classmethod
    def check(cls, db, uid, passwd):
        """External API (XML-RPC / JSON-RPC) calls never pass the PIN screen, so nobody could tell which
        employee acted: they are refused for shared accounts unless explicitly allowed in the settings."""
        super().check(db, uid, passwd)
        with contextlib.closing(cls.pool.cursor()) as cr:
            cr.execute("SELECT nxb_shared FROM res_users WHERE id = %s", [uid])
            row = cr.fetchone()
            if not row or not row[0]:
                return
            cr.execute("SELECT key, value FROM ir_config_parameter WHERE key IN %s",
                       [(PARAM + "enabled", PARAM + "allow_api")])
            params = dict(cr.fetchall())
            if params.get(PARAM + "enabled") == "True" and params.get(PARAM + "allow_api") != "True":
                raise AccessDenied(_("This is a shared account: it can only be used in the browser, "
                                     "with an employee PIN."))

    # ------------------------------------------------------------------ settings
    @api.model
    def _nxb_feature_on(self):
        return self.env["ir.config_parameter"].sudo().get_param(PARAM + "enabled") == "True"

    @api.model
    def _nxb_int_param(self, key, default):
        try:
            return int(self.env["ir.config_parameter"].sudo().get_param(PARAM + key, default))
        except (TypeError, ValueError):
            return default

    def _nxb_is_shared(self):
        self.ensure_one()
        return bool(self.nxb_shared and self._nxb_feature_on())

    # ------------------------------------------------------------------ current employee
    @api.model
    def _nxb_active_employee(self):
        """Employee signed in on the shared account of the current request (empty recordset otherwise)."""
        Employee = self.env["hr.employee"].sudo()
        try:
            session = request.session if request else None
        except RuntimeError:
            session = None
        if not session or not session.uid or session.uid != self.env.uid:
            return Employee
        emp_id = session.get(SESSION_EMP)
        if not emp_id or session.get(SESSION_UID) != session.uid:
            return Employee
        user = self.env["res.users"].sudo().browse(session.uid)
        if not user._nxb_is_shared():
            return Employee
        emp = Employee.browse(emp_id).exists()
        if not emp or not emp.active or emp not in user.nxb_employee_ids:
            return Employee
        return emp

    def _nxb_allowed_employees(self):
        self.ensure_one()
        return self.sudo().nxb_employee_ids.filtered("active").sorted("name")

    @api.model
    def _nxb_session_set(self, employee):
        request.session[SESSION_EMP] = employee.id
        request.session[SESSION_UID] = request.session.uid
        request.session[SESSION_SEEN] = int(time.time())

    @api.model
    def _nxb_session_clear(self):
        for key in (SESSION_EMP, SESSION_UID, SESSION_SEEN):
            request.session.pop(key, None)
