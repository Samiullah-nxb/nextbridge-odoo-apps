# -*- coding: utf-8 -*-
import hashlib
import json
import logging
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .li_common import (CLOSED_STAGES, STAGES, normalize_linkedin_url, public_id_from_url,
                        text_fingerprint)
from .li_geo import locate

_logger = logging.getLogger(__name__)


class LiProspect(models.Model):
    _name = "li.prospect"
    _description = "LinkedIn Sales: Prospect"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "stage_changed_at desc, id desc"

    name = fields.Char(required=True, tracking=True)
    linkedin_url = fields.Char("LinkedIn URL", required=True, index=True)
    public_id = fields.Char(index=True)
    headline = fields.Char()
    company_name = fields.Char("Company name")
    location = fields.Char()
    country_code = fields.Char(size=2)
    latitude = fields.Float(digits=(9, 4))
    longitude = fields.Float(digits=(9, 4))
    profile_text = fields.Text("LinkedIn profile (kept)", help="Profile as read on LinkedIn; the AI reads it first.")
    profile_read_at = fields.Datetime()

    persona_id = fields.Many2one("li.persona", required=True, ondelete="restrict", tracking=True, index=True)
    profile_id = fields.Many2one(related="persona_id.profile_id", store=True, index=True)
    user_id = fields.Many2one(related="profile_id.user_id", store=True, string="Owner")
    company_id = fields.Many2one(related="profile_id.company_id", store=True)
    stage = fields.Selection(STAGES, default="queued", required=True, tracking=True, index=True,
                             group_expand="_group_expand_stage")
    stage_changed_at = fields.Datetime(default=fields.Datetime.now, index=True)
    color = fields.Integer(compute="_compute_color")

    note_text = fields.Text("Invite note")
    invited_at = fields.Datetime(tracking=True)
    accepted_at = fields.Datetime(tracking=True)
    already_connected = fields.Boolean(help="Was already a connection; not counted in acceptance rate.")
    invite_attempts = fields.Integer()
    invite_seen_in_list = fields.Boolean("Invite seen in Sent list")
    invite_verify_after = fields.Datetime()
    allow_reinvite = fields.Boolean()

    step_index = fields.Integer("Questionnaire steps done", default=0)
    followups_sent = fields.Integer()
    last_inbound_at = fields.Datetime("Last reply", index=True)
    last_outbound_at = fields.Datetime("Last message from us")
    needs_reply = fields.Boolean(index=True)
    conversation_url = fields.Char()
    read_failed = fields.Boolean(help="The conversation could not be read; it is read again next run.")

    taken_over = fields.Boolean(tracking=True)
    takeover_reason = fields.Char(tracking=True)
    prev_stage = fields.Char()
    flag_check = fields.Boolean("Needs a person to check", tracking=True)
    flag_reason = fields.Char()

    lead_id = fields.Many2one("crm.lead", "CRM lead", tracking=True)
    ai_summary = fields.Text("AI summary")
    answers = fields.Text("Questionnaire answers")
    referral_details = fields.Text()
    asked_who = fields.Boolean("Asked who is the right person")

    message_ids_li = fields.One2many("li.message", "prospect_id", "Conversation")
    queue_ids = fields.One2many("li.queue", "prospect_id")
    message_count_li = fields.Integer(compute="_compute_message_count_li")

    _sql_constraints = [
        ("persona_url_uniq", "unique(persona_id, linkedin_url)", "This person is already in this persona."),
    ]

    @api.model
    def _group_expand_stage(self, stages, domain):
        return [key for key, _label in STAGES]

    def _compute_color(self):
        colors = {"queued": 0, "on_hold": 3, "invited": 4, "followed": 8, "connected": 10, "conversation": 2,
                  "taken_over": 9, "lead": 10}
        for rec in self:
            rec.color = colors.get(rec.stage, 1)

    def _compute_message_count_li(self):
        for rec in self:
            rec.message_count_li = len(rec.message_ids_li)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            vals["linkedin_url"] = normalize_linkedin_url(vals.get("linkedin_url"))
            vals.setdefault("public_id", public_id_from_url(vals["linkedin_url"]))
            if vals.get("location") and not vals.get("latitude"):
                vals.update(locate(vals["location"]))
        records = super().create(vals_list)
        for rec in records:
            if rec._is_do_not_contact():
                rec.stage = "do_not_contact"
        return records

    def write(self, vals):
        if "stage" in vals:
            vals.setdefault("stage_changed_at", fields.Datetime.now())
        if vals.get("location") and "latitude" not in vals:
            vals.update(locate(vals["location"]))
        if vals.get("linkedin_url"):
            vals["linkedin_url"] = normalize_linkedin_url(vals["linkedin_url"])
            vals["public_id"] = public_id_from_url(vals["linkedin_url"])
        return super().write(vals)

    def _is_do_not_contact(self):
        """Do-not-contact applies across every persona."""
        self.ensure_one()
        return bool(self.search_count([("public_id", "=", self.public_id), ("stage", "=", "do_not_contact"),
                                       ("id", "!=", self.id)])) if self.public_id else False

    def _set_stage(self, stage, reason=None):
        for rec in self:
            if rec.stage == stage:
                continue
            rec.write({"stage": stage})
            if reason:
                rec.message_post(body=reason)

    # ------------------------------------------------------------------ conversation
    def _add_message(self, direction, body, author, date=None, li_uid=None, is_attachment=False, queue=None):
        """Store a message once. Returns the new li.message or False when it already existed."""
        self.ensure_one()
        date = date or fields.Datetime.now()
        if not li_uid:
            li_uid = hashlib.sha1(("%s|%s|%s" % (direction, text_fingerprint(body), self.id)).encode()).hexdigest()
        if self.env["li.message"].search_count([("prospect_id", "=", self.id), ("li_uid", "=", li_uid)]):
            return False
        msg = self.env["li.message"].create({
            "prospect_id": self.id, "direction": direction, "body": body, "author": author, "date": date,
            "li_uid": li_uid, "is_attachment": is_attachment, "queue_id": queue.id if queue else False,
        })
        if direction == "in":
            vals = {"last_inbound_at": date}
            if self.stage in CLOSED_STAGES and self.stage != "do_not_contact":
                # Closed people are still heard; drop a closing message still waiting.
                self.queue_ids.filtered(lambda q: q.kind == "closing" and q.state in ("waiting_text", "ready")
                                        ).write({"state": "dropped", "error": "Prospect wrote again"})
                vals["stage"] = "conversation"
            elif self.stage in ("connected", "invited", "followed", "queued"):
                vals["stage"] = "conversation"
                if not self.accepted_at and self.stage in ("invited", "queued"):
                    vals["accepted_at"] = date
            if self.taken_over:
                self.env["li.agent"]._activity_for_reply(self, msg)
            else:
                vals["needs_reply"] = True
            # A new reply makes any queued chat text stale.
            self.queue_ids.filtered(lambda q: q.kind in ("message", "followup") and q.state in ("waiting_text", "ready")
                                    and q.create_date < date).write({"state": "dropped",
                                                                     "error": "New reply arrived before sending"})
            self.write(vals)
        else:
            self.write({"last_outbound_at": date})
        return msg

    def _sync_conversation(self, messages, source="engine"):
        """Merge a fresh read of the LinkedIn conversation.

        messages: list of dicts {direction: in|out, text, date (iso, optional), uid (optional), attachment: bool}.
        Detects replies and messages written by hand on LinkedIn (takeover).
        Returns dict(new_inbound=int, manual_outbound=int).
        """
        self.ensure_one()
        new_in = manual = 0
        known_out = {text_fingerprint(m.body) for m in self.message_ids_li if m.direction == "out"}
        for m in messages or []:
            text = (m.get("text") or "").strip()
            if not text:
                continue
            date = fields.Datetime.to_datetime(m.get("date")) if m.get("date") else fields.Datetime.now()
            uid = m.get("uid") or hashlib.sha1(
                ("%s|%s|%s" % (m.get("direction"), text_fingerprint(text), self.id)).encode()).hexdigest()
            if m.get("direction") == "out":
                if text_fingerprint(text) in known_out:
                    continue
                created = self._add_message("out", text, "human_linkedin", date=date, li_uid=uid)
                if created and (self.last_outbound_at or self.invited_at):
                    manual += 1
            else:
                created = self._add_message("in", text, "prospect", date=date, li_uid=uid,
                                            is_attachment=bool(m.get("attachment")))
                if created:
                    new_in += 1
        if manual and not self.taken_over:
            self._take_over(_("A message was written by hand on LinkedIn"))
        self.read_failed = False
        return {"new_inbound": new_in, "manual_outbound": manual}

    # ------------------------------------------------------------------ takeover / CRM
    def _take_over(self, reason):
        for rec in self:
            rec.write({"taken_over": True, "takeover_reason": reason, "prev_stage": rec.stage,
                       "stage": "taken_over" if rec.stage not in ("lead",) else rec.stage, "needs_reply": False})
            rec.queue_ids.filtered(lambda q: q.state in ("waiting_text", "ready") and q.kind != "handoff"
                                   ).write({"state": "dropped", "error": "Taken over"})

    def action_take_over(self):
        self._take_over(_("Taken over by %s") % self.env.user.name)

    def action_return_to_agents(self):
        for rec in self:
            rec.write({"taken_over": False, "takeover_reason": False,
                       "stage": rec.prev_stage if rec.prev_stage and rec.prev_stage != "taken_over" else "conversation"})

    def action_do_not_contact(self):
        for rec in self:
            same = self.search([("public_id", "=", rec.public_id)]) if rec.public_id else rec
            same.write({"stage": "do_not_contact", "needs_reply": False})
            same.queue_ids.filtered(lambda q: q.state in ("waiting_text", "ready")).write(
                {"state": "dropped", "error": "Do not contact"})

    def action_allow_invite_again(self):
        if not self.env.user.has_group("nb_linkedin_ai_prospecting.group_li_manager"):
            raise UserError(_("Only a LinkedIn Sales manager can allow an invite again."))
        for rec in self:
            rec.write({"allow_reinvite": True, "stage": "queued", "invite_seen_in_list": False,
                       "invite_attempts": 0})
            rec.message_post(body=_("Invite allowed again by %s.") % self.env.user.name)

    def action_clear_flag(self):
        self.write({"flag_check": False, "flag_reason": False})

    def action_requeue(self):
        self.filtered(lambda p: p.stage == "on_hold").write({"stage": "queued"})

    def _create_crm_lead(self, summary, instruction, extra=None):
        """Create (or update) the CRM lead with the AI summary and an instruction for the salesperson."""
        self.ensure_one()
        persona = self.persona_id
        tags = persona.service_ids.mapped("tag_ids")
        convo = "\n".join("%s %s: %s" % (fields.Datetime.to_string(m.date), "→" if m.direction == "out" else "←",
                                          m.body) for m in self.message_ids_li.sorted("date")[-8:])
        description = "<p><b>%s</b></p><p>%s</p><p><b>%s</b><br/>%s</p><p><b>%s</b><br/>%s</p><pre>%s</pre>" % (
            _("Instruction for the salesperson"), instruction or "",
            _("AI summary"), (summary or "").replace("\n", "<br/>"),
            _("Answers"), (self.answers or "").replace("\n", "<br/>"), convo)
        if extra:
            description += "<p><b>%s</b><br/>%s</p>" % (_("Details"), extra.replace("\n", "<br/>"))
        vals = {
            "name": "%s – %s" % (self.name, persona.service_ids[:1].name or persona.name),
            "contact_name": self.name,
            "partner_name": self.company_name,
            "function": self.headline,
            "website": self.linkedin_url,
            "description": description,
            "user_id": self.user_id.id,
            "team_id": self.profile_id.team_id.id or False,
            "tag_ids": [(6, 0, tags.ids)],
            "type": "lead",
            "li_prospect_id": self.id,
        }
        if self.lead_id:
            self.lead_id.write({"description": description})
            lead = self.lead_id
        else:
            lead = self.env["crm.lead"].create(vals)
            self.lead_id = lead
        lead.activity_schedule(
            "mail.mail_activity_data_todo", user_id=self.user_id.id,
            summary=_("LinkedIn warm lead: %s") % self.name, note=instruction,
            date_deadline=fields.Date.context_today(self) + timedelta(days=1))
        self.ai_summary = summary
        return lead

    def action_open_linkedin(self):
        self.ensure_one()
        return {"type": "ir.actions.act_url", "url": self.linkedin_url, "target": "new"}

    # ------------------------------------------------------------------ AI context
    def _ai_context(self, max_messages=30):
        self.ensure_one()
        msgs = self.message_ids_li.sorted("date")[-max_messages:]
        steps = self.persona_id.step_ids.sorted("sequence")
        next_step = steps[self.step_index]._ai_context() if self.step_index < len(steps) else None
        return {
            "prospect_id": self.id,
            "name": self.name,
            "linkedin_url": self.linkedin_url,
            "headline": self.headline or "",
            "company": self.company_name or "",
            "location": self.location or "",
            "linkedin_profile": (self.profile_text or "")[:6000],
            "stage": self.stage,
            "questionnaire_steps_done": self.step_index,
            "next_step": next_step,
            "questionnaire_over": next_step is None,
            "followups_sent": self.followups_sent,
            "answers_so_far": self.answers or "",
            "conversation": [
                {"from": "us" if m.direction == "out" else "them", "text": m.body,
                 "date": fields.Datetime.to_string(m.date), "attachment": m.is_attachment}
                for m in msgs
            ],
        }


class LiMessage(models.Model):
    _name = "li.message"
    _description = "LinkedIn Sales: Conversation message"
    _order = "date, id"

    prospect_id = fields.Many2one("li.prospect", required=True, ondelete="cascade", index=True)
    profile_id = fields.Many2one(related="prospect_id.profile_id", store=True)
    direction = fields.Selection([("in", "From prospect"), ("out", "From us")], required=True)
    author = fields.Selection([("ai", "AI agent"), ("human_odoo", "Written in Odoo"),
                               ("human_linkedin", "Written by hand on LinkedIn"), ("prospect", "Prospect")],
                              required=True)
    body = fields.Text(required=True)
    date = fields.Datetime(required=True, default=fields.Datetime.now, index=True)
    li_uid = fields.Char(index=True)
    is_attachment = fields.Boolean()
    analysed = fields.Boolean()
    intent = fields.Char()
    queue_id = fields.Many2one("li.queue")
