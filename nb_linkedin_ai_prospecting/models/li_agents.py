# -*- coding: utf-8 -*-
"""The agents: planning, sending, reading LinkedIn and applying results.

Everything LinkedIn-facing goes through `_apply_*` handlers that are shared by the built-in engine
(Odoo drives a browser) and AI browser agent (the AI agent drives the person's Chrome and reports back),
so the rules are enforced by Odoo in both modes.
"""
import logging
from datetime import timedelta

import pytz

from odoo import _, api, fields, models
from odoo.tools import config

from .li_common import (ACTIVE_CHAT_STAGES, CLOSED_STAGES, get_int_param, normalize_linkedin_url,
                        public_id_from_url)

_logger = logging.getLogger(__name__)

CHAT_KINDS = ("message", "followup", "closing", "handoff")
READ_SHARE = 0.6  # inbox + conversation + acceptance reads may use at most 60% of the page budget


class LiAgent(models.AbstractModel):
    _name = "li.agent"
    _description = "LinkedIn Sales: agents"

    # ================================================================== helpers
    def _commit(self):
        if not config["test_enable"] and not getattr(self.env.registry, "in_test_mode", lambda: False)():
            self.env.cr.commit()

    def _log(self, profile, action, target=None, status="ok", pages=0, details=None, source=None):
        self.env["li.engine.action"].sudo().create({
            "profile_id": profile.id, "action": action, "target": target, "status": status, "pages": pages,
            "details": details if isinstance(details, str) or details is None else str(details)[:4000],
            "source": source or profile.execution_mode,
        })

    def _now(self):
        return fields.Datetime.now()

    def _next_monday_utc(self, persona=None):
        tz = pytz.timezone((persona.tz if persona else None) or "UTC")
        local = fields.Datetime.now().replace(tzinfo=pytz.utc).astimezone(tz)
        days = 7 - local.weekday()
        monday = (local + timedelta(days=days)).replace(hour=0, minute=0, second=0, microsecond=0)
        return monday.astimezone(pytz.utc).replace(tzinfo=None)

    def _engine_call(self, profile, action, pages=1, read_share=None, **params):
        """Call the engine for a profile with page budgeting and logging. Returns the result dict."""
        if read_share is not None and profile._pages_left(read_share) < pages:
            return {"ok": False, "code": "budget", "error": "Read budget used for today"}
        if profile._pages_left() < pages:
            return {"ok": False, "code": "budget", "error": "Daily page budget used"}
        try:
            res = profile._engine().call(action, **params)
        except Exception as e:  # noqa: BLE001 - engine problems must never break a cron
            _logger.exception("LinkedIn engine call %s failed", action)
            res = {"ok": False, "code": "engine_down", "error": str(e)}
        used = int(res.get("pages") or (pages if res.get("ok") else 0))
        if used:
            profile._consume_pages(used)
        self._log(profile, action, target=params.get("url") or params.get("keywords"),
                  status="ok" if res.get("ok") else "error", pages=used,
                  details=res.get("error") or res.get("note"))
        if not res.get("ok"):
            profile._handle_engine_problem(res)
        return res

    # ================================================================== planning
    @api.model
    def _cron_plan(self):
        """Create ready invite items when no note is needed; close silent people; withdraw old invites."""
        Persona = self.env["li.persona"].sudo()
        Queue = self.env["li.queue"].sudo()
        Prospect = self.env["li.prospect"].sudo()
        for persona in Persona.search([("conn_state", "=", "running"), ("profile_id.active", "=", True)]):
            if persona.conn_note:
                continue  # notes are written by AI through li_get_work
            pending = Queue.search_count([("persona_id", "=", persona.id), ("kind", "in", ("invite", "follow")),
                                          ("state", "in", ("ready", "sending"))])
            left = persona._invites_left_today() - pending
            if left <= 0:
                continue
            for prospect in Prospect.search([("persona_id", "=", persona.id), ("stage", "=", "queued"),
                                             ("queue_ids", "=", False)], limit=left, order="id"):
                Queue.create({"profile_id": persona.profile_id.id, "persona_id": persona.id,
                              "prospect_id": prospect.id, "kind": "invite", "text": False, "state": "ready"})
        # Withdraw old invites
        for persona in Persona.search([("conn_withdraw_days", ">", 0), ("conn_state", "!=", "stopped")]):
            cutoff = self._now() - timedelta(days=persona.conn_withdraw_days)
            for prospect in Prospect.search([("persona_id", "=", persona.id), ("stage", "=", "invited"),
                                             ("invited_at", "<", cutoff)], limit=10):
                if not prospect.queue_ids.filtered(lambda q: q.kind == "withdraw" and q.state in ("ready", "sending")):
                    Queue.create({"profile_id": persona.profile_id.id, "persona_id": persona.id,
                                  "prospect_id": prospect.id, "kind": "withdraw", "state": "ready"})
        # Silent people after the last follow-up: close as No response
        for profile in self.env["li.profile"].sudo().search([("followup_state", "=", "running")]):
            cutoff = self._now() - timedelta(days=profile.followup_delay_days or 4)
            silent = Prospect.search([
                ("profile_id", "=", profile.id), ("stage", "in", ACTIVE_CHAT_STAGES), ("taken_over", "=", False),
                ("followups_sent", ">=", profile.followup_max), ("last_outbound_at", "<", cutoff),
                ("needs_reply", "=", False)])
            for p in silent:
                if not p.last_inbound_at or p.last_inbound_at < p.last_outbound_at:
                    p._set_stage("closed_no_response", _("Closed: no reply after %s follow-ups.") % p.followups_sent)
        return True

    # ================================================================== sending
    def _pick_next(self, profile, kinds=None):
        """Next queue item allowed to go out now for this account, or an empty recordset."""
        Queue = self.env["li.queue"].sudo()
        domain = [("profile_id", "=", profile.id), ("state", "=", "ready"), ("scheduled_at", "<=", self._now())]
        if kinds:
            domain.append(("kind", "in", kinds))
        for item in Queue.search(domain, limit=80):
            persona = item.persona_id
            prospect = item.prospect_id
            if item.kind == "post":
                return item
            if not persona or not persona._in_window():
                continue
            if prospect and prospect.stage == "do_not_contact":
                item.write({"state": "dropped", "error": "Do not contact"})
                continue
            if item.kind in ("invite", "follow"):
                if persona.conn_state != "running" or persona._invites_left_today() <= 0:
                    continue
            elif item.kind in CHAT_KINDS:
                if prospect.taken_over and item.kind != "handoff":
                    item.write({"state": "dropped", "error": "Taken over"})
                    continue
                if persona.chat_state != "running" and item.kind not in ("handoff", "closing"):
                    continue
                if persona._messages_left_today() <= 0:
                    continue
            return item
        return Queue.browse()

    @api.model
    def _cron_send(self):
        """One LinkedIn action per engine account per tick, with the random delay gap between actions."""
        for profile in self.env["li.profile"].sudo().search([("execution_mode", "=", "engine"),
                                                            ("state", "=", "connected")]):
            if not profile._can_work():
                continue
            now = self._now()
            if (profile.next_action_at and profile.next_action_at > now) or \
                    (profile.busy_until and profile.busy_until > now):
                continue
            item = self._pick_next(profile)
            if not item:
                continue
            try:
                self._execute_engine(profile, item)
            except Exception:  # noqa: BLE001
                _logger.exception("LinkedIn send failed for %s", item.name)
                self.env.cr.rollback()
                item = item.exists()
                if item and item.state == "sending":
                    self._mark_unknown(item, "Unexpected error while sending")
            profile._bump_next_action(item.persona_id)
            profile.busy_until = False
            self._commit()
        return True

    def _execute_engine(self, profile, item):
        prospect = item.prospect_id
        item.write({"state": "sending", "attempts": item.attempts + 1})
        profile.busy_until = self._now() + timedelta(minutes=5)
        self._commit()
        if item.kind == "invite":
            res = self._engine_call(profile, "send_invite", pages=2, url=prospect.linkedin_url,
                                    note=item.text or "", timeout=180)
        elif item.kind == "follow":
            res = self._engine_call(profile, "follow", pages=1, url=prospect.linkedin_url, timeout=120)
        elif item.kind == "withdraw":
            res = self._engine_call(profile, "withdraw_invite", pages=2, url=prospect.linkedin_url, timeout=180)
        elif item.kind == "post":
            res = self._engine_call(profile, "publish_post", pages=1, text=item.text, timeout=240)
        else:
            # Fresh read of the conversation right before every message.
            read = self._engine_call(profile, "read_conversation", pages=1, url=prospect.linkedin_url,
                                     thread_url=prospect.conversation_url or "", timeout=180)
            if not read.get("ok"):
                item.write({"state": "ready", "error": read.get("error")})
                return
            if not self._fresh_read_allows(item, read.get("data") or {}):
                return
            res = self._engine_call(profile, "send_message", pages=1, url=prospect.linkedin_url,
                                    thread_url=prospect.conversation_url or "", text=item.text, timeout=180)
        self._apply_send_result(item, res)

    def _fresh_read_allows(self, item, data):
        """Merge a fresh conversation read; drop the item if a new reply arrived or a person took over."""
        prospect = item.prospect_id
        if data.get("thread_url"):
            prospect.conversation_url = data["thread_url"]
        prospect._sync_conversation(data.get("messages") or [])
        newer_reply = prospect.last_inbound_at and prospect.last_inbound_at > item.create_date
        if prospect.taken_over and item.kind != "handoff":
            item.write({"state": "dropped", "error": "Taken over (fresh read)"})
            return False
        if newer_reply and item.kind in ("message", "followup"):
            item.write({"state": "dropped", "error": "New reply arrived; the AI will answer it"})
            return False
        return True

    def _apply_send_result(self, item, res):
        """Shared by engine and Chrome. res: {ok, data: {result: ...}, code, error}."""
        profile = item.profile_id
        prospect = item.prospect_id
        data = res.get("data") or {}
        result = data.get("result") or ("sent" if res.get("ok") else None)
        if not res.get("ok") and res.get("code") in ("session_expired", "restricted", "engine_down", "budget"):
            item.write({"state": "ready", "error": res.get("error") or res.get("code")})
            return
        if result == "unknown" or res.get("code") == "unknown":
            self._mark_unknown(item, res.get("error") or "The send could not be confirmed")
            return
        if item.kind == "invite":
            self._apply_invite_result(item, result, res)
            return
        if item.kind == "follow":
            if result in ("sent", "followed"):
                item.write({"state": "sent", "sent_at": self._now()})
                prospect._set_stage("followed", _("Followed: LinkedIn offers no Connect for this person."))
            else:
                item.write({"state": "failed", "error": res.get("error") or result})
            return
        if item.kind == "withdraw":
            if result in ("sent", "withdrawn", "not_pending"):
                item.write({"state": "sent", "sent_at": self._now()})
                prospect._set_stage("closed_withdrawn", _("Invite withdrawn after %s days.")
                                    % item.persona_id.conn_withdraw_days)
            else:
                item.write({"state": "failed", "error": res.get("error") or result})
            return
        if item.kind == "post":
            post = item.post_id
            if res.get("ok"):
                item.write({"state": "sent", "sent_at": self._now()})
                post.write({"state": "published", "published_at": self._now(), "post_url": data.get("url")})
            else:
                item.write({"state": "failed", "error": res.get("error")})
                post.write({"state": "failed", "error": res.get("error")})
            return
        # chat kinds
        if result == "sent":
            self._after_sent(item)
        elif result == "not_connected":
            item.write({"state": "failed", "error": "Not connected (no Message button)"})
            prospect.write({"flag_check": True, "flag_reason": "Message button missing"})
        else:
            if item.attempts >= 3:
                item.write({"state": "failed", "error": res.get("error") or result})
            else:
                item.write({"state": "ready", "error": res.get("error") or result,
                            "scheduled_at": self._now() + timedelta(minutes=30)})

    def _apply_invite_result(self, item, result, res):
        prospect = item.prospect_id
        profile = item.profile_id
        persona = item.persona_id
        now = self._now()
        if result in ("sent", "pending_already"):
            item.write({"state": "sent", "sent_at": now})
            prospect.write({"stage": "invited", "invited_at": prospect.invited_at or now,
                            "invite_attempts": prospect.invite_attempts + 1,
                            "invite_verify_after": now + timedelta(minutes=10),
                            "invite_seen_in_list": result == "pending_already", "allow_reinvite": False})
            if res.get("data", {}).get("profile_text") and not prospect.profile_text:
                prospect.write({"profile_text": res["data"]["profile_text"], "profile_read_at": now})
        elif result == "connected_already":
            item.write({"state": "dropped", "error": "Already connected"})
            prospect.write({"stage": "connected", "accepted_at": now, "already_connected": True})
        elif result in ("no_connect", "email_required"):
            if persona.conn_follow_if_no_connect:
                item.write({"kind": "follow", "state": "ready", "error": result, "scheduled_at": now})
            else:
                item.write({"state": "failed", "error": result})
                prospect.write({"flag_check": True, "flag_reason": _("LinkedIn offers no Connect (%s)") % result})
        elif result == "weekly_limit":
            item.write({"state": "ready", "error": "LinkedIn weekly invitation limit"})
            profile._pause_invites(self._next_monday_utc(persona), _("LinkedIn weekly invitation limit reached"))
        else:
            item.write({"state": "failed", "error": res.get("error") or result or "unknown error"})

    def _after_sent(self, item, confirmed_by_person=False):
        prospect = item.prospect_id
        now = self._now()
        item.write({"state": "sent", "sent_at": item.sent_at or now})
        if item.kind not in CHAT_KINDS:
            return
        prospect._add_message("out", item.text, "ai", date=now, queue=item)
        vals = {"needs_reply": False}
        if prospect.stage == "connected":
            vals["stage"] = "conversation"
        if item.kind == "followup":
            vals["followups_sent"] = prospect.followups_sent + 1
        after = item.after_send or ""
        if "advance" in after:
            vals["step_index"] = prospect.step_index + 1
        prospect.write(vals)
        for action in after.split(";"):
            if action.startswith("close:"):
                prospect._set_stage(action[6:])
            elif action == "takeover":
                prospect._take_over(_("Handed to sales"))

    def _mark_unknown(self, item, reason):
        item.write({"state": "unknown", "error": reason})
        if item.prospect_id:
            item.prospect_id.write({"flag_check": True,
                                    "flag_reason": _("Send outcome unknown (%s); check LinkedIn") % item.kind})

    # ================================================================== reading LinkedIn (engine)
    @api.model
    def _cron_read(self):
        """Background reads for engine accounts: acceptances, inbox, invite checks, profiles, search."""
        for profile in self.env["li.profile"].sudo().search([("execution_mode", "=", "engine"),
                                                            ("state", "=", "connected")]):
            if not profile._can_work() or (profile.busy_until and profile.busy_until > self._now()):
                continue
            profile.busy_until = self._now() + timedelta(minutes=10)
            self._commit()
            try:
                for step in self._due_reads(profile):
                    getattr(self, "_engine_" + step)(profile)
                    self._commit()
                    if profile.state != "connected":
                        break
            except Exception:  # noqa: BLE001
                _logger.exception("LinkedIn background read failed for %s", profile.name)
                self.env.cr.rollback()
            profile = profile.exists()
            if profile:
                profile.busy_until = False
                self._commit()
        return True

    def _due_reads(self, profile):
        """Ordered list of read steps due now for an account (shared by engine and Chrome)."""
        now = self._now()
        steps = []
        Prospect = self.env["li.prospect"].sudo()
        pending_invites = Prospect.search_count([("profile_id", "=", profile.id), ("stage", "=", "invited")])
        window = profile._any_window_open()
        inbox_every = get_int_param(self.env, "inbox_interval", 60) if window else 60
        if profile.persona_ids and (not profile.last_inbox_read
                                    or profile.last_inbox_read < now - timedelta(minutes=inbox_every)):
            steps.append("inbox")
        if pending_invites and (not profile.last_acceptance_read
                                or profile.last_acceptance_read < now - timedelta(minutes=15)):
            steps.append("acceptances")
        due_verify = Prospect.search_count([("profile_id", "=", profile.id), ("stage", "=", "invited"),
                                            ("invite_verify_after", "<=", now), ("invite_seen_in_list", "=", False)])
        if due_verify or (pending_invites and (not profile.last_invite_verify
                                               or profile.last_invite_verify < now - timedelta(hours=24))):
            steps.append("verify_invites")
        searching = any(self._persona_needs_people(p) for p in profile.persona_ids) and \
            (not profile.last_search or profile.last_search < now - timedelta(hours=1))
        if searching:
            steps.append("search")
        # after search, so people it just found get their profile read in the same run
        if searching or Prospect.search_count(self._profiles_to_read_domain(profile)):
            steps.append("read_profiles")
        if not profile.last_stats_read or profile.last_stats_read < now - timedelta(days=7):
            if self.env["li.post"].sudo().search_count([("profile_id", "=", profile.id), ("state", "=", "published"),
                                                        ("post_url", "!=", False)]):
                steps.append("post_stats")
        return steps

    def _profiles_to_read_domain(self, profile):
        return [("profile_id", "=", profile.id), ("profile_text", "=", False),
                ("stage", "in", ("queued", "invited", "connected", "conversation")),
                ("persona_id.conn_state", "!=", "stopped")]

    def _persona_needs_people(self, persona):
        if persona.conn_state != "running" or not persona.conn_search:
            return False
        queued = self.env["li.prospect"].sudo().search_count([("persona_id", "=", persona.id), ("stage", "=", "queued")])
        return queued < max(persona.conn_daily_limit * 2, 10)

    # --- engine read steps -------------------------------------------------
    def _engine_inbox(self, profile):
        res = self._engine_call(profile, "read_inbox", pages=1, read_share=READ_SHARE, limit=40, timeout=180)
        profile.last_inbox_read = self._now()
        if not res.get("ok"):
            return
        threads = (res.get("data") or {}).get("threads") or []
        for prospect, thread in self._apply_inbox(profile, threads):
            r = self._engine_call(profile, "read_conversation", pages=1, read_share=READ_SHARE,
                                  url=prospect.linkedin_url, thread_url=thread.get("thread_url") or "", timeout=180)
            if r.get("ok"):
                self._apply_conversation(prospect, r.get("data") or {})
            else:
                prospect.read_failed = True
            self._commit()

    def _engine_acceptances(self, profile):
        res = self._engine_call(profile, "recent_connections", pages=1, read_share=READ_SHARE, limit=40, timeout=120)
        profile.last_acceptance_read = self._now()
        if res.get("ok"):
            self._apply_acceptances(profile, (res.get("data") or {}).get("people") or [])

    def _engine_verify_invites(self, profile):
        res = self._engine_call(profile, "sent_invitations", pages=3, limit=200, timeout=240)
        profile.last_invite_verify = self._now()
        if not res.get("ok"):
            return
        data = res.get("data") or {}
        for prospect in self._apply_sent_invitations(profile, data.get("people") or [], bool(data.get("complete"))):
            r = self._engine_call(profile, "read_profile", pages=1, url=prospect.linkedin_url, timeout=120)
            if r.get("ok"):
                self._apply_profile_check(prospect, r.get("data") or {})
            self._commit()

    def _engine_read_profiles(self, profile):
        for prospect in self.env["li.prospect"].sudo().search(self._profiles_to_read_domain(profile), limit=5,
                                                              order="stage desc, id"):
            r = self._engine_call(profile, "read_profile", pages=1, url=prospect.linkedin_url, timeout=120)
            if not r.get("ok"):
                if r.get("code") == "not_found":
                    prospect.write({"profile_text": "(profile not available)", "flag_check": True,
                                    "flag_reason": "Profile not found on LinkedIn"})
                break
            self._apply_profile(prospect, r.get("data") or {})
            self._commit()

    def _engine_search(self, profile):
        profile.last_search = self._now()
        for persona in profile.persona_ids.filtered(self._persona_needs_people):
            query = self._search_query(persona)
            r = self._engine_call(profile, "search_people", pages=2, keywords=query["keywords"],
                                  location=query["location"], limit=25, timeout=240)
            if r.get("ok"):
                self._apply_search(persona, (r.get("data") or {}).get("people") or [])
            self._commit()

    def _engine_post_stats(self, profile):
        profile.last_stats_read = self._now()
        posts = self.env["li.post"].sudo().search([("profile_id", "=", profile.id), ("state", "=", "published"),
                                                   ("post_url", "!=", False)], limit=5, order="published_at desc")
        for post in posts:
            r = self._engine_call(profile, "post_stats", pages=1, url=post.post_url, timeout=120)
            if r.get("ok"):
                self._apply_post_stats(post, r.get("data") or {})

    def _search_query(self, persona):
        titles = [t.strip() for t in (persona.job_titles or "").splitlines() if t.strip()]
        locations = [t.strip() for t in (persona.locations or "").splitlines() if t.strip()]
        # rotate titles/locations so each search looks at a different slice
        n = self.env["li.engine.action"].sudo().search_count([("profile_id", "=", persona.profile_id.id),
                                                              ("action", "=", "search_people")])
        title = titles[n % len(titles)] if titles else ""
        location = locations[(n // max(len(titles), 1)) % len(locations)] if locations else ""
        keywords = " ".join(x for x in (title, persona.keywords or "") if x).strip()
        return {"keywords": keywords, "location": location, "page": (n // max(len(titles) * max(len(locations), 1), 1)) % 5 + 1}

    # ================================================================== apply handlers (shared)
    def _match_prospect(self, profile, url=None, name=None):
        Prospect = self.env["li.prospect"].sudo()
        pid = public_id_from_url(url) if url else False
        if pid:
            p = Prospect.search([("profile_id", "=", profile.id), ("public_id", "=", pid)],
                                order="stage_changed_at desc", limit=1)
            if p:
                return p
        if name:
            return Prospect.search([("profile_id", "=", profile.id), ("name", "=ilike", name.strip())],
                                   order="stage_changed_at desc", limit=1)
        return Prospect.browse()

    def _apply_inbox(self, profile, threads, max_reads=None):
        """Decide which conversations to open. Returns [(prospect, thread)] in priority order, capped."""
        max_reads = max_reads or get_int_param(self.env, "max_conversation_reads", 8)
        buckets = {0: [], 1: [], 2: []}
        for t in threads:
            p = self._match_prospect(profile, t.get("profile_url"), t.get("name"))
            if not p or p.stage == "do_not_contact":
                continue
            if t.get("thread_url"):
                p.conversation_url = t["thread_url"]
            last_text = (t.get("last_text") or "").strip()
            known = p.message_ids_li.sorted("date")[-1:] if p.message_ids_li else None
            changed = p.read_failed or t.get("unread") or not known or \
                (last_text and last_text[:60] not in (known.body or "")) or \
                (not t.get("last_from_me") and p.stage in ("invited", "connected", "followed"))
            if not changed:
                continue
            if p.stage in ACTIVE_CHAT_STAGES or p.stage in ("taken_over", "lead"):
                buckets[0].append((p, t))
            elif p.stage in ("invited", "followed", "queued"):
                buckets[1].append((p, t))
            else:
                buckets[2].append((p, t))
        ordered = buckets[0] + buckets[1] + buckets[2]
        return ordered[:max_reads]

    def _apply_conversation(self, prospect, data):
        if data.get("thread_url"):
            prospect.conversation_url = data["thread_url"]
        res = prospect._sync_conversation(data.get("messages") or [])
        if prospect.stage == "invited" and any(m.get("direction") == "in" for m in data.get("messages") or []):
            prospect.write({"accepted_at": prospect.accepted_at or self._now()})
        return res

    def _apply_acceptances(self, profile, people):
        n = 0
        for person in people:
            p = self._match_prospect(profile, person.get("url"), person.get("name"))
            if not p:
                continue
            if p.stage in ("invited", "followed"):
                p.write({"stage": "connected", "accepted_at": self._now()})
                n += 1
            elif p.stage == "queued":
                p.write({"stage": "connected", "accepted_at": self._now(), "already_connected": True})
                p.queue_ids.filtered(lambda q: q.kind in ("invite", "follow") and q.state == "ready").write(
                    {"state": "dropped", "error": "Already connected"})
        return n

    def _apply_sent_invitations(self, profile, people, complete):
        """Verify invites against LinkedIn's Sent invitations list (only invitation rows count).

        Returns prospects whose profile must be opened to decide (pending / accepted / never recorded / declined).
        """
        Prospect = self.env["li.prospect"].sudo()
        listed = {public_id_from_url(p.get("url")) for p in people if p.get("url")}
        listed_names = {(p.get("name") or "").strip().lower() for p in people}
        to_check = Prospect.browse()
        now = self._now()
        for p in Prospect.search([("profile_id", "=", profile.id), ("stage", "=", "invited")]):
            if p.public_id in listed or p.name.strip().lower() in listed_names:
                if not p.invite_seen_in_list:
                    p.invite_seen_in_list = True
                    self._log(profile, "verify_invite", p.linkedin_url, details="source: invitation rows; pending")
                continue
            if not complete and not p.invite_seen_in_list:
                continue  # list was cut short; cannot conclude
            if p.invite_seen_in_list or (p.invite_verify_after and p.invite_verify_after <= now):
                to_check |= p
        return to_check[:5]

    def _apply_profile_check(self, prospect, data):
        """After an invite vanished from the Sent list: Pending / Message / Connect again."""
        profile = prospect.profile_id
        state = data.get("connect_state")
        if data.get("text") and not prospect.profile_text:
            self._apply_profile(prospect, data)
        if state == "pending":
            prospect.invite_seen_in_list = True
            self._log(profile, "verify_invite", prospect.linkedin_url, details="profile shows Pending")
        elif state == "message" or state == "connected":
            prospect.write({"stage": "connected", "accepted_at": prospect.accepted_at or self._now()})
            self._log(profile, "verify_invite", prospect.linkedin_url, details="profile shows Message: accepted")
        elif state == "connect":
            if prospect.invite_seen_in_list:
                prospect._set_stage("closed_declined", _("Invite declined or ignored: it left the Sent list and "
                                                         "the profile offers Connect again."))
            else:
                prospect.write({"stage": "queued", "invited_at": False})
                self._log(profile, "verify_invite", prospect.linkedin_url,
                          details="LinkedIn never recorded the invite; back to Queued")
                profile.failed_invite_streak += 1
                if profile.failed_invite_streak >= 2:
                    profile._pause_invites(self._now() + timedelta(hours=24),
                                           _("LinkedIn did not record two invitations in a row"))
                    profile.failed_invite_streak = 0
                return
        profile.failed_invite_streak = 0

    def _apply_profile(self, prospect, data):
        vals = {"profile_read_at": self._now(), "profile_text": data.get("text") or "(empty profile)"}
        for src, dst in (("headline", "headline"), ("company", "company_name"), ("location", "location")):
            if data.get(src) and not prospect[dst]:
                vals[dst] = data[src]
        if data.get("name") and prospect.name in (False, "", "LinkedIn member"):
            vals["name"] = data["name"]
        prospect.write(vals)

    def _apply_search(self, persona, people):
        Prospect = self.env["li.prospect"].sudo()
        excludes = [x.strip().lower() for x in (persona.exclude_keywords or "").split(",") if x.strip()]
        added = 0
        for person in people:
            url = normalize_linkedin_url(person.get("url"))
            pid = public_id_from_url(url)
            if not pid or (person.get("degree") in ("1st", 1)):
                continue
            headline = (person.get("headline") or "").lower()
            if any(x in headline for x in excludes):
                continue
            if Prospect.search_count([("public_id", "=", pid), "|", ("profile_id", "=", persona.profile_id.id),
                                      ("stage", "=", "do_not_contact")]):
                continue
            Prospect.create({"name": person.get("name") or pid, "linkedin_url": url, "headline": person.get("headline"),
                             "location": person.get("location"), "persona_id": persona.id, "stage": "queued"})
            added += 1
        if added:
            persona.message_post(body=_("Search added %s new people to the queue.") % added)
        return added

    def _apply_post_stats(self, post, data):
        post.write({"reactions": int(data.get("reactions") or 0), "comments": int(data.get("comments") or 0),
                    "comment_texts": "\n---\n".join(data.get("comment_texts") or []), "stats_read_at": self._now()})

    # ================================================================== activity agent
    def _activity_for_reply(self, prospect, msg):
        profile = prospect.profile_id
        if profile.activity_agent_state != "running":
            return
        deadline = fields.Date.context_today(prospect) + timedelta(
            days=max(1, round((profile.activity_deadline_hours or 24) / 24.0)))
        prospect.activity_schedule(
            "mail.mail_activity_data_todo", user_id=profile.user_id.id, date_deadline=deadline,
            summary=_("Reply on LinkedIn to %s") % prospect.name,
            note=_("New message (you took over this prospect): %s") % (msg.body or "")[:500])

    # ================================================================== posts
    @api.model
    def _cron_posts(self):
        Queue = self.env["li.queue"].sudo()
        for post in self.env["li.post"].sudo().search([("state", "=", "scheduled"),
                                                       ("scheduled_at", "<=", self._now())]):
            if not Queue.search_count([("post_id", "=", post.id), ("state", "in", ("ready", "sending"))]):
                Queue.create({"profile_id": post.profile_id.id, "post_id": post.id, "kind": "post",
                              "text": post.text, "state": "ready"})
                post.state = "publishing"
        return True

    # ================================================================== watchdog / gc
    @api.model
    def _cron_watchdog(self):
        for profile in self.env["li.profile"].sudo().search([("execution_mode", "=", "engine"),
                                                            ("state", "=", "connected")]):
            client = profile._engine()
            if not client.is_alive():
                try:
                    client.start()
                    self._log(profile, "watchdog_restart", details="Engine restarted by watchdog")
                except Exception as e:  # noqa: BLE001
                    profile.write({"engine_state": "error", "engine_error": str(e)})
            elif profile.engine_state != "running":
                profile.engine_state = "running"
        # Items stuck in "sending" (crash mid-send) are never repeated: unknown outcome.
        stuck = self.env["li.queue"].sudo().search([("state", "=", "sending"),
                                                    ("write_date", "<", self._now() - timedelta(minutes=15))])
        for item in stuck:
            self._mark_unknown(item, "Interrupted while sending")
        # Chrome claims that were never confirmed go back to the queue.
        self.env["li.queue"].sudo().search([("state", "=", "ready"), ("claimed_at", "<",
                                                                       self._now() - timedelta(minutes=30))]
                                           ).write({"claimed_at": False})
        return True

    @api.model
    def _cron_gc(self):
        self.env["li.engine.action"].sudo()._gc_old(60)
        return True
