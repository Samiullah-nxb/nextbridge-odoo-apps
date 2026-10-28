# -*- coding: utf-8 -*-
# Copyright 2026 Nextbridge. License OPL-1.
"""Google OAuth: send the administrator to Google's consent page and store the refresh token."""
import json
import secrets

from werkzeug.exceptions import Forbidden

from odoo import _, http
from odoo.http import request
from odoo.tools import consteq, html_escape
from odoo.tools.misc import hmac as odoo_hmac

from ..models.drive_client import DriveClient, DriveError, authorization_url, exchange_code

SETTINGS_URL = "/odoo/action-nxb_gdrive_attachments.action_gdrive_settings"


class GDriveOAuth(http.Controller):

    def _state(self, company_id, nonce):
        payload = json.dumps({"c": company_id, "u": request.env.uid, "n": nonce}, separators=(",", ":"))
        sig = odoo_hmac(request.env(su=True), "nxb_gdrive_oauth", payload)
        return payload + "." + sig

    @http.route("/nxb_gdrive/oauth/start", type="http", auth="user", methods=["GET"])
    def start(self, company_id=None, **kw):
        if not request.env.user.has_group("base.group_system"):
            raise Forbidden()
        company = request.env["res.company"].browse(int(company_id or request.env.company.id)).exists()
        if not company or company not in request.env.user.company_ids:
            raise Forbidden()
        cfg = company._gdrive_cfg()
        nonce = secrets.token_urlsafe(16)
        request.session["nxb_gdrive_nonce"] = nonce
        url = authorization_url(cfg["client_id"], company._gdrive_redirect_uri(), self._state(company.id, nonce),
                                cfg["scope"], cfg.get("auth_url"))
        return request.redirect(url, local=False)

    @http.route("/nxb_gdrive/oauth/callback", type="http", auth="user", methods=["GET"])
    def callback(self, code=None, state=None, error=None, **kw):
        if not request.env.user.has_group("base.group_system"):
            raise Forbidden()
        if error:
            return self._back(_("Google did not give access: %s", error), "danger")
        try:
            payload, sig = (state or "").rsplit(".", 1)
            data = json.loads(payload)
        except ValueError:
            raise Forbidden()
        expected = odoo_hmac(request.env(su=True), "nxb_gdrive_oauth", payload)
        nonce = request.session.pop("nxb_gdrive_nonce", None)
        if not consteq(sig, expected) or not nonce or data.get("n") != nonce or data.get("u") != request.env.uid:
            raise Forbidden()
        company = request.env["res.company"].sudo().browse(int(data["c"])).exists()
        if not company:
            raise Forbidden()
        cfg = company._gdrive_cfg()
        try:
            info = exchange_code(cfg.get("token_url"), cfg["client_id"], cfg["client_secret"], code,
                                 company._gdrive_redirect_uri())
            company.write({"gdrive_refresh_token": info["refresh_token"], "gdrive_auth": "oauth"})
            client = DriveClient(company._gdrive_cfg(), company._gdrive_cache_key())
            about = client.about()
            who = about.get("user") or {}
            company.gdrive_account = who.get("emailAddress") or who.get("displayName") or ""
        except DriveError as e:
            return self._back(str(e), "danger")
        return self._back(_("Google Drive is connected (%s).", company.gdrive_account), "success")

    def _back(self, message, kind):
        if kind == "success":
            return request.redirect(SETTINGS_URL)
        body = ("<html><head><meta charset='utf-8'><title>Google Drive</title></head>"
                "<body style='font-family:sans-serif;max-width:640px;margin:60px auto;line-height:1.5'>"
                "<h2>Google Drive could not be connected</h2><p>%s</p><p><a href='%s'>Back to the settings</a></p>"
                "</body></html>") % (html_escape(message), SETTINGS_URL)
        return request.make_response(body, headers=[("Content-Type", "text/html; charset=utf-8")], status=400)
