# -*- coding: utf-8 -*-
# Copyright 2026 Nextbridge. License OPL-1.
"""Google Drive storage for ir.attachment.

* Copy mode: the file stays in Odoo and a copy is kept on Drive (link on the attachment).
* Move mode: once uploaded, the attachment's ``store_fname`` becomes ``gdrive:<company>:<file id>:<sha1>`` and
  the local file is released. Odoo's own read hooks (``_file_read``, ``_to_http_stream``) then fetch the file
  from Drive through a local cache, so downloads, previews, e-mails and reports keep working.

Uploads, updates, renames and deletions are queued and done by a scheduled action outside the user's
transaction, so a rolled-back transaction never leaves Drive out of step and nobody waits for Google."""
import hashlib
import logging
import os
import time

from odoo import _, api, fields, models, tools
from odoo.exceptions import UserError
from odoo.http import Stream

from .drive_client import DriveError

_logger = logging.getLogger(__name__)

PREFIX = "gdrive:"
MAX_ATTEMPTS = 6
TECHNICAL_MODELS = {"ir.ui.view", "ir.ui.menu", "ir.module.module", "ir.qweb", "ir.asset", "res.lang",
                    "web_editor.assets", "website.assets", "ir.attachment", "mail.compose.message",
                    "mail.template", "nxb.gdrive.log", "base.module.upgrade", "ir.actions.report"}
STATES = [("pending", "Waiting to upload"), ("changed", "Waiting to update"), ("renamed", "Waiting to rename"),
          ("move", "Waiting to move"), ("synced", "On Google Drive"), ("error", "Error")]


def parse_fname(fname):
    """('gdrive:<company>:<file id>:<sha1>') -> (company_id, file_id, checksum) or None."""
    if not fname or not fname.startswith(PREFIX):
        return None
    try:
        _p, company, file_id, checksum = fname.split(":", 3)
        return int(company), file_id, checksum
    except ValueError:
        return None


class IrAttachment(models.Model):
    _inherit = "ir.attachment"

    gdrive_state = fields.Selection(STATES, "Google Drive", copy=False, index=True, readonly=True)
    gdrive_file_id = fields.Char("Drive file ID", copy=False, readonly=True, index=True)
    gdrive_url = fields.Char("Drive link", copy=False, readonly=True)
    gdrive_folder_id = fields.Char("Drive folder ID", copy=False, readonly=True)
    gdrive_error = fields.Text("Drive error", copy=False, readonly=True)
    gdrive_attempts = fields.Integer(copy=False, readonly=True)
    gdrive_next_try = fields.Datetime(copy=False, readonly=True)
    gdrive_synced_at = fields.Datetime("Last sent to Drive", copy=False, readonly=True)
    gdrive_moved = fields.Boolean("Only on Drive", compute="_compute_gdrive_moved", search="_search_gdrive_moved")

    @api.depends("store_fname")
    def _compute_gdrive_moved(self):
        for att in self:
            att.gdrive_moved = bool(att.store_fname and att.store_fname.startswith(PREFIX))

    def _search_gdrive_moved(self, operator, value):
        positive = (operator == "=") == bool(value)
        return [("store_fname", "=like" if positive else "not like", PREFIX + "%")]

    # ------------------------------------------------------------------ eligibility
    def _gdrive_company(self):
        self.ensure_one()
        return self.company_id or self.env.company

    def _gdrive_eligible(self, company=None):
        self.ensure_one()
        company = company or self._gdrive_company()
        c = company.sudo()
        if not c.gdrive_enabled or not c._gdrive_ready():
            return False
        if self.type != "binary" or not (self.store_fname or self.db_datas) or self.url:
            return False
        if self.res_model in TECHNICAL_MODELS or (self.res_model and self.res_model.startswith("ir.")):
            return False
        if self.res_field and not c.gdrive_include_fields:
            return False
        if self.public and not c.gdrive_include_public:
            return False
        if self.res_model and not self.res_id and not self.res_field:
            return False  # not attached to a record yet (e.g. composer upload); evaluated again when linked
        if c.gdrive_min_size and (self.file_size or 0) < c.gdrive_min_size * 1024:
            return False
        if c.gdrive_model_scope == "selected" and self.res_model not in c.gdrive_model_ids.mapped("model"):
            return False
        return True

    def _gdrive_set_state(self, state, **extra):
        if not self:
            return
        vals = dict(extra, gdrive_state=state)
        if state in ("pending", "changed", "renamed", "move"):
            vals.setdefault("gdrive_attempts", 0)
            vals.setdefault("gdrive_next_try", False)
            vals.setdefault("gdrive_error", False)
        super(IrAttachment, self.sudo().with_context(nxb_gdrive_internal=True)).write(vals)

    @api.model
    def _gdrive_trigger(self):
        cron = self.env.ref("nxb_gdrive_attachments.ir_cron_gdrive_sync", raise_if_not_found=False)
        if cron:
            cron.sudo()._trigger()

    # ------------------------------------------------------------------ ORM hooks
    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        if self.env.context.get("nxb_gdrive_internal"):
            return records
        todo = records.filtered(lambda a: a._gdrive_eligible())
        if todo:
            todo._gdrive_set_state("pending")
            self._gdrive_trigger()
        return records

    def write(self, vals):
        if self.env.context.get("nxb_gdrive_internal"):
            return super().write(vals)
        content = "raw" in vals or "datas" in vals
        meta = "name" in vals
        relink = "res_model" in vals or "res_id" in vals
        before = {a.id: (a.gdrive_state, a.res_model, a.res_id) for a in self} if (content or meta or relink) else {}
        res = super().write(vals)
        if not before or self.env.context.get("nxb_gdrive_restore"):
            return res
        changed = False
        for att in self:
            state, old_model, old_id = before[att.id]
            if att.gdrive_file_id:
                if content:
                    att._gdrive_set_state("changed")
                    changed = True
                elif (meta or (relink and (old_model, old_id) != (att.res_model, att.res_id))) \
                        and state == "synced":
                    att._gdrive_set_state("renamed")
                    changed = True
            elif not state and (content or relink) and att._gdrive_eligible():
                att._gdrive_set_state("pending")
                changed = True
        if changed:
            self._gdrive_trigger()
        return res

    def unlink(self):
        trash = []
        for att in self.sudo():
            if att.gdrive_file_id:
                company = att._gdrive_company().sudo()
                if company.gdrive_delete != "keep":
                    trash.append({"company_id": company.id, "file_id": att.gdrive_file_id, "name": att.name,
                                  "mode": company.gdrive_delete})
        res = super().unlink()
        if trash:
            self.env["nxb.gdrive.trash"].sudo().create(trash)
            self._gdrive_trigger()
        return res

    # ------------------------------------------------------------------ storage hooks
    @api.model
    def _gdrive_cache_dir(self):
        path = os.path.join(tools.config["data_dir"], "nxb_gdrive_cache", self.env.cr.dbname)
        os.makedirs(path, exist_ok=True)
        return path

    @api.model
    def _gdrive_cached_path(self, fname):
        """Local path of a moved file, downloading it from Drive when needed. Returns None on failure."""
        parsed = parse_fname(fname)
        if not parsed:
            return None
        company_id, file_id, checksum = parsed
        path = os.path.join(self._gdrive_cache_dir(), "%s-%s" % (file_id, checksum))
        if os.path.exists(path):
            os.utime(path)
            return path
        company = self.env["res.company"].sudo().browse(company_id).exists()
        if not company:
            _logger.error("Google Drive file %s belongs to a deleted company %s", file_id, company_id)
            return None
        try:
            data = company._gdrive_client().download(file_id)
        except DriveError as e:
            _logger.error("Could not read %s from Google Drive: %s", file_id, e)
            return None
        if hashlib.sha1(data).hexdigest() != checksum:
            _logger.error("Google Drive file %s does not match the content Odoo expects (checksum).", file_id)
            return None
        tmp = path + ".part%d" % os.getpid()
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
        return path

    @api.model
    def _file_read(self, fname):
        if fname and fname.startswith(PREFIX):
            path = self._gdrive_cached_path(fname)
            if not path:
                return b""
            with open(path, "rb") as f:
                return f.read()
        return super()._file_read(fname)

    @api.model
    def _file_delete(self, fname):
        if fname and fname.startswith(PREFIX):
            return  # Drive files are removed by the deletion queue
        return super()._file_delete(fname)

    def _mark_for_gc(self, fname):
        if fname and fname.startswith(PREFIX):
            return
        return super()._mark_for_gc(fname)

    def _to_http_stream(self):
        if self.store_fname and self.store_fname.startswith(PREFIX):
            self.ensure_one()
            path = self._gdrive_cached_path(self.store_fname)
            if not path:
                stream = Stream(mimetype=self.mimetype, download_name=self.name, etag=self.checksum,
                                public=self.public, type="data", data=b"", size=0)
                return stream
            stat = os.stat(path)
            return Stream(type="path", path=path, mimetype=self.mimetype, download_name=self.name,
                          etag=self.checksum, public=self.public, size=stat.st_size,
                          last_modified=stat.st_mtime)
        return super()._to_http_stream()

    # ------------------------------------------------------------------ actions
    def action_gdrive_open(self):
        self.ensure_one()
        if not self.gdrive_url:
            raise UserError(_("This file is not on Google Drive yet."))
        return {"type": "ir.actions.act_url", "url": self.gdrive_url, "target": "new"}

    def action_gdrive_sync(self):
        """Send (again) now: queue and process these attachments immediately."""
        for att in self.sudo():
            if att.gdrive_file_id:
                att._gdrive_set_state("changed" if att.gdrive_state == "error" else (att.gdrive_state or "changed"))
            elif att._gdrive_eligible():
                att._gdrive_set_state("pending")
            else:
                raise UserError(_("'%(n)s' is not sent to Google Drive with the current settings (document "
                                  "type, size, or Google Drive is off for its company).", n=att.name))
        self._gdrive_process(self.ids, commit=False)
        return True

    # ------------------------------------------------------------------ queue processing
    @api.model
    def _cron_gdrive_sync(self, limit=200, time_budget=240):
        self._gdrive_process(limit=limit, time_budget=time_budget, commit=True)

    @api.model
    def _gdrive_process(self, ids=None, limit=200, time_budget=240, commit=True):
        start = time.time()
        Trash = self.env["nxb.gdrive.trash"].sudo()
        for item in Trash.search([("attempts", "<", MAX_ATTEMPTS), "|", ("next_try", "=", False),
                                  ("next_try", "<=", fields.Datetime.now())], limit=limit):
            if time.time() - start > time_budget:
                break
            item._process()
            if commit:
                self.env.cr.commit()
        Att = self.sudo().with_context(active_test=False)
        dom = [("gdrive_state", "in", ("pending", "changed", "renamed", "move", "error")),
               ("gdrive_attempts", "<", MAX_ATTEMPTS),
               "|", ("gdrive_next_try", "=", False), ("gdrive_next_try", "<=", fields.Datetime.now()),
               "|", ("res_field", "=", False), ("res_field", "!=", False)]
        if ids:
            dom.append(("id", "in", list(ids)))
        for att in Att.search(dom, limit=limit, order="gdrive_attempts, id"):
            if time.time() - start > time_budget:
                break
            att._gdrive_sync_one()
            if commit:
                self.env.cr.commit()
        for company in self.env["res.company"].sudo().search([("gdrive_restore", "=", True)]):
            if time.time() - start > time_budget:
                break
            self._gdrive_restore_batch(company, limit, commit)
        if commit:
            self.env["nxb.gdrive.log"].sudo()._prune_cache(self)

    def _gdrive_sync_one(self):
        """Upload / update / rename one attachment. Errors are recorded, never raised."""
        self.ensure_one()
        att = self.sudo()
        company = att._gdrive_company().sudo()
        Log = self.env["nxb.gdrive.log"].sudo()
        op = {"pending": "upload", "changed": "update", "renamed": "rename",
              "move": "move"}.get(att.gdrive_state, "update")
        if att.gdrive_state == "error":
            op = "update" if att.gdrive_file_id else "upload"
        t0 = time.time()
        try:
            if not company._gdrive_ready():
                raise DriveError(_("Google Drive is not connected for %s.", company.name), retry=False)
            client = company._gdrive_client()
            with self.env.cr.savepoint():
                if op == "move":
                    # already on Drive in Copy mode: release the local file if Drive holds the same content
                    if company.gdrive_storage == "move" and not att.gdrive_moved:
                        content = att.raw or b""
                        meta = client.get(att.gdrive_file_id, "id,md5Checksum,webViewLink")
                        if meta.get("md5Checksum") != hashlib.md5(content).hexdigest():
                            meta = client.upload(att.name, content, att.mimetype, file_id=att.gdrive_file_id)
                        att._gdrive_move_out(att.gdrive_file_id, att.checksum)
                    att._gdrive_set_state("synced", gdrive_error=False, gdrive_attempts=0,
                                          gdrive_synced_at=fields.Datetime.now())
                elif op == "rename":
                    folder = company._gdrive_folder_for(att, client)
                    meta = client.rename(att.gdrive_file_id, att.name)
                    if att.gdrive_folder_id and folder != att.gdrive_folder_id:
                        client._call("PATCH", "%s/files/%s" % (client.api, att.gdrive_file_id),
                                     {"addParents": folder, "removeParents": att.gdrive_folder_id, "fields": "id"},
                                     json_body={})
                    att._gdrive_set_state("synced", gdrive_folder_id=folder, gdrive_error=False,
                                          gdrive_synced_at=fields.Datetime.now(), gdrive_attempts=0)
                else:
                    content = att.raw or b""
                    checksum = att.checksum
                    if att.store_fname and att.store_fname.startswith(PREFIX) and not content:
                        raise DriveError(_("The file content could not be read."), retry=True)
                    folder = company._gdrive_folder_for(att, client)
                    meta = None
                    if att.gdrive_file_id:
                        try:
                            meta = client.upload(att.name, content, att.mimetype, file_id=att.gdrive_file_id)
                        except DriveError as e:
                            if e.status != 404:
                                raise
                            meta = None  # deleted on Drive meanwhile: upload again
                    if not meta:
                        meta = client.upload(att.name, content, att.mimetype, parent=folder,
                                             app_props={"odoo_attachment": str(att.id)})
                        if company.gdrive_share_link:
                            client.share_anyone(meta["id"])
                    att._gdrive_set_state("synced", gdrive_file_id=meta["id"], gdrive_url=meta.get("webViewLink"),
                                          gdrive_folder_id=folder, gdrive_error=False, gdrive_attempts=0,
                                          gdrive_next_try=False, gdrive_synced_at=fields.Datetime.now())
                    if company.gdrive_storage == "move":
                        att._gdrive_move_out(meta["id"], checksum)
            Log._add(company, att, op, "ok", duration=time.time() - t0)
        except Exception as e:  # noqa: BLE001
            retry = getattr(e, "retry", True)
            attempts = att.gdrive_attempts + 1
            if not retry:
                attempts = MAX_ATTEMPTS
            delay = min(2 ** attempts, 360)  # minutes
            att._gdrive_set_state("error", gdrive_error=str(e)[:2000], gdrive_attempts=attempts,
                                  gdrive_next_try=fields.Datetime.add(fields.Datetime.now(), minutes=delay))
            Log._add(company, att, op, "error", message=str(e), duration=time.time() - t0)
            if not isinstance(e, DriveError):
                _logger.exception("Google Drive sync of attachment %s failed", att.id)

    def _gdrive_move_out(self, file_id, checksum):
        """Point the attachment to the Drive copy and release the local file."""
        self.ensure_one()
        old = self.store_fname
        new = "%s%d:%s:%s" % (PREFIX, self._gdrive_company().id, file_id, checksum)
        if old == new:
            return
        self.env.cr.execute("UPDATE ir_attachment SET store_fname = %s, db_datas = NULL "
                            "WHERE id = %s AND checksum = %s", [new, self.id, checksum])
        if self.env.cr.rowcount and old and not old.startswith(PREFIX):
            self._mark_for_gc(old)
        self.invalidate_recordset(["store_fname", "db_datas", "raw", "datas", "gdrive_moved"])

    @api.model
    def _gdrive_restore_batch(self, company, limit, commit):
        Att = self.sudo().with_context(active_test=False)
        atts = Att.search([("store_fname", "=like", "%s%d:%%" % (PREFIX, company.id)),
                           "|", ("gdrive_next_try", "=", False), ("gdrive_next_try", "<=", fields.Datetime.now()),
                           "|", ("res_field", "=", False), ("res_field", "!=", False)], limit=limit, order="id")
        Log = self.env["nxb.gdrive.log"].sudo()
        for att in atts:
            data = att.raw
            if not data and att.file_size:
                # try again later; meanwhile the other files keep coming back
                att._gdrive_set_state(att.gdrive_state or "synced", gdrive_error=_("Could not download the file."),
                                      gdrive_next_try=fields.Datetime.add(fields.Datetime.now(), minutes=30))
                Log._add(company, att, "restore", "error", message=_("Could not download the file."))
                continue
            att.with_context(nxb_gdrive_restore=True).write({"raw": data, "mimetype": att.mimetype})
            att._gdrive_set_state(att.gdrive_state or "synced", gdrive_error=False, gdrive_next_try=False)
            Log._add(company, att, "restore", "ok")
            if commit:
                self.env.cr.commit()
        remaining = Att.search_count([("store_fname", "=like", "%s%d:%%" % (PREFIX, company.id)),
                                      "|", ("res_field", "=", False), ("res_field", "!=", False)])
        if not remaining:
            company.gdrive_restore = False
