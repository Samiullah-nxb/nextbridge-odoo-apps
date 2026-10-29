# -*- coding: utf-8 -*-
# Copyright 2026 Nextbridge. License OPL-1.
import base64

from markupsafe import Markup

from werkzeug.exceptions import NotFound

from odoo import _, http
from odoo.http import request
from odoo.tools import consteq

from ..models.res_users import SESSION_TIMEOUT

SHARED_HOME = "/odoo"
DOCTYPE = Markup("<!DOCTYPE html>")
SESSION_PREV = "nxb_shared_previous"


def _safe_redirect(url):
    if url and url.startswith("/") and not url.startswith("//") and not url.startswith("/shared_login"):
        return url
    return SHARED_HOME


class SharedLogin(http.Controller):

    def _user(self):
        user = request.env.user
        if not user._nxb_is_shared():
            return None
        return user

    @http.route("/shared_login", type="http", auth="user", methods=["GET", "POST"], csrf=True, sitemap=False)
    def select(self, employee_id=None, pin=None, redirect=None, **kw):
        user = self._user()
        if not user:
            return request.redirect(_safe_redirect(redirect))
        Users = request.env["res.users"]
        Log = request.env["nxb.shared.login.log"]
        employees = user._nxb_allowed_employees()
        timed_out = request.session.pop(SESSION_TIMEOUT, None)
        if timed_out:
            Log._add(user, request.env["hr.employee"].sudo().browse(timed_out).exists(), "timeout")
        error = None
        selected = None
        if request.httprequest.method == "POST":
            try:
                selected = employees.filtered(lambda e: e.id == int(employee_id or 0))
            except ValueError:
                selected = None
            if not selected:
                error = _("Choose your name.")
            else:
                ok, error = selected._nxb_check_pin((pin or "").strip())
                if ok:
                    previous = Users._nxb_active_employee().id or request.session.pop(SESSION_PREV, None)
                    Users._nxb_session_set(selected)
                    Log._add(user, selected, "switch" if previous and previous != selected.id else "sign_in")
                    return request.redirect(_safe_redirect(redirect))
                Log._add(user, selected, "locked" if selected.sudo().nxb_pin_locked_until else "wrong_pin")
        return request.render("nxb_shared_user_login.select_page", {"doctype": DOCTYPE,
            "user": user, "employees": employees, "error": error,
            "selected_id": selected.id if selected else (employees.id if len(employees) == 1 else 0),
            "redirect": redirect or "", "company": request.env.company,
        })

    @http.route("/shared_login/switch", type="http", auth="user", methods=["GET"], sitemap=False)
    def switch(self, **kw):
        if self._user():
            current = request.env["res.users"]._nxb_active_employee()
            request.env["res.users"]._nxb_session_clear()
            if current:
                request.session[SESSION_PREV] = current.id
        return request.redirect("/shared_login")

    @http.route("/shared_login/avatar/<int:employee_id>", type="http", auth="user", methods=["GET"], sitemap=False)
    def avatar(self, employee_id, **kw):
        user = self._user()
        if not user:
            raise NotFound()
        emp = user._nxb_allowed_employees().filtered(lambda e: e.id == employee_id)
        if not emp:
            raise NotFound()
        data = emp.avatar_128 or b""
        raw = base64.b64decode(data) if data else b""
        mimetype = "image/svg+xml" if raw.lstrip().startswith(b"<") else "image/png"
        return request.make_response(raw, headers=[("Content-Type", mimetype), ("Cache-Control", "private, max-age=600")])

    @http.route("/shared_login/pin", type="http", auth="user", methods=["GET", "POST"], csrf=True, sitemap=False)
    def change_pin(self, current=None, new=None, confirm=None, **kw):
        user = self._user()
        emp = request.env["res.users"]._nxb_active_employee() if user else None
        if not emp:
            return request.redirect("/shared_login")
        error = done = None
        if request.httprequest.method == "POST":
            if not emp.pin or not consteq(str(current or ""), emp.pin):
                error = _("The current PIN is not correct.")
            elif not (new or "").isdigit() or len(new) < 4 or len(new) > 12:
                error = _("The new PIN must have 4 to 12 digits.")
            elif new != confirm:
                error = _("The two new PINs are different.")
            elif new == emp.pin:
                error = _("The new PIN must be different from the current one.")
            else:
                emp.sudo().pin = new
                request.env["nxb.shared.login.log"]._add(user, emp, "pin_changed")
                done = True
        return request.render("nxb_shared_user_login.pin_page", {"doctype": DOCTYPE, "employee": emp, "error": error, "done": done,
                                                                 "company": request.env.company})
