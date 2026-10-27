# -*- coding: utf-8 -*-
# Copyright 2026 Nextbridge. License OPL-1.
"""Tool and pack registry.

A *pack* is a group of tools an administrator can switch on or off. A *tool* is a
plain Python function registered with :func:`tool`. The MCP layer only talks to
this registry, so adding a tool is a single decorated function.
"""
import json
from collections import OrderedDict

PARAM_PREFIX = "nxb_mcp_hub."


class Pack(object):
    def __init__(self, key, title, group, summary, default=True, requires=None, parent=None,
                 risk=None, needs_app=False):
        self.key = key
        self.title = title
        self.group = group            # core / business / website / technical
        self.summary = summary
        self.default = default
        self.requires = requires      # Odoo module that must be installed in the target database
        self.parent = parent          # sub-switch of another pack
        self.risk = risk              # None / "write" / "high"
        self.needs_app = needs_app    # tools use this app's own models in the target database


PACKS = OrderedDict((p.key, p) for p in [
    Pack("hub", "Databases & discovery", "core",
         "List connected databases, explain what is available, search several databases at once.",
         default=True),
    Pack("read", "Read data", "core",
         "Find, read, count and summarise records of any model the user may see.", default=True),
    Pack("write", "Create & change data", "core",
         "Create, update and delete records and run model actions, within the user's rights.",
         default=True, risk="write"),
    Pack("dashboards", "Dashboards", "core",
         "Let the assistant build KPI and chart dashboards that open inside Odoo.",
         default=True, risk="write", needs_app=True),
    Pack("crm", "CRM & prospecting", "business",
         "Work with leads and opportunities, guided by prospecting briefs.",
         default=False, requires="crm", risk="write", needs_app=True),
    Pack("mailing", "Email campaigns", "business",
         "Build mailing lists, contacts and draft email campaigns from campaign briefs.",
         default=False, requires="mass_mailing", risk="write", needs_app=True),
    Pack("access", "Access control", "business",
         "Hide menus, fields, buttons and chatter, block models and add record rules.",
         default=False, risk="high", needs_app=True),
    Pack("seo", "Website SEO", "website",
         "Audit the website and fix titles, descriptions, URLs, redirects, indexing and alt text.",
         default=False, requires="website", risk="write", needs_app=True),
    Pack("designer", "Website builder", "website",
         "Edit website pages with Odoo's own building blocks, texts, images, theme and menus.",
         default=False, requires="website", risk="write", needs_app=True),
    Pack("designer_pro", "Website builder – expert mode", "website",
         "Raw HTML sections, custom CSS, head/footer code and copying an external page.",
         default=False, requires="website", parent="designer", risk="high", needs_app=True),
    Pack("sql", "SQL & schema", "technical",
         "Read-only SQL queries and table information for administrators.",
         default=False, risk="high"),
])

GROUP_TITLES = OrderedDict([
    ("core", "Core"),
    ("business", "Business apps"),
    ("website", "Website"),
    ("technical", "Technical"),
])

TOOLS = OrderedDict()


class Tool(object):
    __slots__ = ("name", "pack", "title", "description", "properties", "required", "func",
                 "writes", "destructive", "remote_ok", "admin_only")

    def __init__(self, name, pack, title, description, properties, required, func,
                 writes=False, destructive=False, remote_ok=False, admin_only=False):
        self.name = name
        self.pack = pack
        self.title = title
        self.description = description
        self.properties = properties
        self.required = required
        self.func = func
        self.writes = writes
        self.destructive = destructive
        self.remote_ok = remote_ok      # can run on a plain RPC connection (no app on the target)
        self.admin_only = admin_only

    def schema(self, databases=None):
        props = OrderedDict(self.properties)
        if databases and self.pack != "hub":
            props["database"] = {
                "type": "string",
                "enum": list(databases),
                "description": "Which connected database to use. Omit for the default database.",
            }
        out = {"type": "object", "properties": props}
        if self.required:
            out["required"] = list(self.required)
        return out

    def definition(self, databases=None):
        annotations = {"title": self.title, "readOnlyHint": not self.writes}
        if self.writes:
            annotations["destructiveHint"] = bool(self.destructive)
        return {
            "name": self.name,
            "title": self.title,
            "description": self.description,
            "inputSchema": self.schema(databases),
            "annotations": annotations,
        }

    def token_estimate(self):
        return max(1, int(len(json.dumps(self.definition())) / 4))


def tool(name, pack, title, description, properties=None, required=(), writes=False,
         destructive=False, remote_ok=False, admin_only=False):
    """Register a tool. The decorated function receives ``(ctx, **arguments)``."""
    assert pack in PACKS, pack

    def deco(func):
        assert name not in TOOLS, "duplicate tool %s" % name
        TOOLS[name] = Tool(name, pack, title, description.strip(), properties or {}, required, func,
                           writes=writes, destructive=destructive, remote_ok=remote_ok,
                           admin_only=admin_only)
        return func
    return deco


# --- small schema helpers --------------------------------------------------------------
def s_str(desc, enum=None, default=None):
    d = {"type": "string", "description": desc}
    if enum:
        d["enum"] = list(enum)
    if default is not None:
        d["default"] = default
    return d


def s_int(desc, default=None, minimum=None, maximum=None):
    d = {"type": "integer", "description": desc}
    if default is not None:
        d["default"] = default
    if minimum is not None:
        d["minimum"] = minimum
    if maximum is not None:
        d["maximum"] = maximum
    return d


def s_bool(desc, default=None):
    d = {"type": "boolean", "description": desc}
    if default is not None:
        d["default"] = default
    return d


def s_arr(desc, items=None):
    return {"type": "array", "description": desc, "items": items or {}}


def s_obj(desc, properties=None):
    d = {"type": "object", "description": desc}
    if properties:
        d["properties"] = properties
    return d


S_MODEL = s_str("Technical model name, e.g. res.partner or sale.order.")
S_DOMAIN = s_arr("Odoo search domain, e.g. [[\"country_id.code\", \"=\", \"ES\"]]. Empty list = all records.")
S_FIELDS = s_arr("Field names to return. Omit to get a compact default set.", {"type": "string"})
S_IDS = s_arr("Record ids.", {"type": "integer"})
S_VALUES = s_obj("Field values, e.g. {\"name\": \"Acme\", \"email\": \"info@acme.com\"}.")
S_CONFIRM = s_bool("Must be true. Confirms you told the user exactly what will be deleted or changed.")
