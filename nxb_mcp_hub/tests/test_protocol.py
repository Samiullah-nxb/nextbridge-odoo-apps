# -*- coding: utf-8 -*-
# Copyright 2026 Nextbridge. License OPL-1.
from odoo.tests import tagged

from ..core.registry import PACKS, PARAM_PREFIX, TOOLS
from .common import McpCase


@tagged("post_install", "-at_install", "nxb_mcp")
class TestProtocol(McpCase):

    def test_initialize_and_ping(self):
        srv = self.server()
        res = srv.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                          "params": {"protocolVersion": "2025-03-26", "clientInfo": {"name": "t"}}})
        self.assertEqual(res["result"]["protocolVersion"], "2025-03-26")
        self.assertIn("tools", res["result"]["capabilities"])
        self.assertIn("Databases available", res["result"]["instructions"])
        # unknown version -> newest supported
        res = srv.handle({"jsonrpc": "2.0", "id": 2, "method": "initialize", "params": {"protocolVersion": "1999"}})
        self.assertEqual(res["result"]["protocolVersion"], "2025-06-18")
        self.assertEqual(srv.handle({"jsonrpc": "2.0", "id": 3, "method": "ping"})["result"], {})

    def test_notifications_and_errors(self):
        srv = self.server()
        self.assertIsNone(srv.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}))
        self.assertEqual(srv.handle({"jsonrpc": "2.0", "id": 1, "method": "nope"})["error"]["code"], -32601)
        self.assertEqual(srv.handle({"foo": 1})["error"]["code"], -32600)
        self.assertIsNone(srv.handle({"jsonrpc": "2.0", "id": 9, "result": {}}))

    def test_tools_list_all_packs(self):
        res = self.server().handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        names = {t["name"] for t in res["result"]["tools"]}
        expected = {t.name for t in TOOLS.values()}
        self.assertEqual(names, expected)
        for t in res["result"]["tools"]:
            self.assertEqual(t["inputSchema"]["type"], "object")
            self.assertTrue(t["description"])
            self.assertIn("readOnlyHint", t["annotations"])

    def test_every_tool_has_valid_schema(self):
        for t in TOOLS.values():
            schema = t.schema(["a", "b"])
            for req in schema.get("required", []):
                self.assertIn(req, schema["properties"], "%s requires undeclared %s" % (t.name, req))
            if t.pack != "hub":
                self.assertIn("database", schema["properties"], t.name)

    def test_pack_switch_hides_and_refuses(self):
        icp = self.env["ir.config_parameter"].sudo()
        icp.set_param(PARAM_PREFIX + "pack.write", "False")
        res = self.server().handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        names = {t["name"] for t in res["result"]["tools"]}
        self.assertNotIn("data_create", names)
        self.assertIn("data_search", names)
        out = self.call("data_create", ok=False, model="res.partner", values={"name": "x"})
        self.assertIn("switched off", out)
        log = self.env["nxb.mcp.log"].search([("tool", "=", "data_create")], limit=1)
        self.assertEqual(log.status, "refused")

    def test_sub_pack_follows_parent(self):
        icp = self.env["ir.config_parameter"].sudo()
        icp.set_param(PARAM_PREFIX + "pack.designer", "False")
        icp.set_param(PARAM_PREFIX + "pack.designer_pro", "True")
        names = {t.name for t in self.server().visible_tools()}
        self.assertNotIn("site_section_html", names)

    def test_readonly_key_hides_writes(self):
        srv = self.server(readonly=True)
        names = {t.name for t in srv.visible_tools()}
        self.assertNotIn("data_update", names)
        self.assertIn("data_search", names)
        out = self.call("data_update", ok=False, server=srv, model="res.partner", ids=[1], values={"name": "x"})
        self.assertIn("read-only", out)

    def test_strict_readonly_setting(self):
        self.env["ir.config_parameter"].sudo().set_param(PARAM_PREFIX + "strict_readonly", "True")
        names = {t.name for t in self.server().visible_tools()}
        self.assertFalse([n for n in names if TOOLS[n].writes])

    def test_delete_needs_confirm_and_setting(self):
        p = self.env["res.partner"].create({"name": "Delete me"})
        out = self.call("data_delete", ok=False, model="res.partner", ids=[p.id], confirm=False)
        self.assertIn("confirm=true", out)
        self.assertTrue(p.exists())
        self.env["ir.config_parameter"].sudo().set_param(PARAM_PREFIX + "allow_delete", "False")
        self.assertNotIn("data_delete", {t.name for t in self.server().visible_tools()})
        self.env["ir.config_parameter"].sudo().set_param(PARAM_PREFIX + "allow_delete", "True")
        res = self.call("data_delete", model="res.partner", ids=[p.id], confirm=True)
        self.assertEqual(res["deleted"], [p.id])

    def test_admin_only_tools(self):
        srv = self.server(user=self.user)
        self.assertNotIn("sql_query", {t.name for t in srv.visible_tools()})
        self.call("sql_query", ok=False, server=srv, query="select 1")

    def test_rate_limit(self):
        self.env["ir.config_parameter"].sudo().set_param(PARAM_PREFIX + "rate_limit", "2")
        self.call("data_count", model="res.partner")
        self.call("data_count", model="res.partner")
        out = self.call("data_count", ok=False, model="res.partner")
        self.assertIn("Rate limit", out)

    def test_unknown_tool_and_bad_database(self):
        self.assertIn("Unknown tool", self.call("nope", ok=False))
        out = self.call("data_count", ok=False, model="res.partner", database="does-not-exist")
        self.assertIn("not available", out)

    def test_log_written_and_redacted(self):
        self.call("data_count", model="res.partner", domain=[["name", "!=", "password"]])
        self.server().call_tool("data_count", {"model": "res.partner", "api_key": "secret"})
        logs = self.env["nxb.mcp.log"].search([("tool", "=", "data_count")])
        self.assertTrue(logs)
        self.assertTrue(any("***" in (l.arguments or "") for l in logs))
        self.assertNotIn("secret", " ".join(logs.mapped("arguments")))

    def test_prompts(self):
        srv = self.server()
        res = srv.handle({"jsonrpc": "2.0", "id": 1, "method": "prompts/list"})
        self.assertTrue(res["result"]["prompts"])
        res = srv.handle({"jsonrpc": "2.0", "id": 2, "method": "prompts/get",
                          "params": {"name": "build_dashboard", "arguments": {"topic": "sales"}}})
        self.assertIn("sales", res["result"]["messages"][0]["content"]["text"])

    def test_key_authentication(self):
        Key = self.env["nxb.mcp.key"]
        self.assertEqual(Key._authenticate(self.token), self.key)
        self.assertFalse(Key._authenticate(self.token + "x"))
        self.assertFalse(Key._authenticate("nxbm_999999_abc"))
        self.assertFalse(Key._authenticate("garbage"))
        self.key.active = False
        self.assertFalse(Key._authenticate(self.token))

    def test_pack_board(self):
        board = self.env["nxb.mcp.pack.board"].get_board()
        self.assertEqual(sum(len(g["cards"]) for g in board["groups"]), len(PACKS))
        board = self.env["nxb.mcp.pack.board"].set_pack("sql", False)
        card = [c for g in board["groups"] for c in g["cards"] if c["key"] == "sql"][0]
        self.assertFalse(card["on"])
        self.assertGreater(board["tokens_saved"], 0)
        overview = self.env["nxb.mcp.overview"].get_overview()
        self.assertIn("/mcp", overview["endpoint"])
