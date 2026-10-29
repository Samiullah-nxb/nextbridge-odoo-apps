# -*- coding: utf-8 -*-
# Copyright 2026 Nextbridge. License OPL-1.
import json
import re
import time
import xmlrpc.client
from unittest.mock import patch

from odoo.exceptions import ValidationError
from odoo.tests import HttpCase, TransactionCase, new_test_user, tagged

P = "nxb_shared_user_login."


class SharedSetup:

    @classmethod
    def _setup_shared(cls):
        icp = cls.env["ir.config_parameter"].sudo()
        icp.set_param(P + "enabled", "True")
        icp.set_param(P + "idle_minutes", "0")
        icp.set_param(P + "max_attempts", "5")
        icp.set_param(P + "lock_minutes", "15")
        icp.set_param(P + "allow_api", "False")
        cls.shared = new_test_user(cls.env, "counter", password="counter-pass-1",
                                   groups="base.group_user,base.group_partner_manager", name="Front Counter",
                                   email="counter@example.com")
        Emp = cls.env["hr.employee"]
        cls.alice = Emp.create({"name": "Alice Agent", "pin": "1234", "work_email": "alice@example.com"})
        cls.bob = Emp.create({"name": "Bob Builder", "pin": "5678"})
        cls.carol = Emp.create({"name": "Carol Nopin"})
        cls.outsider = Emp.create({"name": "Oscar Outsider", "pin": "9999"})
        cls.shared.write({"nxb_shared": True, "nxb_employee_ids": [(6, 0, (cls.alice | cls.bob | cls.carol).ids)]})
        cls.customer = cls.env["res.partner"].create({"name": "Customer One", "email": "customer@example.com"})


@tagged("post_install", "-at_install", "nxb_shared")
class TestSharedModel(SharedSetup, TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_shared()

    def test_constraints(self):
        portal = new_test_user(self.env, "portal_x", groups="base.group_portal")
        with self.assertRaises(ValidationError):
            portal.nxb_shared = True
        admin_like = new_test_user(self.env, "admin_x", groups="base.group_system")
        with self.assertRaises(ValidationError):
            admin_like.nxb_shared = True

    def test_pin_check_and_lockout(self):
        self.env["ir.config_parameter"].sudo().set_param(P + "max_attempts", "3")
        self.env.flush_all()
        ok, msg = self.alice._nxb_check_pin("0000")
        self.assertFalse(ok)
        self.assertIn("2 attempts left", msg)
        self.alice._nxb_check_pin("0000")
        ok, msg = self.alice._nxb_check_pin("0000")
        self.assertIn("locked", msg)
        ok, msg = self.alice._nxb_check_pin("1234")
        self.assertFalse(ok, "right PIN refused while locked")
        self.assertIn("Try again", msg)
        self.alice.action_nxb_unlock_pin()
        self.assertEqual(self.alice._nxb_check_pin("1234"), (True, ""))
        ok, msg = self.carol._nxb_check_pin("1234")
        self.assertIn("no PIN", msg)

    def test_no_attribution_without_request(self):
        msg = self.customer.with_user(self.shared).message_post(body="from a job", message_type="comment")
        self.assertEqual(msg.author_id, self.shared.partner_id)

    def test_feature_switch(self):
        self.assertTrue(self.shared._nxb_is_shared())
        self.env["ir.config_parameter"].sudo().set_param(P + "enabled", "False")
        self.env.flush_all()
        self.assertFalse(self.shared._nxb_is_shared())
        self.assertFalse(self.shared.nxb_feature_on)


@tagged("post_install", "-at_install", "nxb_shared")
class TestSharedHttp(SharedSetup, HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_shared()

    # ------------------------------------------------------------------ helpers
    def csrf(self, path="/shared_login"):
        page = self.url_open(path)
        return re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1), page

    def sign_in(self, employee, pin, redirect=""):
        token, _page = self.csrf()
        return self.url_open("/shared_login", data={"csrf_token": token, "employee_id": employee.id, "pin": pin,
                                                    "redirect": redirect}, allow_redirects=False)

    def rpc(self, model, method, args, kwargs=None):
        return self.url_open("/web/dataset/call_kw", data=json.dumps({
            "jsonrpc": "2.0", "method": "call", "params": {"model": model, "method": method, "args": args,
                                                           "kwargs": kwargs or {}}}),
            headers={"Content-Type": "application/json"}).json()

    def logs(self, event):
        return self.env["nxb.shared.login.log"].search([("user_id", "=", self.shared.id), ("event", "=", event)])

    # ------------------------------------------------------------------ gate
    def test_gate_before_pin(self):
        self.authenticate("counter", "counter-pass-1")
        r = self.url_open("/odoo", allow_redirects=False)
        self.assertEqual(r.status_code, 303)
        self.assertTrue(r.headers["Location"].endswith("/shared_login"))
        r = self.url_open("/odoo/contacts?debug=1", allow_redirects=False)
        self.assertIn("/shared_login?redirect=%2Fodoo%2Fcontacts", r.headers["Location"])
        res = self.rpc("res.partner", "search_read", [[["id", "=", self.customer.id]]], {"fields": ["name"]})
        self.assertIn("error", res)
        self.assertIn("SessionExpired", res["error"]["data"]["name"])
        r = self.url_open("/web/image/res.partner/%d/avatar_128" % self.customer.id, allow_redirects=False)
        self.assertEqual(r.status_code, 303, "binary routes are gated too")
        page = self.url_open("/shared_login")
        self.assertEqual(page.status_code, 200)
        for emp in (self.alice, self.bob, self.carol):
            self.assertIn(emp.name, page.text)
        self.assertNotIn(self.outsider.name, page.text)

    def test_sign_in_and_attribution(self):
        self.authenticate("counter", "counter-pass-1")
        r = self.sign_in(self.alice, "1234")
        self.assertEqual(r.status_code, 303)
        self.assertTrue(r.headers["Location"].endswith("/odoo"))
        self.assertEqual(self.url_open("/odoo").status_code, 200)
        info = self.url_open("/web/session/get_session_info", data=json.dumps({"jsonrpc": "2.0", "params": {}}),
                             headers={"Content-Type": "application/json"}).json()["result"]
        self.assertEqual(info["nxb_shared_employee"]["name"], "Alice Agent")
        self.assertEqual(len(self.logs("sign_in")), 1)
        # chatter message
        res = self.rpc("res.partner", "message_post", [[self.customer.id]],
                       {"body": "Called the customer", "message_type": "comment", "subtype_xmlid": "mail.mt_note"})
        msg = self.env["mail.message"].browse(res["result"])
        self.assertEqual(msg.author_id, self.alice.work_contact_id)
        self.assertIn("Alice Agent", msg.email_from)
        self.assertIn("alice@example.com", msg.email_from)
        # field tracking on any model
        self.rpc("res.partner", "write", [[self.customer.id], {"phone": "+92 300 0000000"}])
        self.env.flush_all()
        track = self.customer.message_ids.filtered(lambda m: m.tracking_value_ids)[:1]
        self.assertEqual(track.author_id, self.alice.work_contact_id)
        # e-mail to a customer
        res = self.rpc("res.partner", "message_post", [[self.customer.id]],
                       {"body": "Your order is ready", "message_type": "comment", "subtype_xmlid": "mail.mt_comment",
                        "partner_ids": [self.customer.id]})
        mail = self.env["mail.mail"].sudo().search([("mail_message_id", "=", res["result"])])
        self.assertTrue(mail)
        self.assertIn("Alice Agent", mail.email_from)
        # Discuss
        channel = self.env["discuss.channel"].create({"name": "Team", "channel_type": "channel"})
        channel.add_members(self.shared.partner_id.ids)
        res = self.rpc("discuss.channel", "message_post", [[channel.id]],
                       {"body": "Hello team", "message_type": "comment", "subtype_xmlid": "mail.mt_comment"})
        self.assertEqual(self.env["mail.message"].browse(res["result"]).author_id, self.alice.work_contact_id)
        # replies still go to the shared account, not to the employee's contact
        self.assertIn(self.shared.partner_id, self.customer.message_partner_ids)
        self.assertNotIn(self.alice.work_contact_id, self.customer.message_partner_ids)

    def test_switch_employee(self):
        self.authenticate("counter", "counter-pass-1")
        self.sign_in(self.alice, "1234")
        r = self.url_open("/shared_login/switch", allow_redirects=False)
        self.assertTrue(r.headers["Location"].endswith("/shared_login"))
        self.assertEqual(self.url_open("/odoo", allow_redirects=False).status_code, 303)
        self.sign_in(self.bob, "5678")
        res = self.rpc("res.partner", "message_post", [[self.customer.id]], {"body": "Bob here",
                                                                             "message_type": "comment"})
        self.assertEqual(self.env["mail.message"].browse(res["result"]).author_id, self.bob.work_contact_id)
        self.assertEqual(len(self.logs("sign_in")), 1)
        self.assertEqual(self.logs("switch").employee_id, self.bob)
        page = self.url_open("/shared_login/switch")
        self.assertTrue(page.text.lstrip().startswith("<!DOCTYPE html>"))

    def test_wrong_pin_lock_and_choices(self):
        self.env["ir.config_parameter"].sudo().set_param(P + "max_attempts", "2")
        self.env.flush_all()
        self.authenticate("counter", "counter-pass-1")
        r = self.sign_in(self.alice, "0000")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Wrong PIN. 1 attempts left", r.text)
        r = self.sign_in(self.alice, "0000")
        self.assertIn("locked", r.text)
        r = self.sign_in(self.alice, "1234")
        self.assertIn("Try again", r.text)
        self.assertEqual(self.url_open("/odoo", allow_redirects=False).status_code, 303)
        self.assertEqual(len(self.logs("wrong_pin")), 1)
        self.assertGreaterEqual(len(self.logs("locked")), 1)
        r = self.sign_in(self.outsider, "9999")
        self.assertIn("Choose your name", r.text)
        token, _p = self.csrf()
        r = self.url_open("/shared_login", data={"csrf_token": token, "employee_id": self.carol.id, "pin": "1"})
        self.assertIn("no PIN yet", r.text)
        r = self.url_open("/shared_login", data={"employee_id": self.bob.id, "pin": "5678"}, allow_redirects=False)
        self.assertNotEqual(r.status_code, 303, "CSRF token required")

    def test_safe_redirect(self):
        self.authenticate("counter", "counter-pass-1")
        r = self.sign_in(self.alice, "1234", redirect="/odoo/contacts")
        self.assertTrue(r.headers["Location"].endswith("/odoo/contacts"))
        self.url_open("/shared_login/switch")
        r = self.sign_in(self.alice, "1234", redirect="//evil.example.com/x")
        self.assertTrue(r.headers["Location"].endswith("/odoo"))
        self.assertNotIn("evil", r.headers["Location"])

    def test_idle_lock(self):
        self.env["ir.config_parameter"].sudo().set_param(P + "idle_minutes", "5")
        self.env.flush_all()
        self.authenticate("counter", "counter-pass-1")
        self.sign_in(self.alice, "1234")
        self.assertEqual(self.url_open("/odoo", allow_redirects=False).status_code, 200)
        later = time.time() + 6 * 60
        with patch("odoo.addons.nxb_shared_user_login.models.ir_http.time.time", return_value=later):
            r = self.url_open("/odoo", allow_redirects=False)
        self.assertEqual(r.status_code, 303)
        page = self.url_open(r.headers["Location"])
        self.assertIn("Who is working", page.text)
        self.assertEqual(len(self.logs("timeout")), 1)
        self.assertEqual(self.logs("timeout").employee_id, self.alice)
        self.sign_in(self.alice, "1234")
        self.assertEqual(self.url_open("/odoo", allow_redirects=False).status_code, 200)

    def test_change_pin(self):
        self.authenticate("counter", "counter-pass-1")
        self.sign_in(self.bob, "5678")
        token, page = self.csrf("/shared_login/pin")
        self.assertIn("Bob Builder", page.text)
        post = lambda **kw: self.url_open("/shared_login/pin", data=dict(kw, csrf_token=token))
        self.assertIn("not correct", post(current="1111", new="2468", confirm="2468").text)
        self.assertIn("4 to 12 digits", post(current="5678", new="12", confirm="12").text)
        self.assertIn("different", post(current="5678", new="2468", confirm="2469").text)
        self.assertIn("has been changed", post(current="5678", new="2468", confirm="2468").text)
        self.assertEqual(self.bob.sudo().pin, "2468")
        self.assertEqual(len(self.logs("pin_changed")), 1)

    def test_avatar_route(self):
        self.authenticate("counter", "counter-pass-1")
        self.assertEqual(self.url_open("/shared_login/avatar/%d" % self.alice.id).status_code, 200)
        self.assertEqual(self.url_open("/shared_login/avatar/%d" % self.outsider.id).status_code, 404)

    def test_other_users_untouched(self):
        self.authenticate("admin", "admin")
        self.assertEqual(self.url_open("/odoo", allow_redirects=False).status_code, 200)
        self.assertEqual(self.url_open("/shared_login", allow_redirects=False).status_code, 303)
        self.env["ir.config_parameter"].sudo().set_param(P + "enabled", "False")
        self.env.flush_all()
        self.authenticate("counter", "counter-pass-1")
        self.assertEqual(self.url_open("/odoo", allow_redirects=False).status_code, 200)

    def xmlrpc(self, service, method, *args):
        body = xmlrpc.client.dumps(args, method, allow_none=True)
        r = self.url_open("/xmlrpc/2/" + service, data=body.encode(), headers={"Content-Type": "text/xml"})
        return xmlrpc.client.loads(r.content)[0][0]

    def test_external_api_blocked(self):
        db = self.env.cr.dbname
        uid = self.xmlrpc("common", "authenticate", db, "counter", "counter-pass-1", {})
        self.assertTrue(uid)
        with self.assertRaises(xmlrpc.client.Fault) as cm:
            self.xmlrpc("object", "execute_kw", db, uid, "counter-pass-1", "res.partner", "search_count", [[]])
        self.assertIn("shared account", cm.exception.faultString)
        # other users are not affected
        admin_uid = self.xmlrpc("common", "authenticate", db, "admin", "admin", {})
        self.assertGreater(self.xmlrpc("object", "execute_kw", db, admin_uid, "admin", "res.partner",
                                       "search_count", [[]]), 0)
        self.env["ir.config_parameter"].sudo().set_param(P + "allow_api", "True")
        self.env.flush_all()
        self.assertGreater(self.xmlrpc("object", "execute_kw", db, uid, "counter-pass-1", "res.partner",
                                       "search_count", [[]]), 0)
