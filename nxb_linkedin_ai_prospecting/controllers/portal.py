# -*- coding: utf-8 -*-
import base64
import hmac
import mimetypes

from odoo import http
from odoo.http import request
from werkzeug.exceptions import NotFound


class LiPortfolioController(http.Controller):

    @http.route("/li/portfolio/<int:service_id>/<string:token>", type="http", auth="public", methods=["GET"])
    def portfolio(self, service_id, token, **kw):
        """Portfolio file for prospects: no login, long random token; PDF and images open in the browser."""
        service = request.env["li.service"].sudo().browse(service_id).exists()
        if not service or not service.portfolio_file or not service.portfolio_token or \
                not hmac.compare_digest(service.portfolio_token, token):
            raise NotFound()
        data = base64.b64decode(service.portfolio_file)
        name = service.portfolio_filename or "portfolio"
        mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
        inline = mime == "application/pdf" or mime.startswith("image/")
        disposition = "%s; filename=\"%s\"" % ("inline" if inline else "attachment", name.replace('"', ""))
        return request.make_response(data, headers=[("Content-Type", mime), ("Content-Disposition", disposition),
                                                    ("X-Robots-Tag", "noindex")])


class LiEngineViewController(http.Controller):
    """LinkedIn's real sign-in page rendered inside Odoo: screenshots out, clicks and keys in.
    Nothing typed here is logged or stored; it goes straight to the engine's browser."""

    def _client(self, profile_id):
        if not request.env.user.has_group("nxb_linkedin_ai_prospecting.group_li_manager"):
            raise NotFound()
        profile = request.env["li.profile"].sudo().browse(int(profile_id)).exists()
        if not profile or profile.execution_mode != "engine":
            raise NotFound()
        return profile, profile._engine()

    @http.route("/li/engine/<int:profile_id>/open_login", type="json", auth="user")
    def open_login(self, profile_id):
        profile, client = self._client(profile_id)
        if not client.is_alive():
            client.start()
        return client.call("login_open", timeout=60)

    @http.route("/li/engine/<int:profile_id>/screen", type="json", auth="user")
    def screen(self, profile_id):
        profile, client = self._client(profile_id)
        shot = client.call("screenshot", timeout=30)
        status = client.call("login_status", timeout=15)
        return {"shot": shot.get("data"), "logged_in": (status.get("data") or {}).get("logged_in"),
                "error": shot.get("error")}

    @http.route("/li/engine/<int:profile_id>/input", type="json", auth="user")
    def input(self, profile_id, kind, x=None, y=None, text=None, key=None):
        profile, client = self._client(profile_id)
        return client.call("input", timeout=30, kind=kind, x=x, y=y, text=text, key=key)

    @http.route("/li/engine/<int:profile_id>/finish_login", type="json", auth="user")
    def finish_login(self, profile_id):
        profile, client = self._client(profile_id)
        status = client.call("login_status", timeout=15)
        if not (status.get("data") or {}).get("logged_in"):
            return {"ok": False, "error": "Not signed in yet"}
        profile._apply_me(client.call("me", timeout=120))
        return {"ok": profile.state == "connected", "name": profile.own_name, "english": profile.language_ok}
