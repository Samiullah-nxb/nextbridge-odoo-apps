# -*- coding: utf-8 -*-
"""Shared helpers: URL normalisation, text fingerprints, settings access."""
import hashlib
import re
import unicodedata

PARAM_PREFIX = "nb_linkedin_ai_prospecting."

STAGES = [
    ("queued", "Queued"),
    ("on_hold", "On hold"),
    ("invited", "Invited"),
    ("followed", "Followed (no Connect)"),
    ("connected", "Connected"),
    ("conversation", "In conversation"),
    ("taken_over", "Taken over"),
    ("lead", "Warm lead (CRM)"),
    ("closed_no_response", "Closed: no response"),
    ("closed_not_interested", "Closed: not interested"),
    ("closed_withdrawn", "Closed: invite withdrawn"),
    ("closed_declined", "Closed: invite declined"),
    ("closed_referral", "Closed: referred"),
    ("do_not_contact", "Do not contact"),
]
CLOSED_STAGES = (
    "closed_no_response", "closed_not_interested", "closed_withdrawn",
    "closed_declined", "closed_referral", "do_not_contact",
)
ACTIVE_CHAT_STAGES = ("connected", "conversation")

INTENTS = [
    ("continue", "Continue questionnaire"),
    ("answer", "Answer question / topic"),
    ("pivot", "Pivot reached (hand to sales)"),
    ("request_contact", "Asked for call / email / meeting / quote"),
    ("wrong_person", "Not the right person (ask who)"),
    ("referral", "Referred to someone else"),
    ("not_interested", "Not interested"),
    ("no_need", "Needs nothing now (warm close)"),
]


def normalize_linkedin_url(url):
    """Return a canonical https://www.linkedin.com/in/<slug>/ URL, or the stripped input."""
    if not url:
        return False
    url = url.strip()
    m = re.search(r"linkedin\.com/(in|company)/([^/?#\s]+)", url, re.I)
    if not m:
        return url
    slug = m.group(2).strip().lower()
    return "https://www.linkedin.com/%s/%s/" % (m.group(1).lower(), slug)


def public_id_from_url(url):
    m = re.search(r"linkedin\.com/in/([^/?#\s]+)", url or "", re.I)
    return m.group(1).lower() if m else False


def text_fingerprint(text):
    """Fingerprint that ignores case, accents, punctuation and whitespace."""
    if not text:
        return ""
    t = unicodedata.normalize("NFKD", text)
    t = "".join(c for c in t if not unicodedata.combining(c)).lower()
    t = re.sub(r"[^a-z0-9]+", "", t)
    return hashlib.sha1(t.encode()).hexdigest()


def one_paragraph(text):
    """LinkedIn sends on Enter, so chat messages go out as a single paragraph."""
    return re.sub(r"\s*\n+\s*", " ", (text or "").strip())


def get_param(env, key, default=None):
    return env["ir.config_parameter"].sudo().get_param(PARAM_PREFIX + key, default)


def get_int_param(env, key, default):
    try:
        return int(get_param(env, key, default))
    except (TypeError, ValueError):
        return default


def is_odoo_sh():
    import os
    return bool(os.environ.get("ODOO_STAGE") or os.environ.get("ODOO_SH") or os.path.isdir("/home/odoo/src/odoo"))
