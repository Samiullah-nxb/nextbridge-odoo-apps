# -*- coding: utf-8 -*-
from odoo import fields, models


class LiAiRun(models.Model):
    _name = "li.ai.run"
    _description = "LinkedIn Sales: AI run"
    _order = "started_at desc, id desc"

    name = fields.Char(required=True)
    token_id = fields.Many2one("li.connector.token", ondelete="set null")
    mode = fields.Selection([("engine", "Built-in engine"), ("chrome", "AI browser agent")], default="engine")
    profile_ids = fields.Many2many("li.profile")
    started_at = fields.Datetime(default=fields.Datetime.now)
    finished_at = fields.Datetime()
    state = fields.Selection([("running", "Running"), ("done", "Done"), ("error", "With errors")], default="running")
    items_given = fields.Integer("Work items given")
    items_written = fields.Integer("Texts written")
    items_refused = fields.Integer("Texts refused")
    actions_done = fields.Integer("LinkedIn steps done (Chrome)")
    summary = fields.Text()
    errors = fields.Text()


class LiEngineAction(models.Model):
    _name = "li.engine.action"
    _description = "LinkedIn Sales: Engine action"
    _order = "id desc"

    profile_id = fields.Many2one("li.profile", required=True, ondelete="cascade", index=True)
    date = fields.Datetime(default=fields.Datetime.now, index=True)
    action = fields.Char(required=True)
    target = fields.Char()
    status = fields.Selection([("ok", "OK"), ("error", "Error"), ("skipped", "Skipped")], default="ok")
    pages = fields.Integer("Pages read")
    details = fields.Text()
    source = fields.Selection([("engine", "Engine"), ("chrome", "AI browser agent")], default="engine")

    def _gc_old(self, days=60):
        cutoff = fields.Datetime.subtract(fields.Datetime.now(), days=days)
        self.search([("date", "<", cutoff)]).unlink()
