# -*- coding: utf-8 -*-
# Copyright 2026 Nextbridge. License OPL-1.
import base64
import hashlib
import re

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .drive_client import DriveClient, DriveError, forget_token

PARAM = "nxb_gdrive."


class ResCompany(models.Model):
    _inherit = "res.company"

    gdrive_enabled = fields.Boolean("Store attachments on Google Drive")
    gdrive_auth = fields.Selection([("oauth", "Google account (OAuth)"), ("service", "Service account (Workspace)")],
                                   "Connect with", default="oauth", required=True)
    gdrive_client_id = fields.Char("Client ID", groups="base.group_system")
    gdrive_client_secret = fields.Char("Client secret", groups="base.group_system")
    gdrive_refresh_token = fields.Char("Refresh token", groups="base.group_system", copy=False)
    gdrive_scope = fields.Selection([("app", "Only files created by Odoo (recommended)"),
                                     ("full", "Full Drive (needed to use an existing folder)")],
                                    "Drive access", default="app", required=True)
    gdrive_service_key = fields.Binary("Service account key (.json)", attachment=False, groups="base.group_system",
                                       copy=False)
    gdrive_service_key_name = fields.Char("Key file name", groups="base.group_system")
    gdrive_service_subject = fields.Char("Act as user (optional)", groups="base.group_system",
                                         help="Workspace user to impersonate (domain-wide delegation). Leave empty "
                                              "and use a Shared Drive otherwise.")
    gdrive_account = fields.Char("Connected account", readonly=True, copy=False)
    gdrive_storage = fields.Selection([("mirror", "Copy: keep files in Odoo and put a copy on Drive"),
                                       ("move", "Move: keep files only on Drive (frees Odoo storage)")],
                                      "Storage mode", default="mirror", required=True)
    gdrive_folder_mode = fields.Selection([("single", "One folder for everything"),
                                           ("model", "One folder per document type"),
                                           ("record", "One folder per record (document type / record)")],
                                          "Folder structure", default="model", required=True)
    gdrive_shared_drive_id = fields.Char("Shared Drive ID", help="Optional: store the files in this Shared Drive "
                                                                  "(required for service accounts without "
                                                                  "impersonation).")
    gdrive_root_folder_id = fields.Char("Main folder ID", copy=False,
                                        help="Folder that receives the files. Leave empty: Odoo creates it.")
    gdrive_model_scope = fields.Selection([("all", "All documents"), ("selected", "Only selected document types")],
                                          "Which attachments", default="all", required=True)
    gdrive_model_ids = fields.Many2many("ir.model", "nxb_gdrive_company_model_rel", string="Document types")
    gdrive_include_fields = fields.Boolean("Include images and files stored in fields",
                                           help="Product images, avatars, signatures... Off by default because "
                                                "Odoo displays them very often.")
    gdrive_include_public = fields.Boolean("Include public files (website images)")
    gdrive_min_size = fields.Integer("Minimum size (KB)", default=0)
    gdrive_delete = fields.Selection([("trash", "Move to the Drive trash"), ("delete", "Delete permanently"),
                                      ("keep", "Keep the file on Drive")],
                                     "When deleted in Odoo", default="trash", required=True)
    gdrive_share_link = fields.Boolean("Anyone with the link can view",
                                       help="Makes each uploaded file readable by anyone who has its link.")
    gdrive_cache_mb = fields.Integer("Local cache (MB)", default=200,
                                     help="Moved files that Odoo reads are kept here to avoid downloading them "
                                          "again.")
    gdrive_restore = fields.Boolean("Bringing files back", copy=False)

    # ------------------------------------------------------------------ configuration
    def _gdrive_cfg(self):
        self.ensure_one()
        rec = self.sudo()
        icp = self.env["ir.config_parameter"].sudo()
        key = base64.b64decode(rec.gdrive_service_key).decode() if rec.gdrive_service_key else ""
        return {"auth": rec.gdrive_auth, "client_id": rec.gdrive_client_id, "client_secret": rec.gdrive_client_secret,
                "refresh_token": rec.gdrive_refresh_token, "service_key": key,
                "service_subject": rec.gdrive_service_subject, "scope": rec.gdrive_scope,
                "api_url": icp.get_param(PARAM + "api_url"), "upload_url": icp.get_param(PARAM + "upload_url"),
                "token_url": icp.get_param(PARAM + "token_url"), "auth_url": icp.get_param(PARAM + "auth_url")}

    def _gdrive_cache_key(self):
        cfg = self._gdrive_cfg()
        secret = "%s|%s|%s|%s" % (cfg["auth"], cfg["refresh_token"], cfg["service_key"], cfg["service_subject"])
        return (self.env.cr.dbname, self.id, hashlib.sha1(secret.encode()).hexdigest())

    def _gdrive_client(self):
        self.ensure_one()
        return DriveClient(self._gdrive_cfg(), self._gdrive_cache_key())

    def _gdrive_ready(self):
        self.ensure_one()
        rec = self.sudo()
        if rec.gdrive_auth == "service":
            return bool(rec.gdrive_service_key)
        return bool(rec.gdrive_client_id and rec.gdrive_client_secret and rec.gdrive_refresh_token)

    @api.model
    def _gdrive_redirect_uri(self):
        base = self.env["ir.config_parameter"].sudo().get_param("web.base.url", "").rstrip("/")
        return base + "/nxb_gdrive/oauth/callback"

    def write(self, vals):
        keys = {"gdrive_client_id", "gdrive_client_secret", "gdrive_refresh_token", "gdrive_service_key",
                "gdrive_service_subject", "gdrive_auth"}
        if keys & set(vals):
            for rec in self:
                forget_token(rec._gdrive_cache_key())
        if "gdrive_service_key" in vals and vals["gdrive_service_key"]:
            self._gdrive_check_key(vals["gdrive_service_key"])
        if "gdrive_shared_drive_id" in vals and "gdrive_root_folder_id" not in vals and any(
                (rec.gdrive_shared_drive_id or False) != (vals["gdrive_shared_drive_id"] or False) for rec in self):
            vals["gdrive_root_folder_id"] = False  # a new main folder is created in the new location
        changed_root = "gdrive_root_folder_id" in vals and any(
            (rec.gdrive_root_folder_id or False) != (vals["gdrive_root_folder_id"] or False) for rec in self)
        to_move = self.filtered(lambda c: c.gdrive_storage != "move") if vals.get("gdrive_storage") == "move" \
            else self.browse()
        res = super().write(vals)
        if to_move:
            to_move._gdrive_queue_move()
        if changed_root:
            self.env["nxb.gdrive.folder"].sudo().search([("company_id", "in", self.ids)]).unlink()
        return res

    def _gdrive_check_key(self, value):
        import json
        try:
            data = json.loads(base64.b64decode(value))
            assert data.get("type") == "service_account" and data.get("client_email") and data.get("private_key")
        except Exception:  # noqa: BLE001
            raise UserError(self.env._("This is not a Google service account key file (JSON with type "
                                       "\"service_account\")."))

    # ------------------------------------------------------------------ folders
    def _gdrive_root(self, client=None):
        """Main folder id, created on first use."""
        self.ensure_one()
        rec = self.sudo()
        if rec.gdrive_root_folder_id:
            return rec.gdrive_root_folder_id
        client = client or rec._gdrive_client()
        parent = rec.gdrive_shared_drive_id or "root"
        name = "Odoo - %s" % rec.name
        folder = None
        if rec.gdrive_scope == "full" or rec.gdrive_auth == "service":
            folder = client.find_folder(name, parent)
        folder = folder or client.create_folder(name, parent)
        rec.write({"gdrive_root_folder_id": folder["id"]})
        return folder["id"]

    def _gdrive_folder_for(self, attachment, client):
        """Folder id that should contain ``attachment`` in the configured structure."""
        self.ensure_one()
        root = self._gdrive_root(client)
        mode = self.sudo().gdrive_folder_mode
        if mode == "single":
            return root
        Folder = self.env["nxb.gdrive.folder"].sudo()
        res_model = attachment.res_model or False
        model_label = self.env["ir.model"]._get(res_model).name if res_model and res_model in self.env \
            else "Other files"
        model_folder = Folder._ensure(self, res_model or "__other__", 0, model_label, root, client)
        if mode == "model" or not res_model or not attachment.res_id or res_model not in self.env:
            return model_folder.folder_id
        record = self.env[res_model].sudo().browse(attachment.res_id).exists()
        label = record.display_name if record else "%s" % attachment.res_id
        label = "%s [%d]" % (re.sub(r"[\r\n/]+", " ", label or "")[:80].strip() or res_model, attachment.res_id)
        return Folder._ensure(self, res_model, attachment.res_id, label, model_folder.folder_id, client).folder_id

    # ------------------------------------------------------------------ buttons
    def _gdrive_check_admin(self):
        if not self.env.user.has_group("base.group_system"):
            raise UserError(_("Only administrators can change Google Drive settings."))

    def action_gdrive_connect(self):
        self.ensure_one()
        self._gdrive_check_admin()
        rec = self.sudo()
        if not (rec.gdrive_client_id and rec.gdrive_client_secret):
            raise UserError(_("Enter the Client ID and Client secret of your Google Cloud project first, then save."))
        return {"type": "ir.actions.act_url", "target": "self",
                "url": "/nxb_gdrive/oauth/start?company_id=%d" % self.id}

    def action_gdrive_disconnect(self):
        self._gdrive_check_admin()
        moved = self.env["ir.attachment"].sudo().search_count(
            [("store_fname", "=like", "gdrive:%"), ("company_id", "in", self.ids)])
        if moved:
            raise UserError(_("%(n)s files are stored only on Google Drive. Bring them back to Odoo before "
                              "disconnecting.", n=moved))
        for rec in self:
            forget_token(rec._gdrive_cache_key())
        self.sudo().write({"gdrive_refresh_token": False, "gdrive_account": False, "gdrive_enabled": False})

    def action_gdrive_test(self):
        self.ensure_one()
        self._gdrive_check_admin()
        try:
            client = self._gdrive_client()
            about = client.about()
            if self.sudo().gdrive_shared_drive_id:
                client.shared_drive(self.sudo().gdrive_shared_drive_id)
            root = self._gdrive_root(client)
            client.get(root, "id,name")
        except DriveError as e:
            return self._gdrive_notify(_("Google Drive"), str(e), "danger")
        who = about.get("user") or {}
        account = who.get("emailAddress") or who.get("displayName") or ""
        self.sudo().gdrive_account = account
        quota = about.get("storageQuota") or {}
        used = int(quota.get("usage") or 0) / 1024 ** 3
        limit = int(quota.get("limit") or 0) / 1024 ** 3
        msg = _("Connected as %(a)s. Main folder is ready.", a=account)
        if limit:
            msg += " " + _("Drive space used: %(u).1f of %(l).1f GB.", u=used, l=limit)
        return self._gdrive_notify(_("Google Drive"), msg, "success")

    def action_gdrive_queue_existing(self):
        """Queue the existing attachments that match the settings."""
        self.ensure_one()
        self._gdrive_check_admin()
        if not self.gdrive_enabled:
            raise UserError(_("Switch on Google Drive storage first."))
        Att = self.env["ir.attachment"].sudo().with_context(active_test=False)
        dom = [("type", "=", "binary"), ("gdrive_state", "=", False), ("company_id", "in", [self.id, False]),
               "|", ("res_field", "=", False), ("res_field", "!=", False)]
        n = 0
        for chunk in self.env.cr.split_for_in_conditions(Att.search(dom).ids, 1000):
            atts = Att.browse(chunk).filtered(lambda a: a._gdrive_eligible(self))
            atts._gdrive_set_state("pending")
            n += len(atts)
        if self.sudo().gdrive_storage == "move":
            n += self._gdrive_queue_move()
        self.env["ir.attachment"]._gdrive_trigger()
        return self._gdrive_notify(_("Google Drive"), _("%(n)s attachments queued. They are sent in the "
                                                        "background.", n=n), "info")

    def _gdrive_queue_move(self):
        """Move mode: files already copied to Drive leave Odoo too."""
        Att = self.env["ir.attachment"].sudo().with_context(active_test=False)
        atts = Att.search([("gdrive_state", "=", "synced"), ("store_fname", "not like", "gdrive:%"),
                           ("company_id", "in", self.ids + [False]),
                           "|", ("res_field", "=", False), ("res_field", "!=", False)])
        atts = atts.filtered(lambda a: a._gdrive_company() in self)
        if atts:
            atts._gdrive_set_state("move")
            Att._gdrive_trigger()
        return len(atts)

    def action_gdrive_bring_back(self):
        self._gdrive_check_admin()
        self.sudo().write({"gdrive_restore": True, "gdrive_storage": "mirror"})
        self.env["ir.attachment"]._gdrive_trigger()
        return self._gdrive_notify(_("Google Drive"), _("Files are being copied back into Odoo in the background. "
                                                        "Storage mode is now 'Copy'."), "info")

    def action_gdrive_sync_now(self):
        self._gdrive_check_admin()
        self.env["ir.attachment"]._gdrive_trigger()
        return self._gdrive_notify(_("Google Drive"), _("Synchronisation started."), "info")

    def action_gdrive_open_folder(self):
        self.ensure_one()
        root = self._gdrive_root()
        return {"type": "ir.actions.act_url", "target": "new",
                "url": "https://drive.google.com/drive/folders/%s" % root}

    @staticmethod
    def _gdrive_notify(title, message, kind):
        return {"type": "ir.actions.client", "tag": "display_notification",
                "params": {"title": title, "message": message, "type": kind, "sticky": kind == "danger",
                           "next": {"type": "ir.actions.client", "tag": "soft_reload"}}}
