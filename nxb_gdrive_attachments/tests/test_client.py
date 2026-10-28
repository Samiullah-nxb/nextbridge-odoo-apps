# -*- coding: utf-8 -*-
# Copyright 2026 Nextbridge. License OPL-1.
import os
from unittest.mock import patch

from odoo.tests import tagged

from ..models import drive_client
from ..models.drive_client import DriveClient, DriveError
from .common import GDriveCase, service_key_b64


@tagged("post_install", "-at_install", "nxb_gdrive")
class TestDriveClient(GDriveCase):

    def client(self):
        return self.company._gdrive_client()

    def test_token_and_about(self):
        c = self.client()
        about = c.about()
        self.assertEqual(about["user"]["emailAddress"], "drive.user@example.com")
        token = c.token()
        self.assertEqual(c.token(), token, "the access token is cached in memory")
        # expired / revoked access token: refreshed once automatically
        self.fake.access_tokens.discard(token)
        self.assertTrue(c.about())
        self.assertNotEqual(c.token(), token)

    def test_bad_refresh_token(self):
        self.company.sudo().gdrive_refresh_token = "rt-revoked"
        with self.assertRaises(DriveError) as cm:
            self.company._gdrive_client().about()
        self.assertFalse(cm.exception.retry)
        self.assertIn("revoked", str(cm.exception))

    def test_not_connected(self):
        self.company.sudo().gdrive_refresh_token = False
        with self.assertRaises(DriveError) as cm:
            self.company._gdrive_client().about()
        self.assertIn("not connected", str(cm.exception))

    def test_folders_upload_download(self):
        c = self.client()
        folder = c.create_folder("Invoices", "root")
        self.assertEqual(c.find_folder("Invoices", "root")["id"], folder["id"])
        self.assertIsNone(c.find_folder("Nope", "root"))
        quote = c.create_folder("Bob's files", "root")
        self.assertEqual(c.find_folder("Bob's files", "root")["id"], quote["id"])
        meta = c.upload("a.pdf", b"%PDF-1.4 data", "application/pdf", parent=folder["id"])
        self.assertIn("drive.google.com", meta["webViewLink"])
        self.assertEqual(c.download(meta["id"]), b"%PDF-1.4 data")
        c.upload("a2.pdf", b"new content", "application/pdf", file_id=meta["id"])
        self.assertEqual(c.download(meta["id"]), b"new content")
        self.assertEqual(self.fake.files[meta["id"]]["name"], "a2.pdf")
        c.rename(meta["id"], "renamed.pdf")
        self.assertEqual(c.get(meta["id"])["name"], "renamed.pdf")
        c.share_anyone(meta["id"])
        self.assertEqual(self.fake.files[meta["id"]]["permissions"][0]["type"], "anyone")
        c.trash(meta["id"])
        self.assertTrue(self.fake.files[meta["id"]]["trashed"])
        with self.assertRaises(DriveError) as cm:
            c.download(meta["id"])
        self.assertEqual(cm.exception.status, 404)
        c.delete(meta["id"])
        self.assertNotIn(meta["id"], self.fake.files)

    def test_resumable_upload(self):
        c = self.client()
        data = os.urandom(5000)
        with patch.object(drive_client, "SIMPLE_LIMIT", 1000), patch.object(drive_client, "CHUNK", 1024):
            meta = c.upload("big.bin", data, "application/octet-stream", parent="root")
            self.assertEqual(c.download(meta["id"]), data)
            c.upload("big.bin", data[::-1], "application/octet-stream", file_id=meta["id"])
        self.assertEqual(self.fake.files[meta["id"]]["content"], data[::-1])
        puts = [r for r in self.fake.requests if r[0] == "PUT"]
        self.assertGreaterEqual(len(puts), 10, "uploaded in several chunks")

    def test_retries_on_server_errors(self):
        c = self.client()
        self.fake.fail_next = [("GET", "/about", 503), ("GET", "/about", 429)]
        with patch("time.sleep"):
            self.assertTrue(c.about())
        self.fake.fail_next = [("GET", "/about", 503)] * 3
        with patch("time.sleep"), self.assertRaises(DriveError) as cm:
            c.about()
        self.assertTrue(cm.exception.retry)
        self.assertEqual(cm.exception.status, 503)

    def test_service_account(self):
        self.company.sudo().write({"gdrive_auth": "service", "gdrive_service_key": service_key_b64(),
                                   "gdrive_service_subject": "boss@example.com"})
        c = self.company._gdrive_client()
        self.assertTrue(c.about())
        self.assertTrue(c.token().startswith("at-sa-"))
        claims = self.fake.last_jwt_claims
        self.assertEqual(claims["iss"], "odoo-sync@project.iam.gserviceaccount.com")
        self.assertEqual(claims["sub"], "boss@example.com")
        self.assertIn("auth/drive", claims["scope"])
        from odoo.exceptions import UserError
        with self.assertRaises(UserError):
            self.company.sudo().gdrive_service_key = b"bm90IGpzb24="  # "not json"

    def test_authorization_url(self):
        url = drive_client.authorization_url("cid", "https://odoo.example/cb", "st", "app")
        self.assertIn("access_type=offline", url)
        self.assertIn("prompt=consent", url)
        self.assertIn("drive.file", url)
        self.assertIn("redirect_uri=https%3A%2F%2Fodoo.example%2Fcb", url)

    def test_token_cache_per_credentials(self):
        key1 = self.company._gdrive_cache_key()
        self.company.sudo().gdrive_refresh_token = "rt-other"
        self.assertNotEqual(self.company._gdrive_cache_key(), key1)
        self.assertIsInstance(self.company._gdrive_client(), DriveClient)
