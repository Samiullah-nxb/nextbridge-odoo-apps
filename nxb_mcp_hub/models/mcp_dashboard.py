# -*- coding: utf-8 -*-
# Copyright 2026 Nextbridge. License OPL-1.
import json
from datetime import date, timedelta

from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError
from odoo.tools.safe_eval import safe_eval

KINDS = [("kpi", "Number (KPI)"), ("bar", "Bar chart"), ("line", "Line chart"), ("pie", "Pie chart"),
         ("table", "Grouped table"), ("list", "Record list")]
AGGS = [("count", "Count"), ("sum", "Sum"), ("avg", "Average"), ("min", "Minimum"), ("max", "Maximum")]
PERIODS = [("all", "All time"), ("today", "Today"), ("this_week", "This week"), ("this_month", "This month"),
           ("last_month", "Last month"), ("this_quarter", "This quarter"), ("this_year", "This year"),
           ("last_30", "Last 30 days"), ("last_90", "Last 90 days"), ("last_365", "Last 12 months")]
INTERVALS = [("day", "Day"), ("week", "Week"), ("month", "Month"), ("quarter", "Quarter"), ("year", "Year")]


def period_range(period, today=None):
    today = today or date.today()
    if period == "today":
        return today, today
    if period == "this_week":
        start = today - timedelta(days=today.weekday())
        return start, start + timedelta(days=6)
    if period == "this_month":
        start = today.replace(day=1)
        return start, start + relativedelta(months=1, days=-1)
    if period == "last_month":
        start = today.replace(day=1) - relativedelta(months=1)
        return start, today.replace(day=1) - timedelta(days=1)
    if period == "this_quarter":
        q = (today.month - 1) // 3
        start = date(today.year, q * 3 + 1, 1)
        return start, start + relativedelta(months=3, days=-1)
    if period == "this_year":
        return date(today.year, 1, 1), date(today.year, 12, 31)
    days = {"last_30": 30, "last_90": 90, "last_365": 365}.get(period)
    if days:
        return today - timedelta(days=days - 1), today
    return None, None


class McpDashboard(models.Model):
    _name = "nxb.mcp.dashboard"
    _description = "AI Connector: dashboard"
    _order = "sequence, id desc"

    name = fields.Char(required=True)
    description = fields.Text()
    sequence = fields.Integer(default=10)
    user_id = fields.Many2one("res.users", "Owner", default=lambda s: s.env.user, required=True, ondelete="cascade")
    shared = fields.Boolean("Visible to everyone", help="Shared dashboards are visible to all internal users. "
                                                         "Each viewer only sees figures they have access to.")
    widget_ids = fields.One2many("nxb.mcp.dashboard.widget", "dashboard_id", copy=True)
    widget_count = fields.Integer(compute="_compute_widget_count")
    created_by_ai = fields.Boolean("Created by the assistant", readonly=True)
    color = fields.Integer()

    def _compute_widget_count(self):
        for rec in self:
            rec.widget_count = len(rec.widget_ids)

    def action_open(self):
        self.ensure_one()
        return {"type": "ir.actions.client", "tag": "nxb_mcp_dashboard", "name": self.name,
                "params": {"dashboard_id": self.id}}

    def get_view_data(self):
        """Definition + computed figures for the viewer (runs with the viewer's rights)."""
        self.ensure_one()
        return {"id": self.id, "name": self.name, "description": self.description or "",
                "widgets": [w._render() for w in self.widget_ids.sorted("sequence")]}

    def _summary(self, with_data=False):
        self.ensure_one()
        out = {"id": self.id, "name": self.name, "description": self.description or "", "shared": self.shared,
               "owner": self.user_id.name, "widgets": [w._summary() for w in self.widget_ids.sorted("sequence")]}
        if with_data:
            out["figures"] = [w._render() for w in self.widget_ids.sorted("sequence")]
        return out


class McpDashboardWidget(models.Model):
    _name = "nxb.mcp.dashboard.widget"
    _description = "AI Connector: dashboard widget"
    _order = "sequence, id"

    dashboard_id = fields.Many2one("nxb.mcp.dashboard", required=True, ondelete="cascade", index=True)
    sequence = fields.Integer(default=10)
    name = fields.Char("Title", required=True)
    kind = fields.Selection(KINDS, required=True, default="kpi")
    model = fields.Char(required=True)
    domain = fields.Text(default="[]", help="Odoo domain as JSON or Python list.")
    measure = fields.Char(help="Numeric field to aggregate (empty = count records).")
    aggregate = fields.Selection(AGGS, default="count", required=True)
    group_by = fields.Char(help="Field to group by (charts and tables).")
    interval = fields.Selection(INTERVALS, help="For date group-by fields.")
    date_field = fields.Char(help="Date field used by the period filter.")
    period = fields.Selection(PERIODS, default="all", required=True)
    list_fields = fields.Char(help="Comma separated fields for record lists.")
    limit = fields.Integer(default=10)
    order = fields.Char(help="Sort order for lists, e.g. 'amount_total desc'.")
    width = fields.Selection([("1", "Quarter"), ("2", "Half"), ("3", "Three quarters"), ("4", "Full")],
                             default="1", required=True)
    prefix = fields.Char(help="Shown before KPI numbers, e.g. '$'.")
    suffix = fields.Char(help="Shown after KPI numbers, e.g. 'h'.")

    @api.constrains("model", "measure", "group_by", "date_field", "kind", "aggregate", "domain", "list_fields")
    def _check_definition(self):
        for w in self:
            w._validate()

    def _validate(self):
        self.ensure_one()
        if self.model not in self.env:
            raise ValidationError(_("Model %s does not exist.", self.model))
        Model = self.env[self.model]
        fields_ = Model._fields
        if self.measure:
            f = fields_.get(self.measure)
            if not f:
                raise ValidationError(_("Field %(f)s does not exist on %(m)s.", f=self.measure, m=self.model))
            if f.type not in ("integer", "float", "monetary"):
                raise ValidationError(_("Field %s is not numeric, it cannot be summed or averaged.", self.measure))
            if not f.store and not f.aggregator:
                raise ValidationError(_("Field %s is not stored, so it cannot be aggregated.", self.measure))
        elif self.aggregate != "count":
            raise ValidationError(_("Choose a numeric measure field, or use the count aggregate."))
        if self.kind in ("bar", "line", "pie", "table") and not self.group_by:
            raise ValidationError(_("Charts and tables need a group-by field."))
        if self.group_by:
            gb = fields_.get(self.group_by)
            if not gb:
                raise ValidationError(_("Group-by field %s does not exist.", self.group_by))
            if not gb.store and gb.type not in ("many2one",):
                raise ValidationError(_("Group-by field %s is not stored.", self.group_by))
        if self.date_field and (self.date_field not in fields_
                                or fields_[self.date_field].type not in ("date", "datetime")):
            raise ValidationError(_("%s is not a date field.", self.date_field))
        if self.period != "all" and not self.date_field:
            raise ValidationError(_("A period filter needs a date field."))
        if self.list_fields:
            for f in self._list_fields():
                if f not in fields_:
                    raise ValidationError(_("Field %s does not exist.", f))
        self._parsed_domain()

    def _list_fields(self):
        return [f.strip() for f in (self.list_fields or "").split(",") if f.strip()]

    def _parsed_domain(self):
        raw = (self.domain or "[]").strip() or "[]"
        try:
            dom = json.loads(raw)
        except ValueError:
            try:
                dom = safe_eval(raw)
            except Exception:  # noqa: BLE001
                raise ValidationError(_("The domain of widget %s is not valid.", self.name))
        if not isinstance(dom, list):
            raise ValidationError(_("The domain must be a list."))
        return dom

    def _full_domain(self):
        dom = list(self._parsed_domain())
        start, end = period_range(self.period)
        if start and self.date_field:
            ftype = self.env[self.model]._fields[self.date_field].type
            if ftype == "datetime":
                dom += [(self.date_field, ">=", fields.Datetime.to_string(fields.Datetime.to_datetime(start))),
                        (self.date_field, "<", fields.Datetime.to_string(
                            fields.Datetime.to_datetime(end + timedelta(days=1))))]
            else:
                dom += [(self.date_field, ">=", start), (self.date_field, "<=", end)]
        return dom

    def _agg_spec(self):
        if not self.measure or self.aggregate == "count":
            return "__count"
        return "%s:%s" % (self.measure, self.aggregate)

    def _render(self):
        self.ensure_one()
        out = {"id": self.id, "title": self.name, "kind": self.kind, "width": int(self.width), "model": self.model,
               "prefix": self.prefix or "", "suffix": self.suffix or "",
               "period": dict(PERIODS)[self.period]}
        try:
            Model = self.env[self.model]
            dom = self._full_domain()
            spec = self._agg_spec()
            if self.kind == "kpi":
                if spec == "__count":
                    out["value"] = Model.search_count(dom)
                else:
                    res = Model._read_group(dom, [], [spec])
                    out["value"] = res[0][0] if res and res[0][0] is not None else 0
            elif self.kind == "list":
                flist = self._list_fields() or ["display_name"]
                recs = Model.search(dom, limit=max(1, min(self.limit or 10, 100)), order=self.order or None)
                rows = recs.read(flist)
                out["columns"] = [{"name": f, "label": Model._fields[f].string} for f in flist]
                out["rows"] = [[_fmt(r[f]) for f in flist] for r in rows]
                out["ids"] = recs.ids
            else:
                gb = self.group_by + (":%s" % self.interval if self.interval and
                                      Model._fields[self.group_by].type in ("date", "datetime") else "")
                res = Model._read_group(dom, [gb], [spec], order=None if self.interval else "%s desc" % spec,
                                        limit=None if self.interval else max(1, min(self.limit or 10, 50)))
                labels, values = [], []
                for key, val in res:
                    labels.append(_label(key))
                    values.append(val or 0)
                out["labels"] = labels
                out["values"] = values
                out["measure_label"] = (Model._fields[self.measure].string if self.measure else _("Count")) + (
                    "" if spec == "__count" else " (%s)" % dict(AGGS)[self.aggregate].lower())
        except Exception as e:  # noqa: BLE001 — a bad widget must not break the dashboard
            out["error"] = str(e)
        return out

    def _summary(self):
        self.ensure_one()
        return {"widget_id": self.id, "title": self.name, "kind": self.kind, "model": self.model,
                "domain": self._parsed_domain(), "measure": self.measure or None, "aggregate": self.aggregate,
                "group_by": self.group_by or None, "interval": self.interval or None,
                "date_field": self.date_field or None, "period": self.period,
                "list_fields": self._list_fields() or None, "limit": self.limit, "order": self.order or None,
                "width": int(self.width), "prefix": self.prefix or None, "suffix": self.suffix or None}


def _label(key):
    if hasattr(key, "display_name"):
        return key.display_name or "None"
    if key in (False, None):
        return "None"
    if isinstance(key, (date,)):
        return fields.Date.to_string(key)
    return str(key)


def _fmt(v):
    if isinstance(v, (list, tuple)) and len(v) == 2 and isinstance(v[0], int):
        return v[1]
    if isinstance(v, (list, tuple)):
        return ", ".join(str(x) for x in v)
    if v is False or v is None:
        return ""
    if isinstance(v, date):
        return fields.Date.to_string(v) if not hasattr(v, "hour") else fields.Datetime.to_string(v)
    return v
