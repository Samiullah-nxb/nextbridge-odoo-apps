# -*- coding: utf-8 -*-
# Copyright 2026 Nextbridge. License OPL-1.
from odoo import api, fields, models

from .ir_attachment import PREFIX


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    gdrive_enabled = fields.Boolean(related="company_id.gdrive_enabled", readonly=False)
    gdrive_auth = fields.Selection(related="company_id.gdrive_auth", readonly=False)
    gdrive_client_id = fields.Char(related="company_id.gdrive_client_id", readonly=False)
    gdrive_client_secret = fields.Char(related="company_id.gdrive_client_secret", readonly=False)
    gdrive_scope = fields.Selection(related="company_id.gdrive_scope", readonly=False)
    gdrive_service_key = fields.Binary(related="company_id.gdrive_service_key", readonly=False)
    gdrive_service_key_name = fields.Char(related="company_id.gdrive_service_key_name", readonly=False)
    gdrive_service_subject = fields.Char(related="company_id.gdrive_service_subject", readonly=False)
    gdrive_account = fields.Char(related="company_id.gdrive_account")
    gdrive_storage = fields.Selection(related="company_id.gdrive_storage", readonly=False)
    gdrive_folder_mode = fields.Selection(related="company_id.gdrive_folder_mode", readonly=False)
    gdrive_shared_drive_id = fields.Char(related="company_id.gdrive_shared_drive_id", readonly=False)
    gdrive_root_folder_id = fields.Char(related="company_id.gdrive_root_folder_id", readonly=False)
    gdrive_model_scope = fields.Selection(related="company_id.gdrive_model_scope", readonly=False)
    gdrive_model_ids = fields.Many2many(related="company_id.gdrive_model_ids", readonly=False)
    gdrive_include_fields = fields.Boolean(related="company_id.gdrive_include_fields", readonly=False)
    gdrive_include_public = fields.Boolean(related="company_id.gdrive_include_public", readonly=False)
    gdrive_min_size = fields.Integer(related="company_id.gdrive_min_size", readonly=False)
    gdrive_delete = fields.Selection(related="company_id.gdrive_delete", readonly=False)
    gdrive_share_link = fields.Boolean(related="company_id.gdrive_share_link", readonly=False)
    gdrive_cache_mb = fields.Integer(related="company_id.gdrive_cache_mb", readonly=False)
    gdrive_restore = fields.Boolean(related="company_id.gdrive_restore")
    gdrive_connected = fields.Boolean(compute="_compute_gdrive_stats")
    gdrive_redirect_uri = fields.Char("Authorized redirect URI", compute="_compute_gdrive_stats")
    gdrive_count_synced = fields.Integer(compute="_compute_gdrive_stats")
    gdrive_count_moved = fields.Integer(compute="_compute_gdrive_stats")
    gdrive_count_waiting = fields.Integer(compute="_compute_gdrive_stats")
    gdrive_count_error = fields.Integer(compute="_compute_gdrive_stats")
    gdrive_moved_mb = fields.Float(compute="_compute_gdrive_stats", digits=(16, 1))

    @api.depends("company_id")
    def _compute_gdrive_stats(self):
        Att = self.env["ir.attachment"].sudo().with_context(active_test=False)
        every = ["|", ("res_field", "=", False), ("res_field", "!=", False)]
        for rec in self:
            c = rec.company_id
            dom = [("company_id", "in", [c.id, False])] + every
            rec.gdrive_connected = c._gdrive_ready()
            rec.gdrive_redirect_uri = c._gdrive_redirect_uri()
            rec.gdrive_count_synced = Att.search_count(dom + [("gdrive_state", "=", "synced")])
            rec.gdrive_count_waiting = Att.search_count(dom + [("gdrive_state", "in", ("pending", "changed",
                                                                                     "renamed", "move"))])
            rec.gdrive_count_error = Att.search_count(dom + [("gdrive_state", "=", "error")])
            moved = Att.search([("store_fname", "=like", "%s%d:%%" % (PREFIX, c.id))] + every)
            rec.gdrive_count_moved = len(moved)
            rec.gdrive_moved_mb = sum(moved.mapped("file_size")) / 1024 / 1024

    def _gdrive_company_action(self, method):
        self.ensure_one()
        return getattr(self.company_id, method)()

    def action_gdrive_connect(self):
        return self._gdrive_company_action("action_gdrive_connect")

    def action_gdrive_disconnect(self):
        return self._gdrive_company_action("action_gdrive_disconnect")

    def action_gdrive_test(self):
        return self._gdrive_company_action("action_gdrive_test")

    def action_gdrive_queue_existing(self):
        return self._gdrive_company_action("action_gdrive_queue_existing")

    def action_gdrive_bring_back(self):
        return self._gdrive_company_action("action_gdrive_bring_back")

    def action_gdrive_sync_now(self):
        return self._gdrive_company_action("action_gdrive_sync_now")

    def action_gdrive_open_folder(self):
        return self._gdrive_company_action("action_gdrive_open_folder")
