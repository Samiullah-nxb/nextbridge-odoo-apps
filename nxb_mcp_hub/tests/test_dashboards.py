# -*- coding: utf-8 -*-
# Copyright 2026 Nextbridge. License OPL-1.
from odoo.tests import tagged

from .common import McpCase


@tagged("post_install", "-at_install", "nxb_mcp")
class TestDashboards(McpCase):

    def test_dashboard_lifecycle(self):
        self.env["res.partner"].create([{"name": "D%d" % i, "is_company": bool(i % 2)} for i in range(6)])
        guide = self.call("dashboard_guide")
        self.assertIn("kpi", guide["widget_kinds"])
        res = self.call("dashboard_create", name="Partners", description="Test", shared=True, widgets=[
            {"title": "Companies", "kind": "kpi", "model": "res.partner", "domain": [["is_company", "=", True]]},
            {"title": "New per month", "kind": "line", "model": "res.partner", "group_by": "create_date",
             "interval": "month", "date_field": "create_date", "period": "last_365"},
            {"title": "By type", "kind": "pie", "model": "res.partner", "group_by": "is_company"},
            {"title": "By country", "kind": "table", "model": "res.partner", "group_by": "country_id"},
            {"title": "Latest", "kind": "list", "model": "res.partner", "list_fields": ["name", "email"],
             "order": "id desc", "limit": 5},
        ])
        did = res["id"]
        self.assertIn("/odoo/", res["open_in_odoo"])
        figs = {f["title"]: f for f in res["figures"]}
        self.assertTrue(all("error" not in f for f in figs.values()), figs)
        self.assertGreaterEqual(figs["Companies"]["value"], 3)
        self.assertTrue(figs["New per month"]["labels"])
        self.assertEqual(len(figs["Latest"]["rows"]), 5)
        self.assertIn(did, [d["id"] for d in self.call("dashboard_list")])

        shown = self.call("dashboard_show", dashboard_id=did)
        wid = shown["widgets"][0]["widget_id"]
        self.call("dashboard_edit", dashboard_id=did, name="Partners 2")
        self.assertEqual(self.env["nxb.mcp.dashboard"].browse(did).name, "Partners 2")
        add = self.call("dashboard_widget_add", dashboard_id=did, position=1,
                        widget={"title": "All", "kind": "kpi", "model": "res.partner"})
        self.assertIn("value", add["figures"])
        dash = self.env["nxb.mcp.dashboard"].browse(did)
        self.assertEqual(dash.widget_ids.sorted("sequence")[0].name, "All")
        edit = self.call("dashboard_widget_edit", widget_id=wid, changes={"title": "Companies (edited)",
                                                                          "suffix": " co."})
        self.assertEqual(edit["widget"]["title"], "Companies (edited)")
        self.call("dashboard_widget_remove", widget_id=wid)
        self.assertFalse(self.env["nxb.mcp.dashboard.widget"].browse(wid).exists())
        view = dash.get_view_data()
        self.assertEqual(len(view["widgets"]), 5)
        self.call("dashboard_delete", dashboard_id=did, confirm=True)
        self.assertFalse(dash.exists())

    def test_validation(self):
        out = self.call("dashboard_create", ok=False, name="Bad", widgets=[
            {"title": "x", "kind": "bar", "model": "res.partner"}])
        self.assertIn("group-by", out)
        out = self.call("dashboard_create", ok=False, name="Bad", widgets=[
            {"title": "x", "kind": "kpi", "model": "res.partner", "measure": "name", "aggregate": "sum"}])
        self.assertIn("not numeric", out)
        out = self.call("dashboard_create", ok=False, name="Bad", widgets=[
            {"title": "x", "kind": "kpi", "model": "nope.model"}])
        self.assertIn("does not exist", out)
        self.assertFalse(self.env["nxb.mcp.dashboard"].search([("name", "=", "Bad")]))

    def test_privacy(self):
        mine = self.env["nxb.mcp.dashboard"].create({"name": "Private", "user_id": self.admin.id, "widget_ids": [
            (0, 0, {"name": "n", "kind": "kpi", "model": "res.partner"})]})
        srv = self.server(user=self.user)
        self.assertNotIn(mine.id, [d["id"] for d in self.call("dashboard_list", server=srv)])
        self.call("dashboard_show", ok=False, server=srv, dashboard_id=mine.id)
        mine.shared = True
        self.assertIn(mine.id, [d["id"] for d in self.call("dashboard_list", server=srv)])
        self.call("dashboard_edit", ok=False, server=srv, dashboard_id=mine.id, name="hijack")
