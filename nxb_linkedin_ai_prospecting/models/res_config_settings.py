# -*- coding: utf-8 -*-
from odoo import _, api, fields, models

from .li_common import PARAM_PREFIX, is_odoo_sh

PROMPT_RUN_ENGINE = """Run my LinkedIn Sales agents (built-in engine accounts).
1. Call li_overview to see the accounts.
2. Call li_get_work. For every item: read the person's LinkedIn profile first, then the conversation and where it stands, then their last reply and its nature, and only then write. Follow the persona's tone, language, questionnaire guidelines and services. For replies choose the intent.
3. Submit all texts with li_submit_work (same run_id). If it says more_waiting, call li_get_work again.
4. Finish with li_finish_run and a 2-3 line summary of what you wrote and anything a person should look at."""

PROMPT_SEND_NOW = """Send my LinkedIn connections now: call li_send_now. If it returns notes_needed, write those invite notes, submit them with li_submit_work and call li_send_now again. A few minutes later call li_sending_status and tell me who was invited."""

PROMPT_REPLIES_NOW = """Answer my LinkedIn replies now: call li_answer_replies_now, write an answer for every reply item (read profile, conversation, last reply, then write; choose the intent), submit with li_submit_work and finish with li_finish_run."""

PROMPT_SCHEDULED = """(Scheduled task, 1-3 times a day) Run my LinkedIn Sales agents: li_overview, then li_get_work → write every item carefully (profile first, then conversation, then last reply) → li_submit_work until nothing is waiting → li_finish_run with a short summary. Do not ask me questions; skip items you cannot write well and say why in the summary."""

PROMPT_CHROME = """Run my LinkedIn Sales agents in this Chrome (AI browser agent) for LinkedIn account PROFILE_ID.
Rules: Odoo decides what may be done. Never click Send, Connect, Follow, Withdraw or Post unless li_confirm_send answered ok=true for that action_id, and then send exactly the text it returned. Never press Enter inside a message before the full text is typed.
1. Open https://www.linkedin.com/in/me/ and call li_chrome_start(profile_id=PROFILE_ID, seen_url, seen_name). Stop if it says the account does not match.
2. Call li_get_work(profile_id=PROFILE_ID, mode="chrome"), write every item (profile first, then conversation, then last reply), and li_submit_work them.
3. Loop: call li_chrome_next(profile_id=PROFILE_ID) and do exactly what it says, reporting each step with li_chrome_report (pass run_id). For reads, open every conversation/profile it lists in open_these / check_these and report each. If it says write, do step 2 again. If it says wait, wait that many seconds. Stop when it says done or stop.
4. Call li_finish_run with a short summary of what you did."""


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    li_inbox_interval = fields.Integer("Check inbox every (minutes)", default=60,
                                       config_parameter=PARAM_PREFIX + "inbox_interval")
    li_daily_page_budget = fields.Integer("Default daily page budget", default=150,
                                          config_parameter=PARAM_PREFIX + "daily_page_budget")
    li_max_conversation_reads = fields.Integer("Conversations opened per inbox read", default=8,
                                               config_parameter=PARAM_PREFIX + "max_conversation_reads")
    li_engine_status = fields.Char("Engine", compute="_compute_li_engine")
    li_engine_detail = fields.Text("Engine details", compute="_compute_li_engine")
    li_engine_admin_cmd = fields.Text("Administrator command", compute="_compute_li_engine")
    li_is_odoo_sh = fields.Boolean(compute="_compute_li_engine")
    li_prompt_run = fields.Text(compute="_compute_li_prompts")
    li_prompt_send_now = fields.Text(compute="_compute_li_prompts")
    li_prompt_replies = fields.Text(compute="_compute_li_prompts")
    li_prompt_scheduled = fields.Text(compute="_compute_li_prompts")
    li_prompt_chrome = fields.Text(compute="_compute_li_prompts")
    li_token_count = fields.Integer(compute="_compute_li_prompts")

    @api.depends("company_id")
    def _compute_li_engine(self):
        from ..engine.installer import engine_status
        st = engine_status()
        for rec in self:
            rec.li_is_odoo_sh = is_odoo_sh()
            rec.li_engine_status = st["label"]
            rec.li_engine_detail = st.get("detail") or ""
            rec.li_engine_admin_cmd = st.get("admin_command") or ""

    @api.depends("company_id")
    def _compute_li_prompts(self):
        for rec in self:
            rec.li_prompt_run = PROMPT_RUN_ENGINE
            rec.li_prompt_send_now = PROMPT_SEND_NOW
            rec.li_prompt_replies = PROMPT_REPLIES_NOW
            rec.li_prompt_scheduled = PROMPT_SCHEDULED
            rec.li_prompt_chrome = PROMPT_CHROME
            rec.li_token_count = self.env["li.connector.token"].sudo().search_count([])

    def action_li_prepare_engine(self):
        from ..engine.installer import prepare_engine
        prepare_engine()
        return {"type": "ir.actions.client", "tag": "display_notification",
                "params": {"type": "info", "sticky": False,
                           "message": _("Preparing the engine in the background (a few minutes). "
                                        "Press Refresh status until it says Ready."),
                           "next": {"type": "ir.actions.client", "tag": "soft_reload"}}}

    def action_li_refresh(self):
        from ..engine.installer import recheck_browser
        recheck_browser()
        return {"type": "ir.actions.client", "tag": "soft_reload"}

    def action_li_new_connector(self):
        return {"type": "ir.actions.act_window", "res_model": "li.token.wizard", "view_mode": "form",
                "target": "new"}

    def action_li_open_tokens(self):
        return self.env["ir.actions.act_window"]._for_xml_id("nxb_linkedin_ai_prospecting.action_li_token")
