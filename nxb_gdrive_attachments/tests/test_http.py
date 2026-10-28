# -*- coding: utf-8 -*-
# Copyright 2026 Nextbridge. License OPL-1.
from urllib.parse import parse_qs, urlparse

from odoo.tests import HttpCase, new_test_user, tagged

from .common import GDriveSetup


@tagged("post_install", "-at_install", "nxb_gdrive")
class TestHttp(GDriveSetup, HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_gdrive()
        cls.partner = cls.env["res.partner"].create({"name": "Web Customer"})

    def test_oauth_connect_flow(self):
        self.company.sudo().write({"gdrive_refresh_token": False, "gdrive_account": False})
        self.authenticate("admin", "admin")
        r = self.url_open("/nxb_gdrive/oauth/start?company_id=%d" % self.company.id, allow_redirects=False)
        self.assertIn(r.status_code, (302, 303))
        loc = urlparse(r.headers["Location"])
        q = parse_qs(loc.query)
        self.assertTrue(r.headers["Location"].startswith(self.fake.url + "/o/oauth2/v2/auth"))
        self.assertEqual(q["client_id"], ["client-1"])
        self.assertEqual(q["access_type"], ["offline"])
        self.assertTrue(q["redirect_uri"][0].endswith("/nxb_gdrive/oauth/callback"))
        state = q["state"][0]
        # tampered state is refused
        bad = self.url_open("/nxb_gdrive/oauth/callback?code=x&state=" + state.replace('"c"', '"x"'),
                            allow_redirects=False)
        self.assertEqual(bad.status_code, 403)
        r = self.url_open("/nxb_gdrive/oauth/start?company_id=%d" % self.company.id, allow_redirects=False)
        state = parse_qs(urlparse(r.headers["Location"]).query)["state"][0]
        code = self.fake.issue_code()
        r = self.url_open("/nxb_gdrive/oauth/callback?code=%s&state=%s" % (code, state), allow_redirects=False)
        self.assertIn(r.status_code, (302, 303), r.text[:300])
        self.assertIn("action_gdrive_settings", r.headers["Location"])
        company = self.company.sudo()
        company.invalidate_recordset()
        self.assertTrue(company.gdrive_refresh_token.startswith("rt-"))
        self.assertEqual(company.gdrive_account, "drive.user@example.com")
        # a code cannot be used twice
        r = self.url_open("/nxb_gdrive/oauth/start?company_id=%d" % self.company.id, allow_redirects=False)
        state = parse_qs(urlparse(r.headers["Location"]).query)["state"][0]
        r = self.url_open("/nxb_gdrive/oauth/callback?code=%s&state=%s" % (code, state), allow_redirects=False)
        self.assertEqual(r.status_code, 400)
        self.assertIn("could not be connected", r.text)
        # Google error (user clicked "Cancel")
        r = self.url_open("/nxb_gdrive/oauth/callback?error=access_denied&state=x", allow_redirects=False)
        self.assertEqual(r.status_code, 400)

    def test_oauth_needs_admin(self):
        new_test_user(self.env, "gd_user", groups="base.group_user")
        self.authenticate("gd_user", "gd_user")
        r = self.url_open("/nxb_gdrive/oauth/start", allow_redirects=False)
        self.assertEqual(r.status_code, 403)

    def test_download_moved_file(self):
        self.company.sudo().gdrive_storage = "move"
        att = self.env["ir.attachment"].create({"name": "report.pdf", "raw": b"%PDF-1.4 moved report",
                                                "mimetype": "application/pdf", "res_model": "res.partner",
                                                "res_id": self.partner.id})
        self.env["ir.attachment"]._gdrive_process([att.id], commit=False)
        att.invalidate_recordset()
        self.assertTrue(att.gdrive_moved)
        self.clear_cache()
        self.authenticate("admin", "admin")
        r = self.url_open("/web/content/%d?download=true" % att.id)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.content, b"%PDF-1.4 moved report")
        self.assertIn("report.pdf", r.headers.get("Content-Disposition", ""))
        r = self.url_open("/web/content/%d" % att.id)
        self.assertEqual(r.content, b"%PDF-1.4 moved report")
