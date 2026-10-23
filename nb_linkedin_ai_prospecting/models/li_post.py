# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import UserError


class LiPost(models.Model):
    _name = "li.post"
    _description = "LinkedIn Sales: Post"
    _inherit = ["mail.thread"]
    _order = "scheduled_at desc, id desc"

    name = fields.Char("Title", required=True)
    profile_id = fields.Many2one("li.profile", "LinkedIn account", required=True, ondelete="cascade")
    service_id = fields.Many2one("li.service")
    idea = fields.Text(required=True, help="What the post should say; the AI drafts from this.")
    text = fields.Text("Post text", tracking=True)
    state = fields.Selection([
        ("idea", "Idea (waiting for AI)"), ("draft", "Draft (to approve)"), ("scheduled", "Approved / scheduled"),
        ("publishing", "Publishing"), ("published", "Published"), ("failed", "Failed")],
        default="idea", required=True, tracking=True, group_expand="_group_expand_state")
    scheduled_at = fields.Datetime("Publish at")
    published_at = fields.Datetime(readonly=True)
    post_url = fields.Char(readonly=True)
    reactions = fields.Integer(readonly=True)
    comments = fields.Integer(readonly=True)
    comment_texts = fields.Text(readonly=True)
    stats_read_at = fields.Datetime(readonly=True)
    error = fields.Char(readonly=True)

    @api.model
    def _group_expand_state(self, states, domain):
        return [k for k, _l in self._fields["state"].selection]

    def action_approve(self):
        for rec in self:
            if not rec.text:
                raise UserError(_("There is no text yet. Wait for the AI draft or write it yourself."))
            rec.write({"state": "scheduled", "scheduled_at": rec.scheduled_at or fields.Datetime.now()})

    def action_back_to_draft(self):
        self.write({"state": "draft"})

    def action_redraft(self):
        self.write({"state": "idea", "text": False})
