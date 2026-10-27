# -*- coding: utf-8 -*-
# Copyright 2026 Nextbridge. License OPL-1.
"""CRM & prospecting tools (needs the CRM app in the target database)."""
import re

from ..core.context import ToolError
from ..core.registry import s_arr, s_int, s_obj, s_str, tool

LEAD_FIELDS = ["name", "type", "partner_name", "contact_name", "email_from", "phone", "website", "country_id",
               "stage_id", "user_id", "expected_revenue", "probability", "tag_ids", "create_date", "active"]


def _brief(ctx, brief_id, kind):
    env = ctx.require_env("Briefs")
    Brief = env["nxb.mcp.brief"]
    if brief_id:
        brief = Brief.browse(int(brief_id)).exists()
    else:
        brief = Brief.search([("kind", "=", kind), ("state", "=", "active")], limit=1)
    if not brief:
        raise ToolError("No %s brief found. Ask the user to create one in AI Connector → AI workspace → Briefs and "
                        "mark it 'Ready for the assistant'." % ("prospecting" if kind == "prospecting" else "campaign"))
    if brief.kind != kind:
        raise ToolError("Brief %s is a %s brief." % (brief.id, brief.kind))
    return brief


def _tags(env, names):
    Tag = env["crm.tag"]
    ids = []
    for name in names or []:
        name = (name or "").strip()
        if not name:
            continue
        tag = Tag.search([("name", "=ilike", name)], limit=1) or Tag.create({"name": name})
        ids.append(tag.id)
    return ids


def _user(env, who):
    if not who:
        return False
    if isinstance(who, int) or (isinstance(who, str) and who.isdigit()):
        return env["res.users"].browse(int(who)).exists().id or False
    user = env["res.users"].search(["|", ("login", "=ilike", who), ("name", "ilike", who)], limit=1)
    if not user:
        raise ToolError("No user matches %r." % who)
    return user.id


def _stage(env, stage):
    if not stage:
        return False
    if isinstance(stage, int) or (isinstance(stage, str) and stage.isdigit()):
        return int(stage)
    st = env["crm.stage"].search([("name", "ilike", stage)], limit=1)
    if not st:
        names = env["crm.stage"].search([]).mapped("name")
        raise ToolError("No stage named %r. Stages: %s." % (stage, ", ".join(names)))
    return st.id


def _country(env, code):
    if not code:
        return False
    c = env["res.country"].search(["|", ("code", "=ilike", code), ("name", "=ilike", code)], limit=1)
    return c.id or False


FREE_MAIL = {"gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com", "msn.com", "yahoo.com",
             "ymail.com", "icloud.com", "me.com", "aol.com", "proton.me", "protonmail.com", "gmx.com", "gmx.de",
             "web.de", "mail.ru", "yandex.ru", "yandex.com", "zoho.com"}


def _domain_of(url_or_email):
    v = (url_or_email or "").lower().strip()
    if "@" in v:
        return v.split("@", 1)[1]
    v = re.sub(r"^https?://", "", v)
    v = re.sub(r"^www\.", "", v)
    return v.split("/", 1)[0] or None


def _lead_out(lead):
    r = lead.read(LEAD_FIELDS)[0]
    return {"id": lead.id, "name": r["name"], "type": r["type"], "company": r["partner_name"] or "",
            "contact": r["contact_name"] or "", "email": r["email_from"] or "", "phone": r["phone"] or "",
            "website": r["website"] or "", "country": r["country_id"] and r["country_id"][1] or "",
            "stage": r["stage_id"] and r["stage_id"][1] or "", "salesperson": r["user_id"] and r["user_id"][1] or "",
            "expected_revenue": r["expected_revenue"], "probability": r["probability"],
            "tags": lead.tag_ids.mapped("name"), "active": r["active"]}


@tool("crm_brief_read", "crm", "Read a prospecting brief",
      "Read a prospecting brief written by a salesperson: goal, target audience, offer, exclusions, how many leads, "
      "tags to use. Without an id, returns the newest brief that is ready.",
      properties={"brief_id": s_int("Brief id (optional).")})
def crm_brief_read(ctx, brief_id=None):
    brief = _brief(ctx, brief_id, "prospecting")
    out = brief._for_assistant()
    out["how_to"] = ("Create each lead with crm_lead_add, using the brief's tags. The tool skips duplicates "
                     "(same email or website domain). When finished, call crm_brief_done with a short summary.")
    return out


@tool("crm_brief_done", "crm", "Report on a brief",
      "Mark a brief as done and store a short summary of what you did, for the salesperson.",
      properties={"brief_id": s_int("Brief id."), "summary": s_str("What you created or found.")},
      required=("brief_id", "summary"), writes=True)
def crm_brief_done(ctx, brief_id, summary):
    env = ctx.require_env("Briefs")
    brief = env["nxb.mcp.brief"].browse(int(brief_id)).exists()
    if not brief:
        raise ToolError("Brief %s does not exist." % brief_id)
    brief.write({"state": "done", "result_note": summary})
    return {"brief_id": brief.id, "state": "done"}


@tool("crm_pipeline", "crm", "Pipeline overview",
      "Summarise the CRM pipeline: number of opportunities and expected revenue per stage, and per salesperson.",
      properties={"salesperson": s_str("Only this salesperson (login or name)."),
                  "team": s_str("Only this sales team (name).")})
def crm_pipeline(ctx, salesperson=None, team=None):
    env = ctx.env
    dom = [("type", "=", "opportunity"), ("active", "=", True)]
    if salesperson:
        dom.append(("user_id", "=", _user(env, salesperson)))
    if team:
        dom.append(("team_id.name", "ilike", team))
    Lead = env["crm.lead"]
    by_stage = [{"stage": s.display_name if s else "None", "count": n, "expected_revenue": rev or 0}
                for s, n, rev in Lead._read_group(dom, ["stage_id"], ["__count", "expected_revenue:sum"])]
    by_user = [{"salesperson": u.display_name if u else "Unassigned", "count": n, "expected_revenue": rev or 0}
               for u, n, rev in Lead._read_group(dom, ["user_id"], ["__count", "expected_revenue:sum"])]
    leads = Lead.search_count([("type", "=", "lead"), ("active", "=", True)])
    return {"open_opportunities_by_stage": by_stage, "by_salesperson": by_user, "unqualified_leads": leads}


@tool("crm_lead_find", "crm", "Find leads",
      "Find leads and opportunities by text (name, company, contact, email), stage, salesperson, tag or type.",
      properties={"text": s_str("Text to look for."), "stage": s_str("Stage name."),
                  "salesperson": s_str("Login or name."), "tag": s_str("Tag name."),
                  "type": s_str("lead or opportunity.", ["lead", "opportunity"]),
                  "include_lost": {"type": "boolean", "description": "Include lost/archived ones."},
                  "limit": s_int("Maximum results.", 20, 1, 200)})
def crm_lead_find(ctx, text=None, stage=None, salesperson=None, tag=None, type=None, include_lost=False, limit=20):
    env = ctx.env
    dom = []
    if text:
        dom += ["|", "|", "|", ("name", "ilike", text), ("partner_name", "ilike", text),
                ("contact_name", "ilike", text), ("email_from", "ilike", text)]
    if stage:
        dom.append(("stage_id", "=", _stage(env, stage)))
    if salesperson:
        dom.append(("user_id", "=", _user(env, salesperson)))
    if tag:
        dom.append(("tag_ids.name", "ilike", tag))
    if type:
        dom.append(("type", "=", type))
    Lead = env["crm.lead"].with_context(active_test=not include_lost)
    leads = Lead.search(dom, limit=ctx.cap_limit(limit, 20), order="create_date desc")
    return {"total": Lead.search_count(dom), "leads": [_lead_out(l) for l in leads]}


@tool("crm_lead_add", "crm", "Add a lead",
      "Create a lead or opportunity. Skips duplicates: if a lead with the same email or website domain exists, it "
      "is returned instead of creating a new one. Tags are created when missing.",
      properties={"name": s_str("Lead title, e.g. 'Acme GmbH – store furniture'."),
                  "company": s_str("Company name."), "contact": s_str("Contact person."),
                  "email": s_str("Email."), "phone": s_str("Phone."), "website": s_str("Website."),
                  "country": s_str("Country code or name, e.g. DE."), "description": s_str("Notes, sources, fit."),
                  "expected_revenue": {"type": "number", "description": "Expected revenue."},
                  "probability": {"type": "number", "description": "Probability 0-100."},
                  "type": s_str("lead (default) or opportunity.", ["lead", "opportunity"]),
                  "tags": s_arr("Tag names.", {"type": "string"}), "salesperson": s_str("Login or name."),
                  "stage": s_str("Stage name (opportunities)."), "brief_id": s_int("Brief this lead comes from.")},
      required=("name",), writes=True)
def crm_lead_add(ctx, name, company=None, contact=None, email=None, phone=None, website=None, country=None,
                 description=None, expected_revenue=None, probability=None, type="lead", tags=None,
                 salesperson=None, stage=None, brief_id=None):
    env = ctx.env
    Lead = env["crm.lead"].with_context(active_test=False)
    for key in (email, website):
        dom = _domain_of(key)
        if not dom or dom in FREE_MAIL:
            continue
        dup = Lead.search(["|", ("email_from", "=ilike", email or "-"), "|", ("email_from", "=ilike", "%@" + dom),
                           ("website", "ilike", dom)], limit=1)
        if dup:
            return {"duplicate": True, "existing": _lead_out(dup),
                    "message": "A lead for this company already exists; nothing created."}
    if brief_id:
        brief = env["nxb.mcp.brief"].browse(int(brief_id)).exists()
        if brief:
            tags = list(tags or []) + brief._for_assistant()["tags"]
            description = ("%s\n\nFrom brief: %s" % (description or "", brief.name)).strip()
    vals = {"name": name, "partner_name": company or False, "contact_name": contact or False,
            "email_from": email or False, "phone": phone or False, "website": website or False,
            "country_id": _country(env, country), "description": description or False,
            "type": type or "lead", "tag_ids": [(6, 0, _tags(env, tags))]}
    if expected_revenue is not None:
        vals["expected_revenue"] = expected_revenue
    if probability is not None:
        vals["probability"] = probability
    if salesperson:
        vals["user_id"] = _user(env, salesperson)
    if stage:
        vals["stage_id"] = _stage(env, stage)
    lead = env["crm.lead"].create(vals)
    return {"created": _lead_out(lead)}


@tool("crm_lead_change", "crm", "Update a lead",
      "Update a lead or opportunity: stage, salesperson, revenue, probability, contact details, add tags, or convert "
      "a lead into an opportunity.",
      properties={"lead_id": s_int("Lead id."), "stage": s_str("New stage name."),
                  "salesperson": s_str("Login or name."),
                  "expected_revenue": {"type": "number"}, "probability": {"type": "number"},
                  "add_tags": s_arr("Tags to add.", {"type": "string"}),
                  "convert_to_opportunity": {"type": "boolean"},
                  "values": s_obj("Other crm.lead fields to set, e.g. {\"phone\": \"...\"}.")},
      required=("lead_id",), writes=True)
def crm_lead_change(ctx, lead_id, stage=None, salesperson=None, expected_revenue=None, probability=None,
                    add_tags=None, convert_to_opportunity=False, values=None):
    env = ctx.env
    lead = env["crm.lead"].with_context(active_test=False).browse(int(lead_id)).exists()
    if not lead:
        raise ToolError("Lead %s does not exist." % lead_id)
    vals = dict(values or {})
    if stage:
        vals["stage_id"] = _stage(env, stage)
    if salesperson:
        vals["user_id"] = _user(env, salesperson)
    if expected_revenue is not None:
        vals["expected_revenue"] = expected_revenue
    if probability is not None:
        vals["probability"] = probability
    if add_tags:
        vals["tag_ids"] = [(4, t) for t in _tags(env, add_tags)]
    if convert_to_opportunity:
        vals["type"] = "opportunity"
    if not vals:
        raise ToolError("Nothing to change.")
    lead.write(vals)
    return {"updated": _lead_out(lead)}


@tool("crm_lead_followup", "crm", "Log a note or plan a follow-up",
      "Add an internal note to a lead and/or schedule a follow-up activity (call, email, meeting, to-do).",
      properties={"lead_id": s_int("Lead id."), "note": s_str("Internal note."),
                  "activity": s_str("Activity type.", ["call", "email", "meeting", "todo"]),
                  "summary": s_str("Activity summary."), "due_date": s_str("YYYY-MM-DD."),
                  "assign_to": s_str("Login or name (default: the salesperson).")},
      required=("lead_id",), writes=True)
def crm_lead_followup(ctx, lead_id, note=None, activity=None, summary=None, due_date=None, assign_to=None):
    env = ctx.env
    lead = env["crm.lead"].browse(int(lead_id)).exists()
    if not lead:
        raise ToolError("Lead %s does not exist." % lead_id)
    out = {}
    if note:
        lead.message_post(body=note, message_type="comment", subtype_xmlid="mail.mt_note")
        out["note"] = "added"
    if activity:
        xmlid = {"call": "mail.mail_activity_data_call", "email": "mail.mail_activity_data_email",
                 "meeting": "mail.mail_activity_data_meeting", "todo": "mail.mail_activity_data_todo"}[activity]
        act = lead.activity_schedule(xmlid, date_deadline=due_date or None, summary=summary or False,
                                     user_id=_user(env, assign_to) or lead.user_id.id or env.uid)
        out["activity"] = {"id": act.id, "type": activity, "due": str(act.date_deadline)}
    if not out:
        raise ToolError("Give a note and/or an activity.")
    return out


@tool("crm_lead_close", "crm", "Mark won or lost",
      "Mark an opportunity as won, or as lost with a reason.",
      properties={"lead_id": s_int("Lead id."), "outcome": s_str("won or lost.", ["won", "lost"]),
                  "reason": s_str("Lost reason (name; created if missing).")},
      required=("lead_id", "outcome"), writes=True)
def crm_lead_close(ctx, lead_id, outcome, reason=None):
    env = ctx.env
    lead = env["crm.lead"].browse(int(lead_id)).exists()
    if not lead:
        raise ToolError("Lead %s does not exist." % lead_id)
    if outcome == "won":
        lead.action_set_won()
    else:
        vals = {}
        if reason:
            Reason = env["crm.lost.reason"]
            r = Reason.search([("name", "=ilike", reason)], limit=1) or Reason.create({"name": reason})
            vals["lost_reason_id"] = r.id
        lead.action_set_lost(**vals)
    return {"lead": _lead_out(lead.with_context(active_test=False)), "outcome": outcome}
