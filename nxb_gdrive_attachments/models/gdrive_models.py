# -*- coding: utf-8 -*-
# Copyright 2026 Nextbridge. License OPL-1.
import logging
import os
import time
from datetime import timedelta

from odoo import _, api, fields, models

from .drive_client import DriveError

_logger = logging.getLogger(__name__)


class GDriveFolder(models.Model):
    _name = "nxb.gdrive.folder"
    _description = "Google Drive folder"
    _order = "company_id, res_model, res_id"

    name = fields.Char(required=True)
    company_id = fields.Many2one("res.company", required=True, ondelete="cascade", index=True)
    res_model = fields.Char("Document type", required=True, index=True)
    res_id = fields.Integer("Record", index=True, help="0 = folder of the document type")
    folder_id = fields.Char("Drive folder ID", required=True)
    parent_folder_id = fields.Char("Parent folder ID")
    url = fields.Char("Link", compute="_compute_url")
    file_count = fields.Integer("Files", compute="_compute_file_count")

    _sql_constraints = [("folder_uniq", "unique(company_id, res_model, res_id)",
                         "This folder is already registered.")]

    def _compute_url(self):
        for rec in self:
            rec.url = "https://drive.google.com/drive/folders/%s" % rec.folder_id

    def _compute_file_count(self):
        Att = self.env["ir.attachment"].sudo()
        for rec in self:
            rec.file_count = Att.search_count([("gdrive_folder_id", "=", rec.folder_id),
                                               "|", ("res_field", "=", False), ("res_field", "!=", False)])

    @api.model
    def _ensure(self, company, res_model, res_id, name, parent, client):
        folder = self.search([("company_id", "=", company.id), ("res_model", "=", res_model),
                              ("res_id", "=", res_id)], limit=1)
        if folder:
            return folder
        found = client.find_folder(name, parent) if company.gdrive_scope == "full" or \
            company.gdrive_auth == "service" else None
        meta = found or client.create_folder(name, parent)
        # another process may have created it meanwhile: keep the first registration
        self.env.cr.execute("""INSERT INTO nxb_gdrive_folder
                               (name, company_id, res_model, res_id, folder_id, parent_folder_id,
                                create_uid, write_uid, create_date, write_date)
                               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, now() at time zone 'UTC',
                                       now() at time zone 'UTC')
                               ON CONFLICT (company_id, res_model, res_id) DO NOTHING""",
                            [name, company.id, res_model, res_id, meta["id"], parent, self.env.uid, self.env.uid])
        return self.search([("company_id", "=", company.id), ("res_model", "=", res_model),
                            ("res_id", "=", res_id)], limit=1)

    def action_open(self):
        self.ensure_one()
        return {"type": "ir.actions.act_url", "url": self.url, "target": "new"}


class GDriveTrash(models.Model):
    _name = "nxb.gdrive.trash"
    _description = "Google Drive file to delete"
    _order = "id"

    name = fields.Char("File name")
    company_id = fields.Many2one("res.company", required=True, ondelete="cascade")
    file_id = fields.Char("Drive file ID", required=True)
    mode = fields.Selection([("trash", "Trash"), ("delete", "Delete")], default="trash", required=True)
    attempts = fields.Integer()
    next_try = fields.Datetime()
    error = fields.Text()

    def _process(self):
        self.ensure_one()
        Log = self.env["nxb.gdrive.log"].sudo()
        t0 = time.time()
        try:
            client = self.company_id.sudo()._gdrive_client()
            with self.env.cr.savepoint():
                if self.mode == "delete":
                    client.delete(self.file_id)
                else:
                    client.trash(self.file_id)
            Log._add(self.company_id, None, "delete", "ok", name=self.name, duration=time.time() - t0)
            self.unlink()
        except DriveError as e:
            if e.status == 404:  # already gone
                Log._add(self.company_id, None, "delete", "ok", name=self.name, message=_("Already removed."))
                self.unlink()
                return
            attempts = self.attempts + 1 if e.retry else 99
            self.write({"attempts": attempts, "error": str(e)[:2000],
                        "next_try": fields.Datetime.now() + timedelta(minutes=min(2 ** attempts, 360))})
            Log._add(self.company_id, None, "delete", "error", name=self.name, message=str(e))

    def action_retry(self):
        self.write({"attempts": 0, "next_try": False, "error": False})
        self.env["ir.attachment"]._gdrive_trigger()


class GDriveLog(models.Model):
    _name = "nxb.gdrive.log"
    _description = "Google Drive activity"
    _order = "id desc"

    create_date = fields.Datetime("Date", readonly=True)
    company_id = fields.Many2one("res.company", ondelete="cascade", index=True)
    attachment_id = fields.Many2one("ir.attachment", ondelete="set null", index=True)
    name = fields.Char("File")
    res_model = fields.Char("Document type")
    res_id = fields.Integer("Record")
    operation = fields.Selection([("upload", "Upload"), ("update", "Update"), ("rename", "Rename / move"),
                                  ("move", "Move out of Odoo"),
                                  ("delete", "Delete"), ("restore", "Bring back")], required=True)
    status = fields.Selection([("ok", "Done"), ("error", "Error")], required=True, index=True)
    message = fields.Text()
    duration_ms = fields.Integer("Duration (ms)")

    @api.model
    def _add(self, company, attachment, operation, status, message=None, name=None, duration=0):
        self.sudo().create({
            "company_id": company.id if company else False,
            "attachment_id": attachment.id if attachment else False,
            "name": name or (attachment.name if attachment else False),
            "res_model": attachment.res_model if attachment else False,
            "res_id": attachment.res_id if attachment else 0,
            "operation": operation, "status": status, "message": (message or "")[:4000] or False,
            "duration_ms": int(duration * 1000)})

    @api.autovacuum
    def _gc_old_logs(self):
        self.sudo().search([("create_date", "<", fields.Datetime.now() - timedelta(days=90))]).unlink()

    @api.model
    def _prune_cache(self, Attachment):
        """Keep the local cache of moved files under the configured size (largest setting wins)."""
        try:
            path = Attachment._gdrive_cache_dir()
        except OSError:
            return
        limit = max(self.env["res.company"].sudo().search([]).mapped("gdrive_cache_mb") or [200]) * 1024 * 1024
        files = []
        for name in os.listdir(path):
            full = os.path.join(path, name)
            if ".part" in name:
                if os.path.getmtime(full) < time.time() - 3600:
                    os.unlink(full)
                continue
            st = os.stat(full)
            files.append((st.st_mtime, st.st_size, full))
        total = sum(f[1] for f in files)
        for _mtime, size, full in sorted(files):
            if total <= limit:
                break
            try:
                os.unlink(full)
                total -= size
            except OSError:
                pass
