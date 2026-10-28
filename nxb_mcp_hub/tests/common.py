# -*- coding: utf-8 -*-
# Copyright 2026 Nextbridge. License OPL-1.
import json

from odoo.tests import TransactionCase

from ..core.registry import PACKS, PARAM_PREFIX
from ..core.server import McpServer, Principal


class McpCase(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["nxb.mcp.connection"]._ensure_this()
        icp = cls.env["ir.config_parameter"].sudo()
        for key in PACKS:
            icp.set_param(PARAM_PREFIX + "pack." + key, "True")
        icp.set_param(PARAM_PREFIX + "rate_limit", "0")
        icp.set_param(PARAM_PREFIX + "strict_readonly", "False")
        icp.set_param(PARAM_PREFIX + "allow_delete", "True")
        # isolate from whatever the database contains: only "this database", no private fetches
        cls.env["nxb.mcp.connection"].search([("kind", "!=", "this")]).write({"active": False})
        icp.set_param(PARAM_PREFIX + "allow_private_fetch", "False")
        icp.set_param(PARAM_PREFIX + "max_records", "500")
        cls.admin = cls.env.ref("base.user_admin")
        cls.key, cls.token = cls.env["nxb.mcp.key"]._generate(
            {"name": "test", "user_id": cls.admin.id, "scope": "write"})
        cls.user = cls.env["res.users"].create({
            "name": "MCP Test User", "login": "mcp_test_user", "email": "mcp_test_user@example.com",
            "group_ids": [(6, 0, [cls.env.ref("base.group_user").id])]})

    def server(self, user=None, readonly=False, key=None):
        principal = Principal(user or self.admin, readonly=readonly, key=key or self.key)
        return McpServer(self.env, principal)

    def call(self, tool_name, /, ok=True, server=None, **arguments):
        res = (server or self.server()).call_tool(tool_name, arguments)
        text = res["content"][0]["text"]
        if ok:
            self.assertFalse(res["isError"], "%s failed: %s" % (tool_name, text))
        else:
            self.assertTrue(res["isError"], "%s should have failed but returned: %s" % (tool_name, text[:300]))
        try:
            return json.loads(text)
        except ValueError:
            return text

    def installed(self, module):
        return bool(self.env["ir.module.module"].search_count([("name", "=", module), ("state", "=", "installed")]))
