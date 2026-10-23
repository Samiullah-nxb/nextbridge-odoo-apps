# -*- coding: utf-8 -*-
from odoo import _, api, fields, models

QUEUE_KINDS = [
    ("invite", "Invite"),
    ("follow", "Follow (no Connect)"),
    ("message", "Message"),
    ("followup", "Follow-up"),
    ("closing", "Closing message"),
    ("handoff", "Handoff message"),
    ("withdraw", "Withdraw invite"),
    ("post", "Post"),
]
QUEUE_STATES = [
    ("waiting_text", "Waiting for AI"),
    ("ready", "Ready to send"),
    ("sending", "Sending"),
    ("sent", "Sent"),
    ("failed", "Failed"),
    ("dropped", "Dropped"),
    ("unknown", "Unknown outcome (check)"),
]


class LiQueue(models.Model):
    _name = "li.queue"
    _description = "LinkedIn Sales: Sending queue"
    _order = "scheduled_at, id"

    name = fields.Char(default=lambda s: s.env["ir.sequence"].next_by_code("li.queue") or "/", readonly=True)
    profile_id = fields.Many2one("li.profile", required=True, ondelete="cascade", index=True)
    persona_id = fields.Many2one("li.persona", ondelete="cascade", index=True)
    prospect_id = fields.Many2one("li.prospect", ondelete="cascade", index=True)
    post_id = fields.Many2one("li.post", ondelete="cascade")
    kind = fields.Selection(QUEUE_KINDS, required=True, index=True)
    text = fields.Text()
    intent = fields.Char(help="The AI's reading of the last reply, when this text answers one.")
    state = fields.Selection(QUEUE_STATES, default="ready", required=True, index=True)
    scheduled_at = fields.Datetime(default=fields.Datetime.now, index=True,
                                   help="Not sent before this time; windows and limits also apply.")
    sent_at = fields.Datetime(index=True)
    claimed_at = fields.Datetime(help="AI browser agent took this item.")
    attempts = fields.Integer()
    error = fields.Char()
    after_send = fields.Char(help="Internal follow-up after a successful send (close, handoff, ...).")
    run_id = fields.Many2one("li.ai.run")

    def action_retry(self):
        self.filtered(lambda q: q.state in ("failed", "unknown", "dropped")).write(
            {"state": "ready", "error": False, "scheduled_at": fields.Datetime.now()})

    def action_drop(self):
        self.filtered(lambda q: q.state in ("waiting_text", "ready", "failed")).write(
            {"state": "dropped", "error": _("Dropped by %s") % self.env.user.name})

    def action_mark_sent(self):
        """A person checked LinkedIn and confirms the item went out."""
        for rec in self.filtered(lambda q: q.state == "unknown"):
            self.env["li.agent"]._after_sent(rec, confirmed_by_person=True)
            if rec.prospect_id:
                rec.prospect_id.action_clear_flag()

    def _summary(self):
        self.ensure_one()
        return {
            "queue_id": self.id, "kind": self.kind, "state": self.state,
            "prospect": self.prospect_id.name if self.prospect_id else None,
            "linkedin_url": self.prospect_id.linkedin_url if self.prospect_id else None,
            "text": self.text or "", "scheduled_at": fields.Datetime.to_string(self.scheduled_at),
            "sent_at": fields.Datetime.to_string(self.sent_at) if self.sent_at else None,
            "error": self.error or None,
        }
