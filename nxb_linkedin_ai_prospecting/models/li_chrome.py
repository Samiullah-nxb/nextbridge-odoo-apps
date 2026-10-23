# -*- coding: utf-8 -*-
"""AI browser agent mode: Odoo hands out one LinkedIn step at a time, the AI does it in the person's
Chrome and reports back. Odoo keeps every limit, window and gap; the AI confirms every send first."""
from datetime import timedelta

from odoo import _, api, fields, models

from .li_common import normalize_linkedin_url, one_paragraph

SEND_OUTCOMES = ("sent", "pending_already", "connected_already", "no_connect", "email_required",
                 "weekly_limit", "followed", "withdrawn", "not_pending", "not_connected", "failed", "unknown")


class LiChrome(models.AbstractModel):
    _name = "li.chrome"
    _description = "LinkedIn Sales: AI browser agent orchestration"

    def _profile(self, token, profile_id):
        profiles = token._profiles().filtered(lambda p: p.execution_mode == "chrome")
        if profile_id:
            profiles = profiles.filtered(lambda p: p.id == int(profile_id))
        return profiles[:1]

    def _log(self, profile, action, target=None, status="ok", details=None):
        self.env["li.agent"].sudo()._log(profile, action, target=target, status=status, details=details,
                                         source="chrome")

    # ------------------------------------------------------------------ account check
    def start(self, token, profile_id, seen_url=None, seen_name=None):
        profile = self._profile(token, profile_id)
        if not profile:
            return {"ok": False, "error": "No AI browser agent account with this id for this connector",
                    "accounts": [{"profile_id": p.id, "name": p.name, "mode": p.execution_mode}
                                 for p in token._profiles()]}
        seen = normalize_linkedin_url(seen_url) if seen_url else None
        same = (seen and seen == profile.linkedin_url) or (
            seen_name and profile.own_name and seen_name.strip().lower() == profile.own_name.strip().lower())
        if not seen and not seen_name:
            return {"ok": False, "do": "check_account",
                    "instructions": ["Open https://www.linkedin.com/in/me/ in this Chrome profile.",
                                     "Call li_chrome_start again with seen_url = the profile URL the browser lands on "
                                     "and seen_name = the name shown."]}
        if not same:
            self._log(profile, "account_check", seen_url or seen_name, status="error",
                      details="Chrome is signed in to a different LinkedIn account")
            return {"ok": False, "error": "This Chrome is signed in to %s, but the account is %s (%s). Stop and ask "
                                          "the person to switch Chrome profile." % (seen_url or seen_name,
                                                                                     profile.name, profile.linkedin_url)}
        vals = {"state": "connected", "last_check": fields.Datetime.now()}
        if seen_name and not profile.own_name:
            vals["own_name"] = seen_name
        profile.write(vals)
        self._log(profile, "account_check", profile.linkedin_url, details="Chrome account confirmed")
        return {"ok": True, "account": profile._ai_context(),
                "next": "Write any texts first (li_get_work → li_submit_work), then loop li_chrome_next until it "
                        "says done."}

    # ------------------------------------------------------------------ next step
    def next_action(self, token, profile_id):
        profile = self._profile(token, profile_id)
        if not profile:
            return {"do": "stop", "reason": "Unknown AI browser agent account"}
        if profile.state in ("reconnect", "restricted"):
            return {"do": "stop", "reason": "Account state: %s" % profile.state}
        if not profile.last_check or profile.last_check < fields.Datetime.now() - timedelta(hours=12):
            return {"do": "check_account", "instructions": [
                "Open https://www.linkedin.com/in/me/ and call li_chrome_start with seen_url and seen_name."]}
        agent = self.env["li.agent"].sudo()
        # 1. Reads that are due
        for step in agent._due_reads(profile):
            action = self._read_action(profile, step)
            if action:
                return action
        # 2. Writing waiting for the AI
        waiting = self.env["li.work"].sudo()._collect(token, profile, limit=1, dry=True)
        if waiting:
            return {"do": "write", "instructions": ["Call li_get_work for this profile, write the texts and "
                                                    "li_submit_work them, then call li_chrome_next again."]}
        # 3. Sends, one at a time with the delay gap
        now = fields.Datetime.now()
        item = agent._pick_next(profile)
        if item:
            if profile.next_action_at and profile.next_action_at > now:
                return {"do": "wait", "seconds": int((profile.next_action_at - now).total_seconds()) + 1,
                        "instructions": ["Wait this many seconds (random delay gap), then call li_chrome_next."]}
            item.claimed_at = now
            return self._send_action(item)
        return {"do": "done", "reason": "Nothing more to do now; Odoo keeps the limits and windows.",
                "instructions": ["Call li_finish_run with a short summary."]}

    def _read_action(self, profile, step):
        common = "Report what you saw with li_chrome_report(action_id, outcome='done', data=...)."
        if step == "inbox":
            return {"do": "read_inbox", "action_id": "read:inbox:%s" % profile.id, "instructions": [
                "Open https://www.linkedin.com/messaging/ and read the conversation list (top ~30).",
                "data = {threads: [{name, profile_url (if visible), thread_url, last_text, last_from_me (bool), "
                "unread (bool)}]}", common]}
        if step == "acceptances":
            return {"do": "read_acceptances", "action_id": "read:acceptances:%s" % profile.id, "instructions": [
                "Open https://www.linkedin.com/mynetwork/invite-connect/connections/ (sorted by recently added).",
                "data = {people: [{name, url}]} for the newest ~40 connections.", common]}
        if step == "verify_invites":
            return {"do": "verify_invites", "action_id": "read:sent:%s" % profile.id, "instructions": [
                "Open https://www.linkedin.com/mynetwork/invitation-manager/sent/ and scroll to the end.",
                "Only invitation rows count (ignore suggestions and other sections).",
                "data = {people: [{name, url}], complete: true if you reached the end of the list}", common]}
        if step == "read_profiles":
            prospect = self.env["li.prospect"].sudo().search(
                self.env["li.agent"]._profiles_to_read_domain(profile), limit=1, order="stage desc, id")
            if prospect:
                return {"do": "read_profile", "action_id": "profile:%s" % prospect.id,
                        "url": prospect.linkedin_url, "instructions": [
                            "Open the profile and read it: headline, location, current company, About, recent "
                            "experience.",
                            "data = {name, headline, location, company, text (a plain-text summary of the whole "
                            "profile, max ~3000 chars), connect_state: connect|pending|message|follow_only}", common]}
        if step == "search":
            persona = profile.persona_ids.filtered(self.env["li.agent"]._persona_needs_people)[:1]
            if persona:
                q = self.env["li.agent"]._search_query(persona)
                return {"do": "search_people", "action_id": "search:%s" % persona.id, "query": q, "instructions": [
                    "Search LinkedIn People for keywords %r in location %r (page %s)." % (
                        q["keywords"], q["location"], q["page"]),
                    "data = {people: [{name, url, headline, location, degree}]} for up to 25 results.",
                    "Do not connect with anyone here; Odoo queues them.", common]}
        if step == "post_stats":
            post = self.env["li.post"].sudo().search([("profile_id", "=", profile.id), ("state", "=", "published"),
                                                      ("post_url", "!=", False)], order="published_at desc", limit=1)
            if post:
                return {"do": "read_post_stats", "action_id": "stats:%s" % post.id, "url": post.post_url,
                        "instructions": ["Open the post, count reactions and comments, copy the comment texts.",
                                         "data = {reactions, comments, comment_texts: []}", common]}
            profile.last_stats_read = fields.Datetime.now()
        return None

    def _send_action(self, item):
        prospect = item.prospect_id
        aid = "queue:%s" % item.id
        confirm = ("Right before the click that sends, call li_confirm_send(action_id%s). Only if it answers "
                   "ok=true, send exactly the text it returns, then call li_chrome_report(action_id, outcome).")
        if item.kind == "invite":
            return {"do": "send_invite", "action_id": aid, "url": prospect.linkedin_url, "person": prospect.name,
                    "instructions": [
                        "Open the profile. Try Connect by every route: the Connect button, then the More menu.",
                        "If it shows Pending: outcome='pending_already'. If it shows Message (1st degree): "
                        "outcome='connected_already'. If LinkedIn asks for their email: outcome='email_required'. "
                        "If there is no Connect anywhere: outcome='no_connect'.",
                        "If you can Connect: click Connect, 'Add a note', paste the note. " + confirm % "",
                        "If LinkedIn says you reached the weekly invitation limit: outcome='weekly_limit'.",
                        "After sending: outcome='sent'."]}
        if item.kind == "follow":
            return {"do": "follow", "action_id": aid, "url": prospect.linkedin_url, "instructions": [
                "Open the profile. If a Connect button is visible (directly or under More), do NOT follow: "
                "report outcome='failed' with data.reason='connect visible'.",
                "Otherwise click Follow. " + confirm % "", "outcome='followed' when done."]}
        if item.kind == "withdraw":
            return {"do": "withdraw_invite", "action_id": aid, "url": prospect.linkedin_url, "instructions": [
                "Open Sent invitations, find this person and click Withdraw. " + confirm % "",
                "outcome='withdrawn', or 'not_pending' if no invitation is there."]}
        if item.kind == "post":
            return {"do": "publish_post", "action_id": aid, "instructions": [
                "Open https://www.linkedin.com/feed/, Start a post, paste the text keeping line breaks. "
                + confirm % "", "outcome='sent' with data.url = the post URL if you can get it."]}
        return {"do": "send_message", "action_id": aid, "url": prospect.linkedin_url, "person": prospect.name,
                "thread_url": prospect.conversation_url or None, "instructions": [
                    "Open the conversation with this person (Message button on their profile, or the thread URL).",
                    "Read the latest messages.",
                    confirm % ", conversation=[{direction: 'in'|'out', text}] = the last ~10 messages you see",
                    "Type the text as one paragraph (never press Enter before it is complete) and send.",
                    "outcome='sent', or 'not_connected' if there is no Message option."]}

    # ------------------------------------------------------------------ confirm
    def confirm_send(self, token, action_id, conversation=None):
        kind, _s, rid = (action_id or "").partition(":")
        if kind != "queue" or not rid.isdigit():
            return {"ok": False, "reason": "Only queue actions are confirmed"}
        item = self.env["li.queue"].sudo().browse(int(rid)).exists()
        if not item or item.profile_id not in token._profiles():
            return {"ok": False, "reason": "Unknown item"}
        if item.state != "ready":
            return {"ok": False, "reason": "Item is %s" % item.state}
        agent = self.env["li.agent"].sudo()
        persona = item.persona_id
        if item.prospect_id and (item.prospect_id.stage == "do_not_contact"
                                 or (item.prospect_id.taken_over and item.kind != "handoff")):
            item.write({"state": "dropped", "error": "Do not contact / taken over"})
            return {"ok": False, "reason": "Prospect is taken over or do-not-contact; do not send"}
        if item.kind != "post":
            if not persona._in_window():
                return {"ok": False, "reason": "Outside the send window; do not send"}
            if item.kind in ("invite", "follow") and persona._invites_left_today() <= 0:
                return {"ok": False, "reason": "Daily/weekly invite limit reached or invites paused"}
            if item.kind in ("message", "followup", "closing", "handoff"):
                if persona._messages_left_today() <= 0:
                    return {"ok": False, "reason": "Daily message limit reached"}
                if conversation is None:
                    return {"ok": False, "reason": "Read the conversation first and pass it as 'conversation'"}
                if not agent._fresh_read_allows(item, {"messages": conversation}):
                    return {"ok": False, "reason": item.error or "Not allowed after fresh read; do not send"}
        item.write({"state": "sending", "attempts": item.attempts + 1})
        text = item.text or ""
        if item.kind in ("message", "followup", "closing", "handoff"):
            text = one_paragraph(text)
        return {"ok": True, "send_exactly": text, "kind": item.kind}

    # ------------------------------------------------------------------ report
    def report(self, token, action_id, outcome, data=None, run_id=None):
        data = data or {}
        parts = (action_id or "").split(":")
        agent = self.env["li.agent"].sudo()
        allowed = token._profiles()
        run = self.env["li.ai.run"].sudo().browse(int(run_id)).exists() if run_id else None
        if run:
            run.actions_done += 1
        head = parts[0]
        if head == "queue" and len(parts) == 2 and parts[1].isdigit():
            item = self.env["li.queue"].sudo().browse(int(parts[1])).exists()
            if not item or item.profile_id not in allowed:
                return {"ok": False, "error": "Unknown item"}
            if outcome not in SEND_OUTCOMES:
                return {"ok": False, "error": "outcome must be one of %s" % ", ".join(SEND_OUTCOMES)}
            if outcome in ("sent", "followed", "withdrawn") and item.state != "sending":
                return {"ok": False, "error": "This send was not confirmed with li_confirm_send first"}
            ok = outcome not in ("failed", "unknown")
            res = {"ok": ok, "data": {"result": outcome, "url": data.get("url")}, "error": data.get("reason")}
            if outcome == "unknown":
                res["code"] = "unknown"
            if item.state != "sending":
                item.state = "sending"
            agent._apply_send_result(item, res)
            item.profile_id._bump_next_action(item.persona_id)
            self._log(item.profile_id, "chrome_" + item.kind, item.prospect_id.linkedin_url if item.prospect_id
                      else None, status="ok" if ok else "error", details=outcome)
            return {"ok": True, "item_state": item.state, "next": "Call li_chrome_next."}

        if head == "read" and len(parts) == 3:
            profile = allowed.filtered(lambda p: p.id == int(parts[2]))
            if not profile:
                return {"ok": False, "error": "Unknown account"}
            now = fields.Datetime.now()
            if parts[1] == "inbox":
                profile.last_inbox_read = now
                to_open = agent._apply_inbox(profile, data.get("threads") or [])
                self._log(profile, "read_inbox", details="%s threads, %s to open" % (
                    len(data.get("threads") or []), len(to_open)))
                return {"ok": True, "open_these": [
                    {"action_id": "conv:%s" % p.id, "person": p.name, "thread_url": t.get("thread_url"),
                     "url": p.linkedin_url,
                     "instructions": "Open, read the last ~15 messages and li_chrome_report(action_id, 'done', "
                                     "{messages: [{direction: 'in'|'out', text, attachment?}], thread_url})"}
                    for p, t in to_open]}
            if parts[1] == "acceptances":
                profile.last_acceptance_read = now
                n = agent._apply_acceptances(profile, data.get("people") or [])
                self._log(profile, "recent_connections", details="%s accepted" % n)
                return {"ok": True, "accepted": n}
            if parts[1] == "sent":
                profile.last_invite_verify = now
                check = agent._apply_sent_invitations(profile, data.get("people") or [], bool(data.get("complete")))
                self._log(profile, "sent_invitations", details="%s listed; %s to check" % (
                    len(data.get("people") or []), len(check)))
                return {"ok": True, "check_these": [
                    {"action_id": "check:%s" % p.id, "url": p.linkedin_url, "person": p.name,
                     "instructions": "Open the profile; report data = {connect_state: pending|message|connect}"}
                    for p in check]}
        if head in ("conv", "check", "profile") and len(parts) == 2 and parts[1].isdigit():
            prospect = self.env["li.prospect"].sudo().browse(int(parts[1])).exists()
            if not prospect or prospect.profile_id not in allowed:
                return {"ok": False, "error": "Unknown prospect"}
            if head == "conv":
                r = agent._apply_conversation(prospect, data)
                return {"ok": True, **r, "needs_reply": prospect.needs_reply}
            if head == "check":
                agent._apply_profile_check(prospect, data)
                return {"ok": True, "stage": prospect.stage}
            agent._apply_profile(prospect, data)
            if data.get("connect_state") == "message" and prospect.stage == "queued":
                prospect.write({"stage": "connected", "accepted_at": fields.Datetime.now(),
                                "already_connected": True})
            return {"ok": True}
        if head == "search" and len(parts) == 2 and parts[1].isdigit():
            persona = self.env["li.persona"].sudo().browse(int(parts[1])).exists()
            if not persona or persona.profile_id not in allowed:
                return {"ok": False, "error": "Unknown persona"}
            persona.profile_id.last_search = fields.Datetime.now()
            self._log(persona.profile_id, "search_people", data.get("keywords"))
            return {"ok": True, "added": agent._apply_search(persona, data.get("people") or [])}
        if head == "stats" and len(parts) == 2 and parts[1].isdigit():
            post = self.env["li.post"].sudo().browse(int(parts[1])).exists()
            if not post or post.profile_id not in allowed:
                return {"ok": False, "error": "Unknown post"}
            agent._apply_post_stats(post, data)
            post.profile_id.last_stats_read = fields.Datetime.now()
            return {"ok": True}
        return {"ok": False, "error": "Unknown action_id %s" % action_id}
