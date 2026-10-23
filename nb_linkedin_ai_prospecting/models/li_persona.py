# -*- coding: utf-8 -*-
from datetime import datetime, timedelta

import pytz

from odoo import _, api, fields, models
from odoo.addons.base.models.res_partner import _tz_get

AGENT_STATES = [("running", "Running"), ("paused", "Paused"), ("stopped", "Stopped")]


class LiPersona(models.Model):
    _name = "li.persona"
    _description = "LinkedIn Sales: Persona (audience + agents)"
    _inherit = ["mail.thread"]
    _order = "profile_id, sequence, name"

    name = fields.Char(required=True, tracking=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    profile_id = fields.Many2one("li.profile", "LinkedIn account", required=True, ondelete="cascade", tracking=True)
    service_ids = fields.Many2many("li.service", string="Services")
    company_id = fields.Many2one(related="profile_id.company_id", store=True)

    # Audience
    job_titles = fields.Text(help="One per line, e.g. 'Head of Operations'.")
    locations = fields.Text(help="One per line: countries, regions or cities.")
    keywords = fields.Char(help="Search keywords (industry, technology, ...).")
    industries = fields.Text()
    ideal_customer = fields.Text()
    pain_points = fields.Text()
    exclude_keywords = fields.Char(help="Skip people whose headline contains any of these (comma separated).")
    tz = fields.Selection(_tz_get, string="Target time zone", default=lambda s: s.env.user.tz or "UTC", required=True)
    day_mon = fields.Boolean("Mon", default=True)
    day_tue = fields.Boolean("Tue", default=True)
    day_wed = fields.Boolean("Wed", default=True)
    day_thu = fields.Boolean("Thu", default=True)
    day_fri = fields.Boolean("Fri", default=True)
    day_sat = fields.Boolean("Sat")
    day_sun = fields.Boolean("Sun")
    window_start = fields.Float("Send from", default=10.0)
    window_end = fields.Float("Send until", default=17.0)
    delay_min = fields.Integer("Random delay min (s)", default=90)
    delay_max = fields.Integer("Random delay max (s)", default=300)

    # Connection Agent
    conn_state = fields.Selection(AGENT_STATES, "Connection Agent", default="stopped", tracking=True)
    conn_daily_limit = fields.Integer("Invites per day", default=15)
    conn_weekly_limit = fields.Integer("Invites per week", default=80, help="0 = no weekly limit.")
    conn_total_target = fields.Integer("Total target", default=0, help="Stop after this many invites. 0 = none.")
    conn_search = fields.Boolean("Find people automatically", default=True)
    conn_note = fields.Boolean("Note with invite", default=True)
    conn_note_instructions = fields.Text(
        "Note instructions",
        default="Max 280 characters. Mention one specific thing from their profile. No pitch, no links.")
    conn_follow_if_no_connect = fields.Boolean("Follow when there is no Connect", default=True)
    conn_withdraw_days = fields.Integer("Withdraw invites older than (days)", default=21, help="0 = never.")

    # Chat Agent
    chat_state = fields.Selection(AGENT_STATES, "Chat Agent", default="stopped", tracking=True)
    chat_daily_limit = fields.Integer("Messages per day", default=40)
    step_ids = fields.One2many("li.persona.step", "persona_id", "Questionnaire", copy=True)
    pivot_criteria = fields.Text(
        default="They confirm a concrete need, a timeline or a budget, or ask how we would do it.")
    handoff_message = fields.Text(
        default="Thank them, say a colleague will follow up personally, offer the meeting link if useful.")
    meeting_link = fields.Char()
    tone = fields.Char(default="Warm, concise, peer-to-peer. No buzzwords. Max 3 short sentences.")
    language = fields.Char(default="Same language as the prospect writes in; English otherwise.")

    prospect_count = fields.Integer(compute="_compute_stats")
    invited_count = fields.Integer(compute="_compute_stats")
    accepted_count = fields.Integer(compute="_compute_stats")
    lead_count = fields.Integer(compute="_compute_stats")

    def _compute_stats(self):
        Prospect = self.env["li.prospect"]
        for rec in self:
            rec.prospect_count = Prospect.search_count([("persona_id", "=", rec.id)])
            rec.invited_count = Prospect.search_count([("persona_id", "=", rec.id), ("invited_at", "!=", False)])
            rec.accepted_count = Prospect.search_count([("persona_id", "=", rec.id), ("accepted_at", "!=", False)])
            rec.lead_count = Prospect.search_count([("persona_id", "=", rec.id), ("lead_id", "!=", False)])

    # ------------------------------------------------------------------ windows
    def _local_now(self):
        self.ensure_one()
        return datetime.now(pytz.timezone(self.tz or "UTC"))

    def _working_day(self, local_dt):
        days = [self.day_mon, self.day_tue, self.day_wed, self.day_thu, self.day_fri, self.day_sat, self.day_sun]
        return days[local_dt.weekday()]

    def _in_window(self, at=None):
        self.ensure_one()
        local = (at.astimezone(pytz.timezone(self.tz or "UTC")) if at else self._local_now())
        if not self._working_day(local):
            return False
        hour = local.hour + local.minute / 60.0
        return self.window_start <= hour < self.window_end

    def _local_day_start_utc(self, days_back=0):
        """UTC naive datetime of local midnight (minus days_back) in the persona time zone."""
        self.ensure_one()
        tz = pytz.timezone(self.tz or "UTC")
        local = self._local_now().replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=days_back)
        return local.astimezone(pytz.utc).replace(tzinfo=None)

    # ------------------------------------------------------------------ limits
    def _sent_count(self, kinds, since):
        return self.env["li.queue"].search_count([
            ("persona_id", "=", self.id), ("kind", "in", kinds),
            ("state", "in", ("sent", "sending", "unknown")), ("sent_at", ">=", since)])

    def _invites_left_today(self):
        self.ensure_one()
        if self.conn_state != "running" or self.profile_id._invites_paused():
            return 0
        left = self.conn_daily_limit - self._sent_count(("invite", "follow"), self._local_day_start_utc())
        if self.conn_weekly_limit:
            monday = self._local_day_start_utc(self._local_now().weekday())
            left = min(left, self.conn_weekly_limit - self._sent_count(("invite", "follow"), monday))
        if self.conn_total_target:
            total = self.env["li.prospect"].search_count(
                [("persona_id", "=", self.id), ("invited_at", "!=", False)])
            left = min(left, self.conn_total_target - total)
            if total >= self.conn_total_target:
                self._stop_connection_agent(_("Total target reached"))
                return 0
        return max(left, 0)

    def _messages_left_today(self):
        self.ensure_one()
        if self.chat_state != "running":
            return 0
        sent = self._sent_count(("message", "followup", "closing", "handoff"), self._local_day_start_utc())
        return max(self.chat_daily_limit - sent, 0)

    # ------------------------------------------------------------------ agent buttons
    def _stop_connection_agent(self, reason):
        for rec in self:
            rec.conn_state = "stopped"
            queued = self.env["li.prospect"].search([("persona_id", "=", rec.id), ("stage", "=", "queued")])
            queued.write({"stage": "on_hold"})
            self.env["li.queue"].search([("persona_id", "=", rec.id), ("kind", "=", "invite"),
                                         ("state", "in", ("waiting_text", "ready"))]).write({"state": "dropped",
                                                                                              "error": reason})
            rec.message_post(body=_("Connection Agent stopped: %s. %s people wait On hold.") % (reason, len(queued)))

    def action_conn_run(self):
        for rec in self:
            rec.conn_state = "running"
            self.env["li.prospect"].search([("persona_id", "=", rec.id), ("stage", "=", "on_hold")]).write(
                {"stage": "queued"})

    def action_conn_pause(self):
        self.write({"conn_state": "paused"})

    def action_conn_stop(self):
        self._stop_connection_agent(_("Stopped by %s") % self.env.user.name)

    def action_chat_run(self):
        self.write({"chat_state": "running"})

    def action_chat_pause(self):
        self.write({"chat_state": "paused"})

    def action_chat_stop(self):
        self.write({"chat_state": "stopped"})
        self.env["li.queue"].search([("persona_id", "in", self.ids), ("kind", "in", ("message", "followup")),
                                     ("state", "in", ("waiting_text", "ready"))]).write(
            {"state": "dropped", "error": "Chat Agent stopped"})

    def action_view_prospects(self):
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id("nb_linkedin_ai_prospecting.action_li_prospect")
        action["domain"] = [("persona_id", "=", self.id)]
        action["context"] = {"default_persona_id": self.id}
        return action

    def _ai_context(self):
        self.ensure_one()
        return {
            "persona_id": self.id,
            "persona": self.name,
            "audience": {
                "job_titles": [t.strip() for t in (self.job_titles or "").splitlines() if t.strip()],
                "locations": [t.strip() for t in (self.locations or "").splitlines() if t.strip()],
                "keywords": self.keywords or "",
                "industries": self.industries or "",
                "ideal_customer": self.ideal_customer or "",
                "pain_points": self.pain_points or "",
            },
            "tone": self.tone or "",
            "language": self.language or "",
            "services": [s._ai_context() for s in self.service_ids],
            "meeting_link": self.meeting_link or "",
            "pivot_criteria": self.pivot_criteria or "",
            "questionnaire": [s._ai_context() for s in self.step_ids.sorted("sequence")],
        }


class LiPersonaStep(models.Model):
    _name = "li.persona.step"
    _description = "LinkedIn Sales: Questionnaire step"
    _order = "sequence, id"

    persona_id = fields.Many2one("li.persona", required=True, ondelete="cascade")
    sequence = fields.Integer(default=10)
    name = fields.Char("Goal", required=True, help="What this step should learn or achieve.")
    trigger = fields.Selection([("after_accept", "After accept"), ("after_reply", "After reply")],
                               default="after_reply", required=True)
    delay_hours = fields.Float("Wait (hours)", default=0.0,
                               help="Minimum wait after the trigger before sending. Questions are always "
                                    "answered right away.")
    instructions = fields.Text(help="Guideline for the AI; not a script.")
    template = fields.Text(help="Optional example message. The AI adapts it to the person.")
    is_pivot = fields.Boolean("Pivot step", help="Reaching this step hands the prospect to sales (CRM lead).")

    def _ai_context(self):
        self.ensure_one()
        return {
            "step": self.sequence, "goal": self.name, "trigger": self.trigger,
            "instructions": self.instructions or "", "example": self.template or "",
            "is_pivot": self.is_pivot,
        }
