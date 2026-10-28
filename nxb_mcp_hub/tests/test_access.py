# -*- coding: utf-8 -*-
# Copyright 2026 Nextbridge. License OPL-1.
from lxml import etree

from odoo.exceptions import AccessError
from odoo.tests import tagged

from .common import McpCase


@tagged("post_install", "-at_install", "nxb_mcp")
class TestAccess(McpCase):

    def setUp(self):
        super().setUp()
        self.env.registry.clear_cache()

    def _visible_menus(self, user):
        return self.env["ir.ui.menu"].with_user(user)._visible_menu_ids()

    def test_users_and_groups(self):
        users = self.call("access_users_find", text="mcp_test")
        self.assertEqual(users[0]["login"], "mcp_test_user")
        groups = self.call("access_groups_list", text="Administrator")
        self.assertTrue(groups)
        created = self.call("access_user_create", name="New Person", login="new.person@example.com",
                            groups=["base.group_partner_manager"])["created"]
        self.assertTrue(self.env["res.users"].browse(created["id"]).has_group("base.group_partner_manager"))
        self.assertIn("already exists", self.call("access_user_create", ok=False, name="x",
                                                  login="new.person@example.com"))
        self.assertIn("Administrator", self.call("access_user_create", ok=False, name="y", login="y@example.com",
                                                 groups=["base.group_system"]))
        self.call("access_user_groups", user="new.person@example.com", remove=["base.group_partner_manager"])
        self.assertFalse(self.env["res.users"].browse(created["id"]).has_group("base.group_partner_manager"))
        summary = self.call("access_user_summary", user="mcp_test_user", model="res.partner")
        self.assertTrue(summary["model_access"]["read"])

    def test_menu_rules(self):
        settings_menu = self.env.ref("base.menu_administration")
        found = self.call("access_menus_find", text=settings_menu.name, top_level_only=True)
        self.assertTrue(found)
        before = self._visible_menus(self.user)
        apps_menu = self.env["ir.ui.menu"].browse(list(before)).filtered(lambda m: not m.parent_id)[:1]
        if not apps_menu:
            self.skipTest("no visible top menu for the test user")
        rule = self.call("access_menus_hide", menus=[str(apps_menu.id)], users=["mcp_test_user"])["created"]
        self.env.registry.clear_cache()
        after = self._visible_menus(self.user)
        self.assertNotIn(apps_menu.id, after)
        self.assertIn(apps_menu.id, self._visible_menus(self.admin))  # admins never restricted
        self.call("access_rule_switch", rule_id=rule["rule_id"], active=False)
        self.env.registry.clear_cache()
        self.assertIn(apps_menu.id, self._visible_menus(self.user))
        self.call("access_rule_remove", rule_id=rule["rule_id"])
        self.assertIn("administrator", self.call("access_menus_hide", ok=False, menus=[str(apps_menu.id)],
                                                 users=[self.admin.login]))
        self.assertIn("Administrator group", self.call("access_menus_hide", ok=False, menus=[str(apps_menu.id)],
                                                       groups=["base.group_system"]))

    def test_menu_only(self):
        before = self._visible_menus(self.user)
        tops = self.env["ir.ui.menu"].browse(list(before)).filtered(lambda m: not m.parent_id)
        if len(tops) < 2:
            self.skipTest("needs two visible apps")
        keep = tops[0]
        self.call("access_menus_only", menus=[str(keep.id)], users=["mcp_test_user"])
        self.env.registry.clear_cache()
        after = self.env["ir.ui.menu"].browse(list(self._visible_menus(self.user)))
        self.assertEqual(after.filtered(lambda m: not m.parent_id), keep)

    def test_field_and_element_rules(self):
        self.call("access_fields_restrict", model="res.partner", fields=["email"], mode="hidden",
                  users=["mcp_test_user"])
        self.call("access_fields_restrict", model="res.partner", fields=["phone"], mode="readonly",
                  users=["mcp_test_user"])
        elements = self.call("access_view_elements", model="res.partner")
        self.assertIn("email", elements["fields"])
        self.call("access_chatter_hide", model="res.partner", users=["mcp_test_user"])
        if elements["tabs"]:
            self.call("access_elements_hide", model="res.partner", elements=[elements["tabs"][0]["selector"]],
                      users=["mcp_test_user"])
        self.env.registry.clear_cache()
        arch = etree.fromstring(self.env["res.partner"].with_user(self.user).get_view(view_type="form")["arch"])
        emails = arch.xpath("//field[@name='email']")
        self.assertTrue(emails and all(e.get("invisible") == "1" for e in emails))
        phones = arch.xpath("//field[@name='phone']")
        self.assertTrue(phones and all(p.get("readonly") == "1" for p in phones))
        self.assertFalse(arch.xpath("//chatter"))
        admin_arch = etree.fromstring(self.env["res.partner"].with_user(self.admin).get_view(view_type="form")["arch"])
        self.assertNotEqual(admin_arch.xpath("//field[@name='email']")[0].get("invisible"), "1")
        self.assertIn("Unsupported", self.call("access_elements_hide", ok=False, model="res.partner",
                                               elements=["button:first"], users=["mcp_test_user"]))

    def test_model_block(self):
        self.user.group_ids = [(4, self.env.ref("base.group_partner_manager").id)]
        self.env["res.partner"].with_user(self.user).create({"name": "allowed before"})
        self.call("access_model_block", model="res.partner", operations=["create", "unlink"],
                  users=["mcp_test_user"])
        self.env.registry.clear_cache()
        with self.assertRaises(AccessError):
            self.env["res.partner"].with_user(self.user).create({"name": "blocked"})
        self.env["res.partner"].with_user(self.user).search([], limit=1)  # reading still allowed
        self.assertIn("technical", self.call("access_model_block", ok=False, model="res.users",
                                             operations=["read"], users=["mcp_test_user"]))

    def test_record_rule(self):
        grp = self.env["res.groups"].create({"name": "MCP restricted readers"})
        self.user.group_ids = [(4, grp.id)]
        mine = self.env["res.partner"].create({"name": "Mine", "user_id": self.user.id})
        other = self.env["res.partner"].create({"name": "Other", "user_id": self.admin.id})
        res = self.call("access_record_rule_add", model="res.partner",
                        domain=[["user_id", "=", "user.id"]], groups=[grp.full_name], operations=["read"],
                        name="Own partners")["created"]
        self.assertIn("user.id", res["domain"])
        self.env.registry.clear_cache()
        visible = self.env["res.partner"].with_user(self.user).search([("id", "in", [mine.id, other.id])])
        self.assertEqual(visible, mine)
        listed = self.call("access_rules_list", model="res.partner")
        self.assertTrue(any(r["rule_id"] == res["rule_id"] for r in listed))
        self.call("access_rule_remove", rule_id=res["rule_id"])
        self.assertFalse(self.env["ir.rule"].search([("name", "=", "[AI] Own partners")]))
        self.assertIn("not valid", self.call("access_record_rule_add", ok=False, model="res.partner",
                                             domain=[["nope_field", "=", 1]], groups=[grp.full_name]))

    def test_cannot_restrict_self(self):
        out = self.call("access_chatter_hide", ok=False, model="res.partner", users=[self.admin.login])
        self.assertIn("administrator", out)
        out = self.call("access_chatter_hide", ok=False, model="res.partner")
        self.assertIn("users and/or groups", out)
        out = self.call("access_chatter_hide", ok=False, server=self.server(user=self.user), model="res.partner",
                        users=["mcp_test_user"])
        self.assertIn("administrator", out)
