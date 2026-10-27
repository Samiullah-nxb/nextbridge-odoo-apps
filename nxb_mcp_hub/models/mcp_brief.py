# -*- coding: utf-8 -*-
# Copyright 2026 Nextbridge. License OPL-1.
from odoo import _, api, fields, models


class McpBrief(models.Model):
    _name = "nxb.mcp.brief"
    _description = "AI Connector: brief for the assistant"
    _order = "state, id desc"

    name = fields.Char("Title", required=True)
    kind = fields.Selection([("prospecting", "Prospecting (CRM)"), ("campaign", "Email campaign")],
                            required=True, default="prospecting")
    state = fields.Selection([("draft", "Draft"), ("active", "Ready for the assistant"), ("done", "Done")],
                             default="draft", required=True)
    user_id = fields.Many2one("res.users", "Responsible", default=lambda s: s.env.user)
    goal = fields.Text(required=True, help="What should come out of it, e.g. '20 qualified leads of furniture "
                                           "retailers in Germany' or 'Promote the spring collection'.")
    audience = fields.Text(help="Who: industries, countries, company size, job roles.")
    offer = fields.Text("Offer / message", help="What you sell or announce, key benefits, proof points.")
    exclude = fields.Text("Exclude", help="Who or what to avoid (existing customers, competitors, regions...).")
    tone = fields.Char(default="Friendly and professional")
    products = fields.Text("Products to feature", help="One product name or internal reference per line.")
    call_to_action = fields.Char("Call to action", help="e.g. 'Book a demo', 'Shop the collection'.")
    link = fields.Char("Link", help="Landing page or booking link to use.")
    target_count = fields.Integer("How many leads/contacts", default=20)
    tags = fields.Char("Tags for created records", help="Comma separated, e.g. 'AI prospecting, DE retail'.")
    deadline = fields.Date()
    notes = fields.Text("Extra instructions")
    prompt = fields.Text("Prompt for your assistant", compute="_compute_prompt")
    result_note = fields.Text("What the assistant did", readonly=True)

    @api.depends("kind", "name")
    def _compute_prompt(self):
        for rec in self:
            if not rec.id:
                rec.prompt = False
            elif rec.kind == "prospecting":
                rec.prompt = _("Read brief %(id)s with crm_brief_read, then find and add the leads it asks for "
                               "with crm_lead_add (skip duplicates). Summarise what you created.", id=rec.id)
            else:
                rec.prompt = _("Read brief %(id)s with mail_brief_read, then prepare the email campaign it "
                               "describes as a draft with the campaign tools. Do not send it; show me the result.",
                               id=rec.id)

    def action_ready(self):
        self.write({"state": "active"})

    def action_done(self):
        self.write({"state": "done"})

    def action_draft(self):
        self.write({"state": "draft"})

    def _for_assistant(self):
        self.ensure_one()
        return {
            "brief_id": self.id, "title": self.name, "type": self.kind, "status": self.state,
            "responsible": self.user_id.name, "goal": self.goal, "audience": self.audience or "",
            "offer": self.offer or "", "exclude": self.exclude or "", "tone": self.tone or "",
            "products": [p.strip() for p in (self.products or "").splitlines() if p.strip()],
            "call_to_action": self.call_to_action or "", "link": self.link or "",
            "target_count": self.target_count, "tags": [t.strip() for t in (self.tags or "").split(",") if t.strip()],
            "deadline": fields.Date.to_string(self.deadline) if self.deadline else None,
            "extra_instructions": self.notes or "",
        }
