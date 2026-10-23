# -*- coding: utf-8 -*-
"""What the AI sees and writes: work items out, texts in. Odoo decides what may be sent and when."""
import logging
from datetime import timedelta

from odoo import _, api, fields, models

from .li_common import ACTIVE_CHAT_STAGES, CLOSED_STAGES, INTENTS, normalize_linkedin_url, one_paragraph, \
    text_fingerprint

_logger = logging.getLogger(__name__)

WRITING_GUIDE = (
    "Before writing to a person: read their LinkedIn profile first, then the whole conversation and where it "
    "stands, then their last reply and its nature, and only then write. The questionnaire steps and examples are "
    "guidelines, not a script: write what a thoughtful salesperson would naturally say next. Never repeat a "
    "message already sent. Never claim to have opened a file the person shared. Chat messages are sent as one "
    "paragraph. Do not invent facts about our services beyond what is given."
)

INTENT_HELP = {
    "continue": "they answered; move to the next questionnaire step",
    "answer": "they asked a question or raised a topic; answer it (the step is not used up)",
    "pivot": "pivot criteria met; your text is the handoff to sales",
    "request_contact": "they ask for a call, email, meeting or quote; your text is a short closing that says a "
                       "colleague will follow up (give phone/email only if asked)",
    "wrong_person": "they say they are not the right person; politely ask who is (only once)",
    "referral": "they point to someone else; put the contact details in 'referral'; your text is a polite thank-you",
    "not_interested": "they decline; your text is one warm closing message",
    "no_need": "they need nothing now; your text is one warm closing message",
}


class LiWork(models.AbstractModel):
    _name = "li.work"
    _description = "LinkedIn Sales: AI work interface"

    # ------------------------------------------------------------------ overview
    def overview(self, token):
        profiles = token._profiles()
        Prospect = self.env["li.prospect"].sudo()
        Queue = self.env["li.queue"].sudo()
        since = fields.Datetime.now() - timedelta(days=1)
        out = []
        for p in profiles:
            out.append({
                **p._ai_context(),
                "engine": p.engine_state if p.execution_mode == "engine" else None,
                "personas": [{"id": x.id, "name": x.name, "connection_agent": x.conn_state, "chat_agent": x.chat_state,
                              "invites_left_today": x._invites_left_today(),
                              "messages_left_today": x._messages_left_today(),
                              "window_open_now": x._in_window()} for x in p.persona_ids],
                "followup_agent": p.followup_state,
                "activity_agent": p.activity_agent_state,
                "work_waiting_for_ai": len(self._collect(token, p, limit=200, dry=True)),
                "queue_ready": Queue.search_count([("profile_id", "=", p.id), ("state", "=", "ready")]),
                "sent_last_24h": Queue.search_count([("profile_id", "=", p.id), ("state", "=", "sent"),
                                                     ("sent_at", ">=", since)]),
                "replies_waiting": Prospect.search_count([("profile_id", "=", p.id), ("needs_reply", "=", True)]),
                "flagged_for_a_person": Prospect.search_count([("profile_id", "=", p.id), ("flag_check", "=", True)]),
                "prospects_by_stage": {s: c for s, c in self._stage_counts(p).items() if c},
            })
        return {"accounts": out, "server_time_utc": fields.Datetime.to_string(fields.Datetime.now()),
                "writing_guide": WRITING_GUIDE}

    def _stage_counts(self, profile):
        groups = self.env["li.prospect"].sudo()._read_group([("profile_id", "=", profile.id)], ["stage"], ["__count"])
        return {stage: count for stage, count in groups}

    # ------------------------------------------------------------------ get work
    def get_work(self, token, profile_id=None, limit=25, kinds=None, mode="engine"):
        profiles = self._scoped_profiles(token, profile_id)
        run = self.env["li.ai.run"].sudo().create({
            "name": _("Run %s") % fields.Datetime.to_string(fields.Datetime.now()),
            "token_id": token.id, "mode": mode, "profile_ids": [(6, 0, profiles.ids)],
        })
        items = []
        for p in profiles:
            if p.state in ("reconnect", "restricted"):
                continue
            items += self._collect(token, p, limit=limit - len(items), kinds=kinds)
            if len(items) >= limit:
                break
        run.items_given = len(items)
        return {
            "run_id": run.id,
            "writing_guide": WRITING_GUIDE,
            "intents": INTENT_HELP,
            "how_to_answer": "Call li_submit_work with run_id and one entry per work item: "
                             "{work_id, text, intent (reply items only), summary (1-3 lines on the person and "
                             "where they stand), answers (what they told us for the questionnaire), "
                             "referral (contact details, referral only)}. Skip an item by leaving it out.",
            "items": items,
            "more_waiting": len(items) >= limit,
        }

    def _scoped_profiles(self, token, profile_id=None):
        profiles = token._profiles()
        if profile_id:
            profiles = profiles.filtered(lambda p: p.id == int(profile_id))
        return profiles

    def _pending_chat(self, prospect):
        return prospect.queue_ids.filtered(lambda q: q.kind in ("message", "followup", "closing", "handoff")
                                           and q.state in ("waiting_text", "ready", "sending"))

    def _collect(self, token, profile, limit=25, kinds=None, dry=False):
        Prospect = self.env["li.prospect"].sudo()
        items = []
        acc = profile._ai_context()

        def want(kind):
            return (not kinds or kind in kinds) and len(items) < limit

        # 1. Replies first: every message a prospect sends gets an answer.
        if want("reply"):
            for pr in Prospect.search([("profile_id", "=", profile.id), ("needs_reply", "=", True),
                                       ("taken_over", "=", False), ("stage", "!=", "do_not_contact")],
                                      order="last_inbound_at", limit=limit):
                if self._pending_chat(pr) or pr.persona_id.chat_state == "stopped":
                    continue
                unanswered = pr.message_ids_li.filtered(
                    lambda m: m.direction == "in" and (not pr.last_outbound_at or m.date >= pr.last_outbound_at))
                items.append(self._item("reply", "reply:%s" % pr.id, acc, pr, {
                    "reply_to": [{"text": m.body, "date": fields.Datetime.to_string(m.date),
                                  "attachment": m.is_attachment} for m in unanswered.sorted("date")],
                    "task": "Analyse their reply, choose an intent and write our next message. If they asked a "
                            "question, answer it first. If the questionnaire is over, still answer.",
                }))
                if not want("reply"):
                    break
        # 2. First message after accept
        if want("first_message"):
            for pr in Prospect.search([("profile_id", "=", profile.id), ("stage", "=", "connected"),
                                       ("taken_over", "=", False), ("last_outbound_at", "=", False),
                                       ("needs_reply", "=", False)], order="accepted_at", limit=limit):
                if pr.persona_id.chat_state != "running" or self._pending_chat(pr):
                    continue
                items.append(self._item("first_message", "first:%s" % pr.id, acc, pr, {
                    "task": "They accepted our invite. Write the first message for the next questionnaire step. "
                            + ("We already know each other (already connected)." if pr.already_connected else ""),
                }))
                if not want("first_message"):
                    break
        # 3. Follow-ups for silent people
        if want("followup") and profile.followup_state == "running":
            cutoff = fields.Datetime.now() - timedelta(days=profile.followup_delay_days or 4)
            for pr in Prospect.search([("profile_id", "=", profile.id), ("stage", "in", ACTIVE_CHAT_STAGES),
                                       ("taken_over", "=", False), ("needs_reply", "=", False),
                                       ("last_outbound_at", "<", cutoff),
                                       ("followups_sent", "<", profile.followup_max)], limit=limit):
                if pr.last_inbound_at and pr.last_inbound_at > pr.last_outbound_at:
                    continue
                if self._pending_chat(pr) or pr.persona_id.chat_state == "stopped":
                    continue
                items.append(self._item("followup", "followup:%s" % pr.id, acc, pr, {
                    "task": "They went silent. Write follow-up %s of %s. %s" % (
                        pr.followups_sent + 1, profile.followup_max, profile.followup_instructions or ""),
                }))
                if not want("followup"):
                    break
        # 4. Invite notes
        if want("invite_note"):
            for persona in profile.persona_ids.filtered(lambda x: x.conn_state == "running" and x.conn_note):
                pending = self.env["li.queue"].sudo().search_count([
                    ("persona_id", "=", persona.id), ("kind", "=", "invite"), ("state", "in", ("ready", "sending"))])
                left = persona._invites_left_today() - pending
                if left <= 0:
                    continue
                domain = [("persona_id", "=", persona.id), ("stage", "=", "queued"), ("note_text", "=", False)]
                if profile.execution_mode == "engine":
                    domain.append(("profile_text", "!=", False))
                for pr in Prospect.search(domain, limit=min(left, limit - len(items)), order="id"):
                    items.append(self._item("invite_note", "note:%s" % pr.id, acc, pr, {
                        "task": "Write the note sent with the connection invite. %s" % (persona.conn_note_instructions or ""),
                        "max_chars": 280,
                    }))
                if not want("invite_note"):
                    break
        # 5. Posts
        if want("post"):
            for post in self.env["li.post"].sudo().search([("profile_id", "=", profile.id), ("state", "=", "idea")],
                                                          limit=max(limit - len(items), 0)):
                items.append({
                    "work_id": "post:%s" % post.id, "kind": "post", "account": acc,
                    "post": {"title": post.name, "idea": post.idea,
                             "service": post.service_id._ai_context() if post.service_id else None},
                    "task": "Draft a LinkedIn post from this idea. Line breaks are kept. No hashtag spam "
                            "(max 3). A person approves it before it is published.",
                })
        return items[:limit]

    def _item(self, kind, work_id, acc, prospect, extra):
        item = {
            "work_id": work_id, "kind": kind, "account": acc,
            "persona": prospect.persona_id._ai_context(),
            "prospect": prospect._ai_context(),
        }
        item.update(extra)
        return item

    # ------------------------------------------------------------------ submit
    def submit_work(self, token, run_id, entries):
        run = self.env["li.ai.run"].sudo().browse(int(run_id or 0)).exists()
        allowed = token._profiles()
        results = []
        written = refused = 0
        for entry in entries or []:
            try:
                res = self._submit_one(entry, allowed, run)
            except Exception as e:  # noqa: BLE001 - one bad entry must not lose the others
                _logger.exception("li_submit_work entry failed")
                res = {"work_id": entry.get("work_id"), "status": "error", "error": str(e)}
            if res.get("status") == "accepted":
                written += 1
            else:
                refused += 1
            results.append(res)
        if run:
            run.write({"items_written": run.items_written + written, "items_refused": run.items_refused + refused})
        return {"results": results, "accepted": written, "refused": refused}

    def _submit_one(self, entry, allowed, run):
        work_id = (entry.get("work_id") or "").strip()
        text = (entry.get("text") or "").strip()
        kind, _sep, rid = work_id.partition(":")
        if not rid.isdigit():
            return {"work_id": work_id, "status": "refused", "error": "Unknown work_id"}
        if kind == "post":
            post = self.env["li.post"].sudo().browse(int(rid)).exists()
            if not post or post.profile_id not in allowed:
                return {"work_id": work_id, "status": "refused", "error": "Post not found"}
            if not text:
                return {"work_id": work_id, "status": "refused", "error": "Empty text"}
            post.write({"text": text, "state": "draft"})
            post.message_post(body=_("Draft written by the AI; waiting for approval."))
            return {"work_id": work_id, "status": "accepted", "note": "Draft saved; a person approves it."}

        prospect = self.env["li.prospect"].sudo().browse(int(rid)).exists()
        if not prospect or prospect.profile_id not in allowed:
            return {"work_id": work_id, "status": "refused", "error": "Prospect not found for this connector"}
        if prospect.stage == "do_not_contact":
            return {"work_id": work_id, "status": "refused", "error": "Do not contact"}
        if prospect.taken_over:
            return {"work_id": work_id, "status": "refused", "error": "A person took this prospect over"}
        if not text:
            return {"work_id": work_id, "status": "refused", "error": "Empty text"}
        Queue = self.env["li.queue"].sudo()
        persona = prospect.persona_id
        base = {"profile_id": prospect.profile_id.id, "persona_id": persona.id, "prospect_id": prospect.id,
                "run_id": run.id if run else False}

        if kind == "note":
            if prospect.stage != "queued":
                return {"work_id": work_id, "status": "refused", "error": "No longer queued (%s)" % prospect.stage}
            note = text[:300]
            prospect.note_text = note
            Queue.create(dict(base, kind="invite", text=note, state="ready"))
            return {"work_id": work_id, "status": "accepted", "chars": len(note)}

        # chat texts
        text = one_paragraph(text)
        fp = text_fingerprint(text)
        if any(text_fingerprint(m.body) == fp for m in prospect.message_ids_li if m.direction == "out"):
            return {"work_id": work_id, "status": "refused", "error": "Repeats a message already sent to this person"}
        if self._pending_chat(prospect):
            return {"work_id": work_id, "status": "refused", "error": "A message is already waiting to be sent"}
        summary = (entry.get("summary") or "").strip()
        answers = (entry.get("answers") or "").strip()
        if summary:
            prospect.ai_summary = summary
        if answers:
            prospect.answers = ((prospect.answers or "") + "\n" + answers).strip()
        steps = persona.step_ids.sorted("sequence")
        now = fields.Datetime.now()

        if kind == "first":
            step = steps[prospect.step_index] if prospect.step_index < len(steps) else None
            delay = step.delay_hours if step and step.trigger == "after_accept" else 0
            sched = max(now, (prospect.accepted_at or now) + timedelta(hours=delay or 0))
            Queue.create(dict(base, kind="message", text=text, scheduled_at=sched,
                              after_send="advance"))
            self._maybe_pivot_on_step(prospect, step, summary)
            return {"work_id": work_id, "status": "accepted", "scheduled_at": fields.Datetime.to_string(sched)}

        if kind == "followup":
            Queue.create(dict(base, kind="followup", text=text, scheduled_at=now))
            return {"work_id": work_id, "status": "accepted"}

        if kind == "reply":
            intent = (entry.get("intent") or "answer").strip()
            if intent not in dict(INTENTS):
                intent = "answer"
            prospect.message_ids_li.filtered(lambda m: m.direction == "in" and not m.analysed).write(
                {"analysed": True, "intent": intent})
            prospect.needs_reply = False
            vals = dict(base, text=text, intent=intent, scheduled_at=now)
            step = steps[prospect.step_index] if prospect.step_index < len(steps) else None
            if intent == "continue":
                Queue.create(dict(vals, kind="message", after_send="advance"))
                self._maybe_pivot_on_step(prospect, step, summary)
            elif intent == "answer":
                Queue.create(dict(vals, kind="message"))
            elif intent == "wrong_person":
                if prospect.asked_who:
                    Queue.create(dict(vals, kind="closing", after_send="close:closed_not_interested"))
                else:
                    prospect.asked_who = True
                    Queue.create(dict(vals, kind="message"))
            elif intent in ("pivot", "request_contact"):
                instruction = {
                    "pivot": _("Pivot reached on LinkedIn. Continue the conversation personally."),
                    "request_contact": _("The prospect asked for a call / email / meeting / quote. "
                                         "Reply personally today."),
                }[intent]
                if entry.get("instruction"):
                    instruction += " " + entry["instruction"]
                prospect._create_crm_lead(summary or prospect.ai_summary, instruction)
                Queue.create(dict(vals, kind="handoff", after_send="close:lead;takeover"))
                prospect.write({"stage": "lead"})
            elif intent == "referral":
                details = (entry.get("referral") or "").strip()
                prospect.referral_details = details
                prospect._create_crm_lead(summary or prospect.ai_summary,
                                          _("The prospect referred us to someone else. Contact them."), extra=details)
                Queue.create(dict(vals, kind="closing", after_send="close:closed_referral"))
            elif intent in ("not_interested", "no_need"):
                Queue.create(dict(vals, kind="closing", after_send="close:closed_not_interested"))
            return {"work_id": work_id, "status": "accepted", "intent": intent}

        return {"work_id": work_id, "status": "refused", "error": "Unknown work kind"}

    def _maybe_pivot_on_step(self, prospect, step, summary):
        """Reaching a pivot step hands the prospect to sales once the text is queued."""
        if step and step.is_pivot:
            prospect._create_crm_lead(summary or prospect.ai_summary,
                                      _("Pivot step reached: %s. Continue personally.") % step.name)
            item = self._pending_chat(prospect)[:1]
            if item:
                item.write({"kind": "handoff", "after_send": "advance;close:lead;takeover"})
            prospect.write({"stage": "lead"})

    # ------------------------------------------------------------------ finish
    def finish_run(self, token, run_id, summary=None, errors=None):
        run = self.env["li.ai.run"].sudo().browse(int(run_id or 0)).exists()
        if not run or run.token_id != token:
            return {"ok": False, "error": "Run not found"}
        run.write({"finished_at": fields.Datetime.now(), "summary": summary,
                   "errors": errors, "state": "error" if errors else "done"})
        return {"ok": True, "run_id": run.id, "items_given": run.items_given, "texts_written": run.items_written,
                "refused": run.items_refused}

    # ------------------------------------------------------------------ send now / status
    def send_now(self, token, profile_id=None):
        profiles = self._scoped_profiles(token, profile_id).filtered(lambda p: p.execution_mode == "engine")
        Queue = self.env["li.queue"].sudo()
        missing_notes = []
        for p in profiles:
            missing_notes += self._collect(token, p, limit=50, kinds=["invite_note"])
        if missing_notes:
            run = self.env["li.ai.run"].sudo().create({"name": _("Send now"), "token_id": token.id,
                                                           "profile_ids": [(6, 0, profiles.ids)],
                                                           "items_given": len(missing_notes)})
            return {"status": "notes_needed", "run_id": run.id,
                    "message": "Write these invite notes with li_submit_work, then call li_send_now again.",
                    "writing_guide": WRITING_GUIDE, "items": missing_notes}
        self.env["li.agent"].sudo()._cron_plan()
        ready = []
        for p in profiles:
            p.next_action_at = False
            ready.append({"account": p.name,
                          "ready_to_send": Queue.search_count([("profile_id", "=", p.id), ("state", "=", "ready")]),
                          "windows_open": [x.name for x in p.persona_ids if x._in_window()],
                          "invites_paused_until": fields.Datetime.to_string(p.invites_paused_until)
                          if p._invites_paused() else None})
        cron = self.env.ref("nxb_linkedin_ai_prospecting.ir_cron_li_send", raise_if_not_found=False)
        if cron:
            cron.sudo()._trigger()
        return {"status": "sending", "accounts": ready,
                "note": "Odoo sends one at a time with the random delay gap, only inside send windows. "
                        "Ask li_sending_status in a few minutes."}

    def sending_status(self, token, profile_id=None, hours=24):
        profiles = self._scoped_profiles(token, profile_id)
        since = fields.Datetime.now() - timedelta(hours=float(hours or 24))
        Queue = self.env["li.queue"].sudo()
        out = []
        for p in profiles:
            sent = Queue.search([("profile_id", "=", p.id), ("sent_at", ">=", since), ("state", "=", "sent")])
            problems = Queue.search([("profile_id", "=", p.id), ("write_date", ">=", since),
                                     ("state", "in", ("failed", "unknown"))])
            ready = Queue.search([("profile_id", "=", p.id), ("state", "=", "ready")], limit=20)
            out.append({"account": p.name, "sent": [q._summary() for q in sent[:50]],
                        "problems": [q._summary() for q in problems[:20]],
                        "still_queued": [q._summary() for q in ready],
                        "invites_paused_until": fields.Datetime.to_string(p.invites_paused_until)
                        if p._invites_paused() else None,
                        "pause_reason": p.pause_reason if p._invites_paused() else None})
        return {"accounts": out}

    # ------------------------------------------------------------------ single invite / takeover
    def invite_person(self, token, linkedin_url, profile_id=None, persona_id=None, name=None):
        url = normalize_linkedin_url(linkedin_url)
        if not url or "/in/" not in url:
            return {"ok": False, "error": "Give a LinkedIn profile link like https://www.linkedin.com/in/someone/"}
        profiles = self._scoped_profiles(token, profile_id)
        Persona = self.env["li.persona"].sudo()
        persona = Persona.browse(int(persona_id)).exists() if persona_id else Persona.browse()
        if persona and persona.profile_id not in profiles:
            return {"ok": False, "error": "Persona not available for this connector"}
        if not persona:
            persona = profiles.mapped("persona_ids").sorted(lambda x: (x.conn_state != "running", x.sequence))[:1]
        if not persona:
            return {"ok": False, "error": "Create a persona for this LinkedIn account first"}
        Prospect = self.env["li.prospect"].sudo()
        prospect = Prospect.search([("persona_id", "=", persona.id), ("linkedin_url", "=", url)], limit=1)
        if prospect and prospect.stage not in ("queued", "on_hold"):
            return {"ok": False, "error": "Already %s" % dict(prospect._fields["stage"].selection)[prospect.stage]}
        if not prospect:
            prospect = Prospect.create({"name": name or url.rstrip("/").rsplit("/", 1)[-1], "linkedin_url": url,
                                        "persona_id": persona.id})
        if prospect.stage == "do_not_contact":
            return {"ok": False, "error": "Do not contact"}
        prospect.stage = "queued"
        res = {"ok": True, "prospect_id": prospect.id, "persona": persona.name, "account": persona.profile_id.name}
        if persona.conn_note:
            res["write_note"] = self._item("invite_note", "note:%s" % prospect.id, persona.profile_id._ai_context(),
                                           prospect, {"task": "Write the invite note. %s" % (
                                               persona.conn_note_instructions or ""), "max_chars": 280})
            res["next"] = "Submit the note with li_submit_work (run_id may be 0); Odoo then sends the invite."
        else:
            self.env["li.queue"].sudo().create({"profile_id": persona.profile_id.id, "persona_id": persona.id,
                                                "prospect_id": prospect.id, "kind": "invite", "state": "ready"})
            res["next"] = "Queued; Odoo sends it inside the send window."
        return res

    def takeover(self, token, who, release=False):
        Prospect = self.env["li.prospect"].sudo()
        profiles = token._profiles()
        url = normalize_linkedin_url(who) if "linkedin.com" in (who or "") else None
        domain = [("profile_id", "in", profiles.ids)]
        domain += [("linkedin_url", "=", url)] if url else [("name", "ilike", (who or "").strip())]
        found = Prospect.search(domain, limit=5)
        if not found:
            return {"ok": False, "error": "No prospect matches '%s'" % who}
        if len(found) > 1 and not url:
            return {"ok": False, "error": "Several matches; be more specific",
                    "matches": [{"name": p.name, "url": p.linkedin_url, "account": p.profile_id.name} for p in found]}
        if release:
            found.action_return_to_agents()
        else:
            found._take_over(_("Taken over via the AI assistant"))
        return {"ok": True, "prospect": found.name, "taken_over": not release}

    def answer_replies_now(self, token, profile_id=None):
        profiles = self._scoped_profiles(token, profile_id)
        agent = self.env["li.agent"].sudo()
        read = []
        for p in profiles.filtered(lambda x: x.execution_mode == "engine" and x._can_work()):
            agent._engine_inbox(p)
            read.append(p.name)
        work = self.get_work(token, profile_id=profile_id, kinds=["reply"], limit=30)
        work["inbox_read_now"] = read
        return work
