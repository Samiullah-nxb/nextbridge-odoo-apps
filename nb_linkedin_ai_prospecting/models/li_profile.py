# -*- coding: utf-8 -*-
import logging
import random
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .li_common import get_int_param, is_odoo_sh, normalize_linkedin_url

_logger = logging.getLogger(__name__)


class LiProfile(models.Model):
    _name = "li.profile"
    _description = "LinkedIn Sales: LinkedIn account"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "name"

    name = fields.Char(required=True, tracking=True)
    linkedin_url = fields.Char("LinkedIn URL", required=True)
    user_id = fields.Many2one("res.users", "Owner", default=lambda s: s.env.user, required=True, tracking=True,
                              help="Receives warm leads, activities and reconnect requests.")
    team_id = fields.Many2one("crm.team", "Sales team")
    active = fields.Boolean(default=True)
    company_id = fields.Many2one("res.company", default=lambda s: s.env.company)
    execution_mode = fields.Selection(
        [("engine", "Built-in engine (own server)"), ("chrome", "AI browser agent")],
        required=True, default=lambda s: "chrome" if is_odoo_sh() else "engine", tracking=True)
    state = fields.Selection(
        [("draft", "Not connected"), ("connected", "Connected"),
         ("reconnect", "Reconnect needed"), ("restricted", "Restricted by LinkedIn")],
        default="draft", required=True, tracking=True)
    engine_state = fields.Selection(
        [("stopped", "Stopped"), ("running", "Running"), ("error", "Error")], default="stopped")
    engine_error = fields.Text()
    engine_port = fields.Integer(groups="base.group_system", copy=False)
    engine_secret = fields.Char(groups="base.group_system", copy=False)
    engine_pid = fields.Integer(groups="base.group_system", copy=False)
    language_ok = fields.Boolean("LinkedIn in English", help="Engine accounts read LinkedIn by its English labels.")
    own_name = fields.Char("Name on LinkedIn", readonly=True)
    own_headline = fields.Char("Headline on LinkedIn", readonly=True)
    last_check = fields.Datetime("Last account check", readonly=True)

    # Budgets and pacing
    daily_page_budget = fields.Integer(default=lambda s: get_int_param(s.env, "daily_page_budget", 150),
                                       help="LinkedIn pages the engine may read per day.")
    pages_today = fields.Integer(readonly=True)
    pages_date = fields.Date(readonly=True)
    next_action_at = fields.Datetime(readonly=True, help="Earliest time for the next send (random delay gap).")
    busy_until = fields.Datetime(readonly=True)
    invites_paused_until = fields.Datetime(tracking=True)
    pause_reason = fields.Char(tracking=True)
    failed_invite_streak = fields.Integer(readonly=True)

    # Background schedule bookkeeping
    last_inbox_read = fields.Datetime(readonly=True)
    last_acceptance_read = fields.Datetime(readonly=True)
    last_invite_verify = fields.Datetime(readonly=True)
    last_search = fields.Datetime(readonly=True)
    last_stats_read = fields.Datetime(readonly=True)

    # Shared agents (per account)
    followup_state = fields.Selection([("running", "Running"), ("paused", "Paused"), ("stopped", "Stopped")],
                                      default="running", string="Follow-up Agent", tracking=True)
    followup_delay_days = fields.Integer(default=4, help="Days of silence before a follow-up.")
    followup_max = fields.Integer(default=2, help="Follow-ups before the person is closed as No response.")
    followup_instructions = fields.Text(default="Short, friendly, add something useful; never guilt-trip.")
    activity_agent_state = fields.Selection([("running", "Running"), ("paused", "Paused"), ("stopped", "Stopped")],
                                      default="running", string="Activity Agent", tracking=True)
    activity_deadline_hours = fields.Integer(default=24, help="Reply deadline for a taken-over prospect's message.")

    persona_ids = fields.One2many("li.persona", "profile_id")
    prospect_ids = fields.One2many("li.prospect", "profile_id")
    prospect_count = fields.Integer(compute="_compute_counts")
    queue_count = fields.Integer(compute="_compute_counts")
    persona_count = fields.Integer(compute="_compute_counts")
    is_odoo_sh = fields.Boolean(compute="_compute_is_odoo_sh")

    _sql_constraints = [("url_uniq", "unique(linkedin_url, company_id)", "This LinkedIn account already exists.")]

    def _compute_is_odoo_sh(self):
        for rec in self:
            rec.is_odoo_sh = is_odoo_sh()

    def _compute_counts(self):
        for rec in self:
            rec.prospect_count = self.env["li.prospect"].search_count([("profile_id", "=", rec.id)])
            rec.queue_count = self.env["li.queue"].search_count(
                [("profile_id", "=", rec.id), ("state", "in", ("waiting_text", "ready"))])
            rec.persona_count = len(rec.persona_ids)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            vals["linkedin_url"] = normalize_linkedin_url(vals.get("linkedin_url"))
            if is_odoo_sh():
                vals["execution_mode"] = "chrome"
        return super().create(vals_list)

    def write(self, vals):
        if vals.get("linkedin_url"):
            vals["linkedin_url"] = normalize_linkedin_url(vals["linkedin_url"])
        if vals.get("execution_mode") == "engine" and is_odoo_sh():
            raise UserError(_("The built-in engine is not available on Odoo.sh. Use AI browser agent."))
        return super().write(vals)

    # ------------------------------------------------------------------ engine
    def _engine(self):
        from ..engine.manager import EngineClient
        self.ensure_one()
        return EngineClient(self.sudo())

    def action_open_connect(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "res_model": "li.connect.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_profile_id": self.id},
        }

    def action_start_engine(self):
        for rec in self:
            if rec.execution_mode != "engine":
                continue
            rec._engine().start()
        return True

    def action_stop_engine(self):
        for rec in self:
            if rec.execution_mode == "engine":
                rec._engine().stop()
        return True

    def action_test_connection(self):
        self.ensure_one()
        if self.execution_mode != "engine":
            raise UserError(_("In AI browser agent mode, the AI checks the account at the start of every run."))
        res = self._engine().call("me", timeout=120)
        self._apply_me(res)
        return {
            "type": "ir.actions.client", "tag": "display_notification",
            "params": {"type": "success" if self.state == "connected" else "warning",
                       "message": _("LinkedIn says you are %s (%s). Language English: %s") % (
                           self.own_name or "?", self.own_headline or "", _("yes") if self.language_ok else _("no"))},
        }

    def _apply_me(self, res):
        self.ensure_one()
        if not res or not res.get("ok"):
            self._handle_engine_problem(res or {})
            return
        data = res.get("data") or {}
        self.write({
            "own_name": data.get("name"),
            "own_headline": data.get("headline"),
            "language_ok": bool(data.get("english", True)),
            "last_check": fields.Datetime.now(),
            "state": "connected",
        })
        if not self.language_ok:
            self._notify_owner(_("Set LinkedIn to English"),
                               _("Engine accounts read LinkedIn by its English labels. Change the interface "
                                 "language in LinkedIn settings, then press Test connection."))

    def _handle_engine_problem(self, res):
        """Translate engine signals into account states. Returns True when the account must pause."""
        self.ensure_one()
        code = res.get("code")
        if code == "session_expired":
            self._mark_reconnect()
            return True
        if code == "restricted":
            self._mark_restricted()
            return True
        if code == "engine_down":
            self.engine_state = "error"
            self.engine_error = res.get("error")
        return False

    def _mark_reconnect(self):
        for rec in self:
            if rec.state != "reconnect":
                rec.state = "reconnect"
                rec._notify_owner(_("Reconnect LinkedIn: %s") % rec.name,
                                  _("LinkedIn ended the session. Agents for this account are paused until you "
                                    "reconnect (LinkedIn Profiles → Reconnect)."))
            if rec.execution_mode == "engine":
                rec._engine().stop()

    def _mark_restricted(self):
        for rec in self:
            rec.state = "restricted"
            rec.persona_ids.write({"conn_state": "paused", "chat_state": "paused"})
            rec._notify_owner(_("LinkedIn restricted %s") % rec.name,
                              _("LinkedIn restricted this account. All its agents are paused. Resolve it on "
                                "LinkedIn, then reconnect and run the agents again."))
            if rec.execution_mode == "engine":
                rec._engine().stop()

    def _notify_owner(self, summary, note):
        self.ensure_one()
        act_type = self.env.ref("nb_linkedin_ai_prospecting.mail_act_li_attention", raise_if_not_found=False)
        self.activity_schedule(
            activity_type_id=act_type.id if act_type else False,
            summary=summary, note=note, user_id=self.user_id.id)

    # ------------------------------------------------------------------ pacing
    def _can_work(self):
        self.ensure_one()
        if not self.active or self.state in ("reconnect", "restricted", "draft"):
            return False
        if self.execution_mode == "engine" and self.engine_state != "running":
            return False
        return True

    def _consume_pages(self, n=1):
        """Count LinkedIn pages read today. Returns False when the daily budget is used up."""
        self.ensure_one()
        today = fields.Date.context_today(self)
        if self.pages_date != today:
            self.write({"pages_date": today, "pages_today": 0})
        if self.pages_today + n > self.daily_page_budget:
            return False
        self.pages_today += n
        return True

    def _pages_left(self, share=1.0):
        self.ensure_one()
        today = fields.Date.context_today(self)
        used = self.pages_today if self.pages_date == today else 0
        return int(self.daily_page_budget * share) - used

    def _bump_next_action(self, persona=None):
        self.ensure_one()
        lo = (persona.delay_min if persona else 60) or 30
        hi = max(lo, (persona.delay_max if persona else 240) or lo)
        self.next_action_at = fields.Datetime.now() + timedelta(seconds=random.randint(lo, hi))

    def _invites_paused(self):
        self.ensure_one()
        return bool(self.invites_paused_until and self.invites_paused_until > fields.Datetime.now())

    def _pause_invites(self, until, reason):
        for rec in self:
            rec.write({"invites_paused_until": until, "pause_reason": reason})
            rec.message_post(body=_("Invites paused until %s: %s") % (until, reason))

    def _any_window_open(self):
        self.ensure_one()
        return any(p._in_window() for p in self.persona_ids)

    def action_resume_invites(self):
        self.write({"invites_paused_until": False, "pause_reason": False, "failed_invite_streak": 0})

    def action_view_prospects(self):
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id("nb_linkedin_ai_prospecting.action_li_prospect")
        action["domain"] = [("profile_id", "=", self.id)]
        return action

    def action_view_queue(self):
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id("nb_linkedin_ai_prospecting.action_li_queue")
        action["domain"] = [("profile_id", "=", self.id)]
        return action

    def _ai_context(self):
        self.ensure_one()
        return {
            "profile_id": self.id,
            "account": self.name,
            "linkedin_url": self.linkedin_url,
            "name_on_linkedin": self.own_name or "",
            "headline": self.own_headline or "",
            "mode": self.execution_mode,
            "state": self.state,
            "invites_paused_until": fields.Datetime.to_string(self.invites_paused_until) if self._invites_paused() else None,
        }
