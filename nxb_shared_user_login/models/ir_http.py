# -*- coding: utf-8 -*-
# Copyright 2026 Nextbridge. License OPL-1.
import time
from urllib.parse import quote

import werkzeug.exceptions
import werkzeug.utils

from odoo import models
from odoo.http import SessionExpiredException, request

from .res_users import SESSION_SEEN, SESSION_TIMEOUT

SELECT_URL = "/shared_login"
# reachable before an employee has signed in on a shared account
ALLOWED = ("/shared_login", "/web/login", "/web/session/logout", "/web/assets/", "/web/static/",
           "/web/webclient/locale/", "/web/webclient/translations", "/web/binary/company_logo", "/logo",
           "/favicon.ico", "/web/session/check", "/web/health")


class IrHttp(models.AbstractModel):
    _inherit = "ir.http"

    @classmethod
    def _authenticate(cls, endpoint):
        super()._authenticate(endpoint)
        cls._nxb_shared_gate(endpoint)

    @classmethod
    def _nxb_shared_gate(cls, endpoint):
        session = request.session
        if not session.uid:
            return
        path = request.httprequest.path
        if path.startswith(ALLOWED):
            return
        user = request.env["res.users"].sudo().browse(session.uid)
        if not user.exists() or not user._nxb_is_shared():
            return
        Users = request.env["res.users"]
        emp = Users.with_user(session.uid)._nxb_active_employee()
        now = int(time.time())
        if emp:
            idle = Users._nxb_int_param("idle_minutes", 0)
            seen = session.get(SESSION_SEEN) or now
            if idle and now - seen > idle * 60:
                # the redirect below rolls the transaction back: the selection page logs the timeout
                Users._nxb_session_clear()
                session[SESSION_TIMEOUT] = emp.id
                emp = None
            else:
                if now - seen >= 30:
                    session[SESSION_SEEN] = now
                return
        # no employee signed in (yet / any more)
        if endpoint.routing.get("type") in ("json", "jsonrpc"):
            raise SessionExpiredException("Select your name and enter your PIN first.")
        if request.httprequest.method in ("GET", "HEAD"):
            target = SELECT_URL
            if path not in ("/", "/web", "/odoo") and not path.startswith(SELECT_URL):
                target += "?redirect=" + quote(request.httprequest.full_path.rstrip("?"), safe="")
            raise werkzeug.exceptions.HTTPException(response=werkzeug.utils.redirect(target, 303))
        raise werkzeug.exceptions.Forbidden("Select your name and enter your PIN first.")

    def session_info(self):
        info = super().session_info()
        emp = self.env["res.users"]._nxb_active_employee()
        if emp:
            info["nxb_shared_employee"] = {"id": emp.id, "name": emp.name,
                                           "partner_id": emp.work_contact_id.id or False}
        return info
