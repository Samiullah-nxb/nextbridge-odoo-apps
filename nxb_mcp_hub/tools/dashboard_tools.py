# -*- coding: utf-8 -*-
# Copyright 2026 Nextbridge. License OPL-1.
"""Dashboard tools."""
import json

from ..core.context import ToolError
from ..core.registry import S_CONFIRM, s_arr, s_bool, s_int, s_obj, s_str, tool
from ..models.mcp_dashboard import AGGS, INTERVALS, KINDS, PERIODS

WIDGET_SCHEMA = s_obj("A widget.", {
    "title": s_str("Title shown on the widget."),
    "kind": s_str("Widget type.", [k for k, _l in KINDS]),
    "model": s_str("Model, e.g. sale.order."),
    "domain": s_arr("Filter as an Odoo domain."),
    "measure": s_str("Numeric field to aggregate; omit to count records."),
    "aggregate": s_str("How to aggregate the measure.", [k for k, _l in AGGS]),
    "group_by": s_str("Field to group by (required for charts and tables)."),
    "interval": s_str("Date grouping when group_by is a date.", [k for k, _l in INTERVALS]),
    "date_field": s_str("Date field the period filter applies to."),
    "period": s_str("Period filter.", [k for k, _l in PERIODS]),
    "list_fields": s_arr("Fields shown in a record list.", {"type": "string"}),
    "limit": s_int("Rows or bars shown (default 10)."),
    "order": s_str("Sort order for record lists."),
    "width": s_int("Width on a 4-column grid (1-4).", None, 1, 4),
    "prefix": s_str("Text before a KPI number, e.g. '€'."),
    "suffix": s_str("Text after a KPI number."),
})


def _widget_vals(w):
    if not isinstance(w, dict):
        raise ToolError("Each widget must be an object.")
    if not w.get("title") or not w.get("model"):
        raise ToolError("Each widget needs at least a title and a model.")
    vals = {
        "name": w["title"], "kind": w.get("kind") or "kpi", "model": w["model"],
        "domain": json.dumps(w.get("domain") or []), "measure": w.get("measure") or False,
        "aggregate": w.get("aggregate") or ("sum" if w.get("measure") else "count"),
        "group_by": w.get("group_by") or False, "interval": w.get("interval") or False,
        "date_field": w.get("date_field") or False, "period": w.get("period") or "all",
        "list_fields": ",".join(w.get("list_fields") or []) or False, "limit": int(w.get("limit") or 10),
        "order": w.get("order") or False, "width": str(int(w.get("width") or (2 if w.get("kind") in
                                                                                   ("bar", "line", "list", "table") else 1))),
        "prefix": w.get("prefix") or False, "suffix": w.get("suffix") or False,
    }
    for key, allowed in (("kind", KINDS), ("aggregate", AGGS), ("period", PERIODS)):
        if vals[key] not in dict(allowed):
            raise ToolError("Invalid %s %r. Allowed: %s." % (key, vals[key], ", ".join(dict(allowed))))
    if vals["interval"] and vals["interval"] not in dict(INTERVALS):
        raise ToolError("Invalid interval %r." % vals["interval"])
    return vals


def _get(ctx, dashboard_id):
    env = ctx.require_env("Dashboards")
    dash = env["nxb.mcp.dashboard"].search([("id", "=", int(dashboard_id))], limit=1)
    if not dash:
        raise ToolError("Dashboard %s does not exist or you cannot see it." % dashboard_id)
    return dash


def _url(ctx, dash):
    base = ctx.env["ir.config_parameter"].sudo().get_param("web.base.url", "").rstrip("/")
    return "%s/odoo/action-nxb_mcp_hub.action_nxb_mcp_dashboards/%d/action-nxb_mcp_hub.action_nxb_mcp_dashboard_view" % (
        base, dash.id)


@tool("dashboard_guide", "dashboards", "How to design dashboards",
      "Explain the widget types, options and good practice for building dashboards. Read this once before "
      "creating a dashboard.")
def dashboard_guide(ctx):
    return {
        "widget_kinds": {
            "kpi": "One big number: count of records or sum/average of a measure. Width 1.",
            "bar": "Compare categories (stage, salesperson, product). Needs group_by. Width 2.",
            "line": "Trends over time: group_by a date field with interval month/week. Width 2-4.",
            "pie": "Share of a total over a few categories (max ~6). Needs group_by.",
            "table": "Same as bar but shown as a table of totals.",
            "list": "Top records, e.g. 10 largest open invoices: list_fields + order + limit.",
        },
        "periods": [k for k, _l in PERIODS],
        "aggregates": [k for k, _l in AGGS],
        "tips": [
            "Check the model and fields with the field tool first; measures must be stored numeric fields.",
            "Use date_field + period to keep figures current (e.g. this_month on date_order).",
            "Filter states with domain, e.g. [[\"state\", \"in\", [\"sale\", \"done\"]]] for confirmed sales.",
            "Start with 3-4 KPIs in one row, then charts at width 2.",
            "Each viewer only sees figures for records they may access.",
        ],
        "example": {"title": "Revenue this month", "kind": "kpi", "model": "sale.order",
                    "domain": [["state", "in", ["sale", "done"]]], "measure": "amount_total", "aggregate": "sum",
                    "date_field": "date_order", "period": "this_month", "prefix": "$"},
    }


@tool("dashboard_list", "dashboards", "List dashboards",
      "List the dashboards you can see, with their widgets.")
def dashboard_list(ctx):
    env = ctx.require_env("Dashboards")
    return [{"id": d.id, "name": d.name, "widgets": d.widget_count, "shared": d.shared, "owner": d.user_id.name}
            for d in env["nxb.mcp.dashboard"].search([])]


@tool("dashboard_show", "dashboards", "Show a dashboard",
      "Return a dashboard's definition and its current figures, so you can check the result or answer questions "
      "from it.", properties={"dashboard_id": s_int("Dashboard id.")}, required=("dashboard_id",))
def dashboard_show(ctx, dashboard_id):
    dash = _get(ctx, dashboard_id)
    out = dash._summary(with_data=True)
    out["open_in_odoo"] = _url(ctx, dash)
    return out


@tool("dashboard_create", "dashboards", "Create a dashboard",
      "Create a dashboard with its widgets in one call. Returns the link to open it in Odoo and the computed "
      "figures, so you can verify every widget works.",
      properties={"name": s_str("Dashboard title."), "description": s_str("What it is for."),
                  "widgets": s_arr("Widgets in display order.", WIDGET_SCHEMA),
                  "shared": s_bool("Visible to all internal users.", False)},
      required=("name", "widgets"), writes=True)
def dashboard_create(ctx, name, widgets, description=None, shared=False):
    env = ctx.require_env("Dashboards")
    if not widgets:
        raise ToolError("Add at least one widget.")
    lines = []
    for i, w in enumerate(widgets):
        vals = _widget_vals(w)
        vals["sequence"] = (i + 1) * 10
        lines.append((0, 0, vals))
    dash = env["nxb.mcp.dashboard"].create({"name": name, "description": description or False, "shared": shared,
                                            "widget_ids": lines, "created_by_ai": True})
    out = dash._summary(with_data=True)
    out["open_in_odoo"] = _url(ctx, dash)
    return out


@tool("dashboard_edit", "dashboards", "Rename or share a dashboard",
      "Change a dashboard's title, description or sharing.",
      properties={"dashboard_id": s_int("Dashboard id."), "name": s_str("New title."),
                  "description": s_str("New description."), "shared": s_bool("Visible to everyone.")},
      required=("dashboard_id",), writes=True)
def dashboard_edit(ctx, dashboard_id, name=None, description=None, shared=None):
    dash = _get(ctx, dashboard_id)
    vals = {k: v for k, v in (("name", name), ("description", description), ("shared", shared)) if v is not None}
    if not vals:
        raise ToolError("Nothing to change.")
    dash.write(vals)
    return dash._summary()


@tool("dashboard_widget_add", "dashboards", "Add a widget",
      "Add a widget to an existing dashboard (at the end, or at a position).",
      properties={"dashboard_id": s_int("Dashboard id."), "widget": WIDGET_SCHEMA,
                  "position": s_int("1-based position; omit to append.")},
      required=("dashboard_id", "widget"), writes=True)
def dashboard_widget_add(ctx, dashboard_id, widget, position=None):
    dash = _get(ctx, dashboard_id)
    vals = _widget_vals(widget)
    widgets = dash.widget_ids.sorted("sequence")
    for i, w in enumerate(widgets):
        w.sequence = (i + 1) * 10 + (10 if position and i + 1 >= position else 0)
    vals["sequence"] = position * 10 if position else (len(widgets) + 1) * 10
    vals["dashboard_id"] = dash.id
    new = ctx.env["nxb.mcp.dashboard.widget"].create(vals)
    return {"added": new._summary(), "figures": new._render()}


@tool("dashboard_widget_edit", "dashboards", "Change a widget",
      "Change one or more settings of a widget (same keys as when creating it).",
      properties={"widget_id": s_int("Widget id (see dashboard_show)."), "changes": WIDGET_SCHEMA},
      required=("widget_id", "changes"), writes=True)
def dashboard_widget_edit(ctx, widget_id, changes):
    env = ctx.require_env("Dashboards")
    w = env["nxb.mcp.dashboard.widget"].browse(int(widget_id)).exists()
    if not w:
        raise ToolError("Widget %s does not exist." % widget_id)
    merged = dict(w._summary())
    merged["title"] = merged.pop("title")
    merged.update(changes or {})
    w.write(_widget_vals(merged))
    return {"widget": w._summary(), "figures": w._render()}


@tool("dashboard_widget_remove", "dashboards", "Remove a widget",
      "Remove a widget from its dashboard.", properties={"widget_id": s_int("Widget id.")},
      required=("widget_id",), writes=True)
def dashboard_widget_remove(ctx, widget_id):
    env = ctx.require_env("Dashboards")
    w = env["nxb.mcp.dashboard.widget"].browse(int(widget_id)).exists()
    if not w:
        raise ToolError("Widget %s does not exist." % widget_id)
    title = w.name
    w.unlink()
    return {"removed": title}


@tool("dashboard_delete", "dashboards", "Delete a dashboard",
      "Delete a whole dashboard. Tell the user which one first, then call with confirm=true.",
      properties={"dashboard_id": s_int("Dashboard id."), "confirm": S_CONFIRM},
      required=("dashboard_id", "confirm"), writes=True, destructive=True)
def dashboard_delete(ctx, dashboard_id, confirm=False):
    dash = _get(ctx, dashboard_id)
    name = dash.name
    dash.unlink()
    return {"deleted": name}
