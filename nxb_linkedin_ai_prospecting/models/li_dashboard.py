# -*- coding: utf-8 -*-
from datetime import timedelta

from odoo import api, fields, models

from .li_common import STAGES


class LiDashboard(models.AbstractModel):
    _name = "li.dashboard"
    _description = "LinkedIn Sales: dashboard data"

    @api.model
    def get_data(self, profile_id=False, persona_id=False, days=30):
        Prospect = self.env["li.prospect"]
        Queue = self.env["li.queue"]
        now = fields.Datetime.now()
        since = now - timedelta(days=int(days or 30))
        pdom = []
        if profile_id:
            pdom.append(("profile_id", "=", int(profile_id)))
        if persona_id:
            pdom.append(("persona_id", "=", int(persona_id)))
        qdom = [("profile_id", "=", int(profile_id))] if profile_id else []
        if persona_id:
            qdom.append(("persona_id", "=", int(persona_id)))

        profiles = self.env["li.profile"].search([("id", "=", int(profile_id))] if profile_id else [])
        accounts = []
        for p in profiles:
            accounts.append({
                "id": p.id, "name": p.name, "mode": p.execution_mode, "state": p.state,
                "engine": p.engine_state if p.execution_mode == "engine" else False,
                "pages": "%s / %s" % (p.pages_today if p.pages_date == fields.Date.context_today(self) else 0,
                                      p.daily_page_budget),
                "paused_until": fields.Datetime.to_string(p.invites_paused_until) if p._invites_paused() else False,
                "pause_reason": p.pause_reason if p._invites_paused() else False,
                "ready": Queue.search_count([("profile_id", "=", p.id), ("state", "=", "ready")]),
                "agents": [{"persona": x.name, "conn": x.conn_state, "chat": x.chat_state} for x in p.persona_ids],
            })

        invited = Prospect.search_count(pdom + [("invited_at", ">=", since)])
        accepted = Prospect.search_count(pdom + [("accepted_at", ">=", since), ("already_connected", "=", False),
                                                 ("invited_at", "!=", False)])
        already = Prospect.search_count(pdom + [("already_connected", "=", True)])
        replied = Prospect.search_count(pdom + [("last_inbound_at", ">=", since)])
        leads = Prospect.search_count(pdom + [("lead_id", "!=", False), ("lead_id.create_date", ">=", since)])
        sent_msgs = Queue.search_count(qdom + [("kind", "in", ("message", "followup", "closing", "handoff")),
                                               ("state", "=", "sent"), ("sent_at", ">=", since)])
        kpis = {
            "invited": invited, "accepted": accepted,
            "acceptance_rate": round(100.0 * accepted / invited, 1) if invited else 0,
            "already_connected": already, "replied": replied, "messages_sent": sent_msgs, "leads": leads,
            "reply_rate": round(100.0 * replied / accepted, 1) if accepted else 0,
            "waiting_reply": Prospect.search_count(pdom + [("needs_reply", "=", True)]),
            "flagged": Prospect.search_count(pdom + [("flag_check", "=", True)]),
        }
        groups = Prospect._read_group(pdom, ["stage"], ["__count"])
        counts = {s: c for s, c in groups}
        stages = [{"key": k, "label": label, "count": counts.get(k, 0)} for k, label in STAGES]
        funnel = [
            {"label": "Invited", "count": Prospect.search_count(pdom + [("invited_at", "!=", False)])},
            {"label": "Accepted", "count": Prospect.search_count(pdom + [("accepted_at", "!=", False)])},
            {"label": "Replied", "count": Prospect.search_count(pdom + [("last_inbound_at", "!=", False)])},
            {"label": "Warm lead", "count": Prospect.search_count(pdom + [("lead_id", "!=", False)])},
        ]
        # invites per day (14 days)
        per_day = []
        for i in range(13, -1, -1):
            day = fields.Date.context_today(self) - timedelta(days=i)
            start = fields.Datetime.to_datetime(day)
            per_day.append({"day": fields.Date.to_string(day)[5:],
                            "count": Queue.search_count(qdom + [("kind", "=", "invite"), ("state", "=", "sent"),
                                                                ("sent_at", ">=", start),
                                                                ("sent_at", "<", start + timedelta(days=1))])})
        personas = []
        for x in self.env["li.persona"].search([("profile_id", "=", int(profile_id))] if profile_id else []):
            personas.append({"name": x.name, "account": x.profile_id.name, "invited": x.invited_count,
                             "accepted": x.accepted_count, "leads": x.lead_count})
        last_run = self.env["li.ai.run"].search([], limit=1)
        work_queue = {
            "waiting_for_ai": Prospect.search_count(pdom + [("needs_reply", "=", True), ("taken_over", "=", False)])
            + self.env["li.post"].search_count([("state", "=", "idea")]),
            "ready_to_send": Queue.search_count(qdom + [("state", "=", "ready")]),
            "unknown": Queue.search_count(qdom + [("state", "=", "unknown")]),
            "posts_to_approve": self.env["li.post"].search_count([("state", "=", "draft")]),
        }
        points = [{"id": r["id"], "name": r["name"], "lat": r["latitude"], "lon": r["longitude"],
                   "stage": r["stage"], "location": r["location"] or "", "headline": r["headline"] or ""}
                  for r in Prospect.search_read(pdom + [("latitude", "!=", 0)],
                                                ["name", "latitude", "longitude", "stage", "location", "headline"],
                                                limit=3000)]
        return {
            "accounts": accounts, "kpis": kpis, "stages": stages, "funnel": funnel, "per_day": per_day,
            "personas": personas, "points": points, "work_queue": work_queue,
            "last_run": {"name": last_run.name, "state": last_run.state, "written": last_run.items_written,
                         "given": last_run.items_given,
                         "finished": fields.Datetime.to_string(last_run.finished_at) if last_run.finished_at else False,
                         "errors": last_run.errors or False} if last_run else False,
            "filters": {"profiles": [{"id": p.id, "name": p.name} for p in self.env["li.profile"].search([])],
                        "personas": [{"id": x.id, "name": x.name} for x in self.env["li.persona"].search(
                            [("profile_id", "=", int(profile_id))] if profile_id else [])]},
            "refreshed": fields.Datetime.to_string(now),
        }
