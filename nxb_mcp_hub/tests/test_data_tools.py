# -*- coding: utf-8 -*-
# Copyright 2026 Nextbridge. License OPL-1.
from odoo.tests import tagged
from odoo.tools import mute_logger

from .common import McpCase


@tagged("post_install", "-at_install", "nxb_mcp")
class TestDataTools(McpCase):

    def test_hub_tools(self):
        who = self.call("hub_whoami")
        self.assertEqual(who["login"], self.admin.login)
        self.assertEqual(who["access"], "read and write")
        dbs = self.call("hub_databases")["databases"]
        self.assertEqual(len(dbs), 1)
        self.assertTrue(dbs[0]["default"])
        caps = self.call("hub_capabilities")
        self.assertTrue(caps["packs"])
        self.assertIsInstance(caps["installed_apps"], list)
        apps = self.call("hub_installed_apps")
        self.assertIsInstance(apps, list)

    def test_models_and_fields(self):
        models = self.call("data_models", search="partner")
        self.assertIn("res.partner", [m["model"] for m in models])
        fields = self.call("data_fields", model="res.partner", search="email")
        self.assertIn("email", [f["name"] for f in fields["fields"]])
        req = self.call("data_fields", model="res.partner", required_only=True)
        self.assertTrue(all(f.get("required") for f in req["fields"]))
        self.assertIn("does not exist", self.call("data_fields", ok=False, model="no.such.model"))

    def test_create_search_read_update_delete(self):
        res = self.call("data_create", model="res.partner", values={"name": "Acme MCP", "email": "a@acme.test",
                                                                    "is_company": True})
        pid = res["created"][0]["id"]
        res = self.call("data_create", model="res.partner", records=[
            {"name": "Contact One", "parent_id": pid}, {"name": "Contact Two", "parent_id": pid}])
        self.assertEqual(len(res["created"]), 2)
        found = self.call("data_search", model="res.partner", domain=[["parent_id", "=", pid]],
                          fields=["name", "parent_id"], order="name")
        self.assertEqual(found["total"], 2)
        self.assertEqual(found["records"][0]["name"], "Contact One")
        self.assertEqual(found["records"][0]["parent_id"][0], pid)
        # string domain is accepted too
        found = self.call("data_search", model="res.partner", domain="[('id', '=', %d)]" % pid)
        self.assertEqual(found["total"], 1)
        self.assertIn("name", found["records"][0])  # default fields
        cnt = self.call("data_count", model="res.partner", domain=[["parent_id", "=", pid]])
        self.assertEqual(cnt["count"], 2)
        rec = self.call("data_read", model="res.partner", ids=[pid], fields=["name", "email"])
        self.assertEqual(rec["records"][0]["email"], "a@acme.test")
        full = self.call("data_read", model="res.partner", ids=[pid])
        self.assertIn("email", full["records"][0])
        upd = self.call("data_update", model="res.partner", ids=[pid, 99999999], values={"phone": "+1 555"})
        self.assertEqual(upd["updated"], [pid])
        self.assertEqual(upd["not_found"], [99999999])
        self.assertEqual(self.env["res.partner"].browse(pid).phone, "+1 555")
        look = self.call("data_lookup", model="res.partner", text="Acme MCP")
        self.assertEqual(look[0]["id"], pid)
        if self.installed("mail"):
            note = self.call("data_add_note", model="res.partner", id=pid, body="Called by the AI")
            self.assertTrue(note["posted"])
            self.assertIn("Called by the AI", self.env["res.partner"].browse(pid).message_ids[:1].body)
        else:
            self.assertIn("no chatter", self.call("data_add_note", ok=False, model="res.partner", id=pid, body="x"))
        dele = self.call("data_delete", model="res.partner", ids=[pid], confirm=True)
        self.assertEqual(dele["deleted"], [pid])

    def test_summarise(self):
        self.env["res.partner"].create([{"name": "S1", "is_company": True}, {"name": "S2", "is_company": True},
                                        {"name": "S3", "is_company": False}])
        res = self.call("data_summarise", model="res.partner", group_by=["is_company"], measures=["id:count"])
        self.assertTrue(res["groups"])
        self.assertTrue(all("count" in g for g in res["groups"]))
        res = self.call("data_summarise", model="res.partner", group_by=["create_date:month"])
        self.assertTrue(res["groups"])

    def test_run_action_and_guards(self):
        p = self.env["res.partner"].create({"name": "Archivable"})
        res = self.call("data_run_action", model="res.partner", method="action_archive", ids=[p.id])
        self.assertTrue(res["done"])
        self.assertFalse(p.active)
        self.assertIn("Private", self.call("data_run_action", ok=False, model="res.partner", method="_compute_x"))
        self.assertIn("not allowed", self.call("data_run_action", ok=False, model="res.partner", method="unlink",
                                               ids=[p.id]))

    def test_access_rights_respected(self):
        srv = self.server(user=self.user)
        out = self.call("data_create", ok=False, server=srv, model="res.users",
                        values={"name": "Hacker", "login": "hacker"})
        self.assertIn("Access", out)
        self.assertFalse(self.env["res.users"].search([("login", "=", "hacker")]))

    def test_companies_and_search_all(self):
        self.assertTrue(self.call("data_companies"))
        res = self.call("hub_search_all", model="res.partner", domain=[["id", "=", self.admin.partner_id.id]],
                        fields=["name"])
        self.assertEqual(res["per_database"][0]["result"]["total"], 1)
        res = self.call("hub_search_all", model="res.partner", count_only=True)
        self.assertGreater(res["per_database"][0]["result"]["count"], 0)

    def test_limits(self):
        self.env["ir.config_parameter"].sudo().set_param("nxb_mcp_hub.max_records", "3")
        res = self.call("data_search", model="res.partner", limit=100)
        self.assertLessEqual(res["returned"], 3)

    def test_sql_tools(self):
        tables = self.call("sql_tables", search="res_partner")
        self.assertIn("res_partner", [t["table"] for t in tables])
        info = self.call("sql_table_info", table="res_partner")
        self.assertIn("name", [c["column"] for c in info["columns"]])
        q = self.call("sql_query", query="SELECT id, name FROM res_partner ORDER BY id LIMIT 2")
        self.assertEqual(q["columns"], ["id", "name"])
        self.assertIn("Only SELECT", self.call("sql_query", ok=False, query="DELETE FROM res_partner"))
        self.assertIn("one statement", self.call("sql_query", ok=False, query="select 1; select 2"))
        self.assertIn("not allowed", self.call("sql_query", ok=False, query="select pg_read_file('/etc/passwd')"))
        with mute_logger("odoo.sql_db"):
            out = self.call("sql_query", ok=False,
                            query="WITH x AS (UPDATE res_partner SET name='z' WHERE id=1 RETURNING id) SELECT * FROM x")
        self.assertIn("read-only", out.lower())
        self.assertNotEqual(self.env["res.partner"].browse(1).name, "z")
