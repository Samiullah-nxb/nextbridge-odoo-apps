# -*- coding: utf-8 -*-
# Copyright 2026 Nextbridge. License OPL-1.
import re

from odoo import SUPERUSER_ID, _, api, fields, models
from odoo.exceptions import UserError, ValidationError

from ..core.context import LocalOrm, RemoteOrm, ToolContext, ToolError
from ..core.registry import PACKS
from ..core import server as mcp_server

MODULE = "nxb_mcp_hub"


class McpConnection(models.Model):
    _name = "nxb.mcp.connection"
    _description = "AI Connector: connected database"
    _order = "sequence, id"

    name = fields.Char(required=True, help="Friendly name shown to the assistant, e.g. 'Spain company'.")
    code = fields.Char(required=True, help="Short identifier the assistant uses in the 'database' argument.")
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    kind = fields.Selection([
        ("this", "This database"),
        ("local", "Another database on this server"),
        ("remote", "Database on another Odoo server"),
    ], required=True, default="local")
    db_name = fields.Char("Database name")
    url = fields.Char("Server URL", help="Base URL of the other Odoo server, e.g. https://erp.example.com")
    login = fields.Char("User login", help="Login of the Odoo user the assistant acts as in that database.")
    api_key = fields.Char("Odoo API key", groups="base.group_system",
                          help="An API key of that user (Preferences → Account Security → New API Key). "
                               "The assistant gets exactly this user's access rights there.")
    mcp_url = fields.Char("Remote connector URL", groups="base.group_system",
                          help="Only if the AI Connector app is also installed on the other server: its "
                               "connector URL. Unlocks every tool pack on that database.")
    mcp_key = fields.Char("Remote connector key", groups="base.group_system")
    federated = fields.Boolean(compute="_compute_federated")
    user_ids = fields.Many2many("res.users", string="Allowed users",
                                help="Leave empty to let every user with a connector key use this database.")
    note = fields.Text("Note for the assistant", help="Short context sent to the assistant, e.g. "
                                                      "'Production database of the Spanish company'.")
    state = fields.Selection([("unknown", "Not tested"), ("ok", "Working"), ("error", "Error")],
                             default="unknown", readonly=True)
    state_message = fields.Char(readonly=True)
    last_check = fields.Datetime(readonly=True)
    remote_version = fields.Char(readonly=True)
    app_installed = fields.Boolean("App installed there", readonly=True)
    call_count = fields.Integer(compute="_compute_call_count")

    _sql_constraints = [("code_uniq", "unique(code)", "The code must be unique.")]

    @api.depends("mcp_url")
    def _compute_federated(self):
        for rec in self:
            rec.federated = bool(rec.sudo().mcp_url and rec.sudo().mcp_key)

    def _compute_call_count(self):
        data = self.env["nxb.mcp.log"].sudo()._read_group([("connection_id", "in", self.ids)],
                                                          ["connection_id"], ["__count"])
        counts = {c.id: n for c, n in data}
        for rec in self:
            rec.call_count = counts.get(rec.id, 0)

    @api.constrains("code")
    def _check_code(self):
        for rec in self:
            if not re.match(r"^[a-z0-9][a-z0-9_\-]{0,39}$", rec.code or ""):
                raise ValidationError(_("The code may only contain lowercase letters, digits, '-' and '_' "
                                        "(max 40 characters)."))

    @api.constrains("kind")
    def _check_single_this(self):
        if self.search_count([("kind", "=", "this")]) > 1:
            raise ValidationError(_("There can only be one 'This database' connection."))

    @api.constrains("kind", "db_name", "url", "login")
    def _check_fields(self):
        for rec in self:
            if rec.kind == "local" and not rec.db_name:
                raise ValidationError(_("Enter the database name."))
            if rec.kind == "local" and rec.db_name == self.env.cr.dbname:
                raise ValidationError(_("This database is already connected as 'This database'."))
            if rec.kind == "remote" and not (rec.url and rec.db_name):
                raise ValidationError(_("Enter the server URL and the database name."))

    @api.onchange("name")
    def _onchange_name(self):
        if self.name and not self.code:
            self.code = re.sub(r"[^a-z0-9]+", "-", self.name.lower()).strip("-")[:40] or False

    def unlink(self):
        if any(r.kind == "this" for r in self):
            raise UserError(_("'This database' cannot be deleted. Archive it to hide it from the assistant."))
        return super().unlink()

    # ------------------------------------------------------------------ helpers
    def _user_allowed(self, user):
        self.ensure_one()
        return not self.user_ids or user in self.user_ids or user.has_group("base.group_system")

    def _kind_label(self):
        return dict(self._fields["kind"].selection)[self.kind]

    def _describe(self):
        self.ensure_one()
        return {"code": self.code, "name": self.name, "kind": self.kind,
                "database": self.db_name or self.env.cr.dbname, "note": self.note or ""}

    def _remote_orm(self):
        rec = self.sudo()
        if not (rec.login and rec.api_key):
            raise ToolError("Connection %r has no login or API key configured." % rec.code)
        return RemoteOrm(rec.url, rec.db_name, rec.login, rec.api_key)

    def _run_federated(self, tool_name, arguments):
        rec = self.sudo()
        return mcp_server.forward_mcp(rec.mcp_url, rec.mcp_key, tool_name, arguments)

    def _local_uid(self, env):
        """Authenticate with an Odoo API key of the target database; returns the user id."""
        rec = self.sudo()
        if not (rec.login and rec.api_key):
            raise ToolError("Connection %r has no login or API key configured." % rec.code)
        uid = env["res.users.apikeys"]._check_credentials(scope="rpc", key=rec.api_key)
        if not uid:
            raise ToolError("The API key of connection %r is not valid in database %r." % (rec.code, rec.db_name))
        user = env["res.users"].browse(uid)
        if user.login != rec.login:
            raise ToolError("The API key of connection %r belongs to %r, not %r." % (rec.code, user.login,
                                                                                    rec.login))
        return uid

    def _full_support(self):
        """True when every tool pack can run on this connection (app present there)."""
        self.ensure_one()
        rec = self.sudo()
        if rec.kind == "this" or rec.federated:
            return True
        if rec.kind != "local":
            return False
        from odoo.modules.registry import Registry
        try:
            with Registry(rec.db_name).cursor() as cr:
                cr.execute("SELECT 1 FROM ir_module_module WHERE name = %s AND state = 'installed'", [MODULE])
                return bool(cr.fetchone())
        except Exception:  # noqa: BLE001
            return False

    def _run_local(self, server, tool, arguments):
        """Run a tool inside another database of this server, in its own transaction."""
        from odoo.modules.registry import Registry
        rec = self.sudo()
        try:
            registry = Registry(rec.db_name).check_signaling()
        except Exception as e:  # noqa: BLE001
            raise ToolError("Database %r cannot be opened on this server: %s" % (rec.db_name, e))
        pack = PACKS[tool.pack]
        try:
            result = rec._run_local_tx(registry, server, tool, pack, arguments)
        except Exception:
            registry.reset_changes()
            raise
        registry.signal_changes()
        return result

    def _run_local_tx(self, registry, server, tool, pack, arguments):
        from odoo import api as odoo_api
        rec = self
        with registry.cursor() as cr:
            su_env = odoo_api.Environment(cr, SUPERUSER_ID, {})
            uid = rec._local_uid(su_env)
            user = su_env["res.users"].browse(uid)
            env = odoo_api.Environment(cr, uid, {"lang": user.lang or "en_US", "tz": user.tz or "UTC"})
            ctx = ToolContext(LocalOrm(env), env, server.hub_env, rec._describe(), server.principal,
                              server.settings)
            ctx.server = server
            if pack.needs_app and not ctx.orm.installed(MODULE):
                raise ToolError("Database %r does not have the AI Connector app installed, so %s is not "
                                "available there. Basic record tools still work." % (rec.code, tool.name))
            if pack.requires:
                ctx.require_module(pack.requires, pack.title)
            result = tool.func(ctx, **arguments)
            env.flush_all()
        return result

    # ------------------------------------------------------------------ buttons
    def action_test(self):
        for rec in self:
            rec._test_connection()
        if len(self) == 1:
            return {"type": "ir.actions.client", "tag": "display_notification", "params": {
                "type": "success" if self.state == "ok" else "danger",
                "title": self.name, "message": self.state_message, "sticky": False}}
        return True

    def _test_connection(self):
        self.ensure_one()
        vals = {"last_check": fields.Datetime.now()}
        try:
            if self.kind == "this":
                vals.update(state="ok", state_message=_("Connected (this database)."), app_installed=True)
            elif self.kind == "local":
                from odoo import api as odoo_api
                from odoo.modules.registry import Registry
                with Registry(self.db_name).cursor() as cr:
                    env = odoo_api.Environment(cr, SUPERUSER_ID, {})
                    uid = self._local_uid(env)
                    installed = LocalOrm(env).installed(MODULE)
                    vals.update(state="ok", app_installed=installed, state_message=_(
                        "Connected as %(user)s. %(app)s", user=env["res.users"].browse(uid).name,
                        app=_("All tool packs available.") if installed else
                        _("Basic tools only (app not installed there).")))
            else:
                orm = self._remote_orm()
                info = orm.user_info()
                version = ""
                try:
                    import xmlrpc.client
                    version = xmlrpc.client.ServerProxy("%s/xmlrpc/2/common" % orm.url).version()\
                        .get("server_version", "")
                except Exception:  # noqa: BLE001
                    pass
                fed = ""
                if self.federated:
                    res = mcp_server.forward_mcp(self.sudo().mcp_url, self.sudo().mcp_key, "hub_whoami", {})
                    fed = _(" Remote connector OK (%s).", res.get("user") if isinstance(res, dict) else "")
                vals.update(state="ok", remote_version=version, app_installed=self.federated,
                            state_message=_("Connected as %s.", info["name"]) + fed)
        except ToolError as e:
            vals.update(state="error", state_message=str(e))
        except Exception as e:  # noqa: BLE001
            vals.update(state="error", state_message="%s: %s" % (type(e).__name__, e))
        self.write(vals)

    def action_view_logs(self):
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id("nxb_mcp_hub.action_nxb_mcp_log")
        action["domain"] = [("connection_id", "=", self.id)]
        return action

    @api.model
    def _ensure_this(self):
        if not self.with_context(active_test=False).search_count([("kind", "=", "this")]):
            code = re.sub(r"[^a-z0-9]+", "-", self.env.cr.dbname.lower()).strip("-")[:40] or "main"
            if self.with_context(active_test=False).search_count([("code", "=", code)]):
                code = "main"
            self.create({"name": self.env.company.name or self.env.cr.dbname, "code": code, "kind": "this",
                         "sequence": 0, "state": "ok"})
