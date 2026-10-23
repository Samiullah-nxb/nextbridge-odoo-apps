# -*- coding: utf-8 -*-
import base64
import io
import json
import zipfile

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class LiConnectWizard(models.TransientModel):
    _name = "li.connect.wizard"
    _description = "Connect LinkedIn account"

    profile_id = fields.Many2one("li.profile", required=True)
    execution_mode = fields.Selection(related="profile_id.execution_mode")
    method = fields.Selection([
        ("login", "Log in here (LinkedIn sign-in page inside Odoo)"),
        ("import", "Import session (signed in on your own computer)"),
        ("cookie", "Paste the li_at cookie"),
    ], default="login", required=True)
    session_file = fields.Binary("Session file (.zip or .json)", attachment=False)
    session_filename = fields.Char()
    li_at = fields.Char("li_at cookie")

    def action_connect(self):
        self.ensure_one()
        profile = self.profile_id.sudo()
        if profile.execution_mode != "engine":
            raise UserError(_("AI browser agent accounts sign in to LinkedIn in Chrome; nothing to connect here."))
        client = profile._engine()
        if not client.is_alive():
            client.start()
        if self.method == "login":
            return {"type": "ir.actions.client", "tag": "li_remote_login", "target": "new",
                    "name": _("Sign in to LinkedIn"), "params": {"profile_id": profile.id}}
        if self.method == "cookie":
            if not self.li_at:
                raise UserError(_("Paste the li_at cookie value."))
            res = client.call("import_session", timeout=120, cookies={"li_at": self.li_at})
        else:
            if not self.session_file:
                raise UserError(_("Upload the session file."))
            cookies = self._parse_session(base64.b64decode(self.session_file))
            res = client.call("import_session", timeout=120, cookies=cookies)
        # Never keep secrets in the wizard.
        self.write({"li_at": False, "session_file": False})
        if not res.get("ok"):
            raise UserError(_("Import failed: %s") % (res.get("error") or res.get("code")))
        profile._apply_me(client.call("me", timeout=120))
        return profile.action_test_connection() if profile.state == "connected" else {"type": "ir.actions.act_window_close"}

    @api.model
    def _parse_session(self, raw):
        """Accept a zip (cookies.json [+ source-state.json]), a Playwright storage state or a cookie list."""
        data = None
        if raw[:2] == b"PK":
            with zipfile.ZipFile(io.BytesIO(raw)) as z:
                for name in z.namelist():
                    base = name.rsplit("/", 1)[-1]
                    if base in ("cookies.json", "storage_state.json", "state.json"):
                        data = json.loads(z.read(name))
                        break
            if data is None:
                raise UserError(_("The zip has no cookies.json."))
        else:
            try:
                data = json.loads(raw)
            except ValueError:
                raise UserError(_("The file is not JSON or zip."))
        cookies = data.get("cookies") if isinstance(data, dict) else data
        if not isinstance(cookies, list):
            raise UserError(_("No cookie list found in the file."))
        for c in cookies:
            # Chrome-extension exports use expirationDate; Playwright uses expires
            if "expirationDate" in c and "expires" not in c:
                c["expires"] = c.pop("expirationDate")
            same = (c.get("sameSite") or "").lower()
            c["sameSite"] = {"strict": "Strict", "lax": "Lax", "none": "None", "no_restriction": "None"}.get(same)
        return cookies


class LiTokenWizard(models.TransientModel):
    _name = "li.token.wizard"
    _description = "New AI connector URL"

    name = fields.Char(default=lambda s: _("AI connector"), required=True)
    profile_ids = fields.Many2many("li.profile", string="Limit to accounts")
    url = fields.Char("Connector URL", readonly=True)
    done = fields.Boolean()

    def action_generate(self):
        self.ensure_one()
        if not self.env.user.has_group("nb_linkedin_ai_prospecting.group_li_manager"):
            raise UserError(_("Only a LinkedIn Sales manager can create connector URLs."))
        _tok, url = self.env["li.connector.token"]._generate(self.name, self.profile_ids.ids)
        self.write({"url": url, "done": True})
        return {"type": "ir.actions.act_window", "res_model": self._name, "res_id": self.id, "view_mode": "form",
                "target": "new"}

    def action_close(self):
        self.write({"url": False})
        return {"type": "ir.actions.act_window_close"}
