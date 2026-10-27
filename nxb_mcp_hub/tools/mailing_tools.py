# -*- coding: utf-8 -*-
# Copyright 2026 Nextbridge. License OPL-1.
"""Email campaign tools (needs the Email Marketing app in the target database)."""
from html import escape

from odoo import fields

from ..core.context import ToolError
from ..core.registry import S_CONFIRM, s_arr, s_int, s_obj, s_str, tool
from .crm_tools import _brief

SECTION = s_obj("A block of the email.", {
    "type": s_str("Block type.", ["heading", "text", "button", "image", "divider", "products"]),
    "text": s_str("Heading or paragraph text (plain text; blank lines start new paragraphs)."),
    "url": s_str("Link for buttons or images."),
    "image_url": s_str("Image address for image blocks."),
    "products": s_arr("Product names or references for a product block.", {"type": "string"}),
})


# ---------------------------------------------------------------------------- email builder
def _p(text):
    out = []
    for para in (text or "").split("\n\n"):
        para = escape(para.strip()).replace("\n", "<br/>")
        if para:
            out.append('<p style="margin:0 0 14px;font-size:15px;line-height:1.6;color:#333333;">%s</p>' % para)
    return "".join(out)


def _products_html(env, refs, link):
    if "product.product" not in env:
        raise ToolError("Product blocks need the Sales or Inventory app (products).")
    Product = env["product.product"]
    cards = []
    missing = []
    for ref in refs or []:
        prod = Product.search(["|", ("default_code", "=ilike", ref), ("name", "ilike", ref)], limit=1)
        if not prod:
            missing.append(ref)
            continue
        price = "%s %s" % (env.company.currency_id.symbol or "", "{:,.2f}".format(prod.lst_price))
        img = ""
        base = env["ir.config_parameter"].sudo().get_param("web.base.url", "").rstrip("/")
        if prod.image_256:
            img = ('<img src="%s/web/image/product.product/%d/image_256" alt="%s" width="160" '
                   'style="display:block;margin:0 auto 8px;border-radius:6px;max-width:100%%;"/>'
                   % (base, prod.id, escape(prod.name)))
        cards.append(
            '<td width="50%%" valign="top" style="padding:8px;"><div style="border:1px solid #e6e6e6;border-radius:8px;'
            'padding:12px;text-align:center;">%s<div style="font-weight:bold;font-size:15px;color:#222;">%s</div>'
            '<div style="color:#555;font-size:14px;margin:4px 0 10px;">%s</div>%s</div></td>'
            % (img, escape(prod.name), escape(price),
               ('<a href="%s" style="font-size:14px;color:#4c56d6;">View</a>' % escape(link)) if link else ""))
    rows = ["<tr>%s</tr>" % "".join(cards[i:i + 2]) for i in range(0, len(cards), 2)]
    return '<table width="100%%" cellpadding="0" cellspacing="0">%s</table>' % "".join(rows), missing


def build_email(env, sections, preview=""):
    blocks, missing = [], []
    for s in sections or []:
        t = s.get("type") or "text"
        if t == "heading":
            blocks.append('<h1 style="margin:0 0 14px;font-size:24px;line-height:1.3;color:#111111;">%s</h1>'
                          % escape(s.get("text") or ""))
        elif t == "text":
            blocks.append(_p(s.get("text")))
        elif t == "button":
            blocks.append('<p style="margin:18px 0;"><a href="%s" style="background:#4c56d6;color:#ffffff;'
                          'padding:12px 22px;border-radius:6px;text-decoration:none;font-weight:bold;'
                          'display:inline-block;">%s</a></p>' % (escape(s.get("url") or "#"),
                                                                  escape(s.get("text") or "Learn more")))
        elif t == "image":
            img = '<img src="%s" alt="%s" style="display:block;width:100%%;max-width:600px;border-radius:6px;"/>' % (
                escape(s.get("image_url") or ""), escape(s.get("text") or ""))
            if s.get("url"):
                img = '<a href="%s">%s</a>' % (escape(s["url"]), img)
            blocks.append('<div style="margin:0 0 16px;">%s</div>' % img)
        elif t == "divider":
            blocks.append('<hr style="border:0;border-top:1px solid #e6e6e6;margin:20px 0;"/>')
        elif t == "products":
            html, miss = _products_html(env, s.get("products"), s.get("url"))
            blocks.append(html)
            missing += miss
        else:
            raise ToolError("Unknown block type %r." % t)
    pre = ('<div style="display:none;max-height:0;overflow:hidden;">%s</div>' % escape(preview)) if preview else ""
    body = ('<div style="background:#f4f5f7;padding:24px 0;">%s<table align="center" width="600" cellpadding="0" '
            'cellspacing="0" style="max-width:600px;width:100%%;background:#ffffff;border-radius:10px;">'
            '<tr><td style="padding:28px 32px;font-family:Arial,Helvetica,sans-serif;">%s</td></tr></table></div>'
            % (pre, "".join(blocks)))
    return body, missing


def _lists(env, refs, create=False):
    List = env["mailing.list"]
    out = List.browse()
    for ref in refs or []:
        if isinstance(ref, int) or (isinstance(ref, str) and ref.isdigit()):
            rec = List.browse(int(ref)).exists()
        else:
            rec = List.search([("name", "=ilike", ref)], limit=1)
            if not rec and create:
                rec = List.create({"name": ref})
        if not rec:
            raise ToolError("Mailing list %r not found." % ref)
        out |= rec
    return out


def _mailing_out(m):
    return {"id": m.id, "subject": m.subject, "state": m.state, "preview": m.preview or "",
            "lists": m.contact_list_ids.mapped("name"), "scheduled_for": fields.Datetime.to_string(m.schedule_date)
            if m.schedule_date else None,
            "stats": {"sent": m.sent, "delivered": m.delivered, "opened": m.opened, "clicked": m.clicked,
                      "replied": m.replied, "bounced": m.bounced}}


@tool("mail_guide", "mailing", "How to write campaigns",
      "Guidance for good email campaigns and the block types the campaign tools accept.")
def mail_guide(ctx):
    return {
        "blocks": {"heading": "Main title", "text": "Paragraphs (blank line = new paragraph)",
                   "button": "Call to action: text + url", "image": "image_url (+ optional url)",
                   "divider": "Horizontal line", "products": "Product cards: products = names or references"},
        "tips": ["Subject 30-50 characters; preview text adds context, not a repeat of the subject.",
                 "One main call to action, near the top and repeated at the end.",
                 "Short paragraphs; write for the audience in the brief.",
                 "Always create campaigns as drafts and let a person review and send them."],
    }


@tool("mail_brief_read", "mailing", "Read a campaign brief",
      "Read an email campaign brief: goal, audience, message, products to feature, call to action, link and tone. "
      "Without an id, returns the newest brief that is ready.",
      properties={"brief_id": s_int("Brief id (optional).")})
def mail_brief_read(ctx, brief_id=None):
    out = _brief(ctx, brief_id, "campaign")._for_assistant()
    out["how_to"] = ("Prepare the campaign as a draft with mail_campaign_draft (use a products block for the "
                     "brief's products). Do not send it. Then report with mail_brief_done.")
    return out


@tool("mail_lists", "mailing", "List mailing lists",
      "List mailing lists with their number of contacts.",
      properties={"search": s_str("Only lists whose name contains this text.")})
def mail_lists(ctx, search=None):
    dom = [("name", "ilike", search)] if search else []
    return [{"id": l.id, "name": l.name, "contacts": l.contact_count}
            for l in ctx.env["mailing.list"].search(dom, limit=200)]


@tool("mail_list_create", "mailing", "Create a mailing list",
      "Create a mailing list (returns the existing one if the name is taken).",
      properties={"name": s_str("List name.")}, required=("name",), writes=True)
def mail_list_create(ctx, name):
    lst = _lists(ctx.env, [name], create=True)
    return {"id": lst.id, "name": lst.name, "contacts": lst.contact_count}


@tool("mail_contact_add", "mailing", "Add contacts to lists",
      "Add contacts to one or more mailing lists. Existing contacts (same email) are reused and just subscribed.",
      properties={"lists": s_arr("List names or ids (created if missing).", {"type": "string"}),
                  "contacts": s_arr("Contacts.", s_obj("Contact", {"name": s_str("Name"), "email": s_str("Email"),
                                                                   "company": s_str("Company")}))},
      required=("lists", "contacts"), writes=True)
def mail_contact_add(ctx, lists, contacts):
    env = ctx.env
    lsts = _lists(env, lists, create=True)
    Contact = env["mailing.contact"]
    added, reused, skipped = 0, 0, []
    for c in contacts or []:
        email = (c.get("email") or "").strip().lower()
        if not email or "@" not in email:
            skipped.append(c.get("name") or email or "?")
            continue
        rec = Contact.search([("email", "=ilike", email)], limit=1)
        if rec:
            rec.write({"list_ids": [(4, l.id) for l in lsts]})
            reused += 1
        else:
            Contact.create({"name": c.get("name") or email, "email": email, "company_name": c.get("company") or False,
                            "list_ids": [(6, 0, lsts.ids)]})
            added += 1
    return {"lists": lsts.mapped("name"), "new_contacts": added, "existing_subscribed": reused,
            "skipped_without_email": skipped}


@tool("mail_contact_find", "mailing", "Find mailing contacts",
      "Find mailing contacts by name, email or company, optionally in one list.",
      properties={"text": s_str("Text to look for."), "list": s_str("List name or id."),
                  "limit": s_int("Maximum results.", 30, 1, 500)})
def mail_contact_find(ctx, text=None, list=None, limit=30):
    env = ctx.env
    dom = []
    if text:
        dom += ["|", "|", ("name", "ilike", text), ("email", "ilike", text), ("company_name", "ilike", text)]
    if list:
        dom.append(("list_ids", "in", _lists(env, [list]).ids))
    recs = env["mailing.contact"].search(dom, limit=ctx.cap_limit(limit, 30))
    return {"total": env["mailing.contact"].search_count(dom),
            "contacts": [{"id": r.id, "name": r.name, "email": r.email, "company": r.company_name or "",
                          "lists": r.list_ids.mapped("name")} for r in recs]}


@tool("mail_campaign_draft", "mailing", "Draft a campaign",
      "Create an email campaign as a DRAFT for the given mailing lists, from content blocks (or ready HTML). "
      "Nothing is sent. Returns the id and a link for the user to review.",
      properties={"subject": s_str("Subject line."), "preview": s_str("Preview text shown after the subject."),
                  "lists": s_arr("Mailing list names or ids.", {"type": "string"}),
                  "sections": s_arr("Content blocks in order.", SECTION),
                  "html": s_str("Complete HTML body instead of sections (advanced)."),
                  "brief_id": s_int("Brief this campaign comes from.")},
      required=("subject", "lists"), writes=True)
def mail_campaign_draft(ctx, subject, lists, sections=None, html=None, preview=None, brief_id=None):
    env = ctx.env
    if not sections and not html:
        raise ToolError("Give 'sections' (recommended) or 'html'.")
    lsts = _lists(env, lists)
    missing = []
    if sections:
        html, missing = build_email(env, sections, preview or "")
    model = env["ir.model"]._get("mailing.list")
    m = env["mailing.mailing"].create({
        "subject": subject, "preview": preview or False, "body_arch": html, "body_html": html,
        "mailing_model_id": model.id, "contact_list_ids": [(6, 0, lsts.ids)], "mailing_type": "mail"})
    if brief_id and env["nxb.mcp.brief"].browse(int(brief_id)).exists():
        m.message_post(body="Drafted by the AI assistant from brief #%s." % brief_id, subtype_xmlid="mail.mt_note")
    base = env["ir.config_parameter"].sudo().get_param("web.base.url", "").rstrip("/")
    out = _mailing_out(m)
    out["review_link"] = "%s/odoo/mailing.mailing/%d" % (base, m.id)
    if missing:
        out["products_not_found"] = missing
    return out


@tool("mail_campaign_update", "mailing", "Update a draft campaign",
      "Change the subject, preview, lists or content of a campaign that is still a draft.",
      properties={"mailing_id": s_int("Campaign id."), "subject": s_str("New subject."),
                  "preview": s_str("New preview text."), "lists": s_arr("Replace the lists.", {"type": "string"}),
                  "sections": s_arr("Replace the content with these blocks.", SECTION),
                  "html": s_str("Replace the content with this HTML.")},
      required=("mailing_id",), writes=True)
def mail_campaign_update(ctx, mailing_id, subject=None, preview=None, lists=None, sections=None, html=None):
    env = ctx.env
    m = env["mailing.mailing"].browse(int(mailing_id)).exists()
    if not m:
        raise ToolError("Campaign %s does not exist." % mailing_id)
    if m.state != "draft":
        raise ToolError("Only draft campaigns can be changed (this one is %s)." % m.state)
    vals = {}
    if subject:
        vals["subject"] = subject
    if preview is not None:
        vals["preview"] = preview
    if lists:
        vals["contact_list_ids"] = [(6, 0, _lists(env, lists).ids)]
    if sections:
        html, _missing = build_email(env, sections, preview or m.preview or "")
    if html:
        vals.update(body_arch=html, body_html=html)
    if not vals:
        raise ToolError("Nothing to change.")
    m.write(vals)
    return _mailing_out(m)


@tool("mail_campaign_find", "mailing", "Find campaigns",
      "List email campaigns with status and results (sent, opened, clicked, replied, bounced).",
      properties={"text": s_str("Subject contains."),
                  "state": s_str("Status.", ["draft", "in_queue", "sending", "done"]),
                  "limit": s_int("Maximum results.", 20, 1, 200)})
def mail_campaign_find(ctx, text=None, state=None, limit=20):
    dom = [("mailing_type", "=", "mail")]
    if text:
        dom.append(("subject", "ilike", text))
    if state:
        dom.append(("state", "=", state))
    recs = ctx.env["mailing.mailing"].search(dom, limit=ctx.cap_limit(limit, 20), order="create_date desc")
    return [_mailing_out(m) for m in recs]


@tool("mail_campaign_send", "mailing", "Send or schedule a campaign",
      "Send a draft campaign now, or schedule it. Emails cannot be recalled: confirm with the user first and pass "
      "confirm=true.",
      properties={"mailing_id": s_int("Campaign id."), "send_at": s_str("YYYY-MM-DD HH:MM (UTC); omit = now."),
                  "confirm": S_CONFIRM},
      required=("mailing_id", "confirm"), writes=True)
def mail_campaign_send(ctx, mailing_id, confirm=False, send_at=None):
    if confirm is not True:
        raise ToolError("Sending cannot be undone. Confirm with the user, then call again with confirm=true.")
    env = ctx.env
    m = env["mailing.mailing"].browse(int(mailing_id)).exists()
    if not m:
        raise ToolError("Campaign %s does not exist." % mailing_id)
    if m.state != "draft":
        raise ToolError("Campaign is already %s." % m.state)
    if send_at:
        when = send_at.strip().replace("T", " ")
        if len(when) == 16:
            when += ":00"
        try:
            when = fields.Datetime.to_datetime(when)
        except ValueError:
            raise ToolError("send_at must look like 2026-05-01 09:00.")
        m.write({"schedule_type": "scheduled", "schedule_date": when})
        m.action_put_in_queue()
    else:
        m.action_launch()
    return _mailing_out(m)


@tool("mail_brief_done", "mailing", "Report on a campaign brief",
      "Mark a campaign brief as done and store a short summary of what you prepared.",
      properties={"brief_id": s_int("Brief id."), "summary": s_str("What you prepared.")},
      required=("brief_id", "summary"), writes=True)
def mail_brief_done(ctx, brief_id, summary):
    env = ctx.require_env("Briefs")
    brief = env["nxb.mcp.brief"].browse(int(brief_id)).exists()
    if not brief:
        raise ToolError("Brief %s does not exist." % brief_id)
    brief.write({"state": "done", "result_note": summary})
    return {"brief_id": brief.id, "state": "done"}
