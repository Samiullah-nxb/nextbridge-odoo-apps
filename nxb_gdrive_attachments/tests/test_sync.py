# -*- coding: utf-8 -*-
# Copyright 2026 Nextbridge. License OPL-1.
import base64
import os

from odoo.exceptions import UserError
from odoo.tests import tagged
from odoo.tools import mute_logger

from .. import _uninstall
from ..models.ir_attachment import MAX_ATTEMPTS, PREFIX
from .common import GDriveCase

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")


@tagged("post_install", "-at_install", "nxb_gdrive")
class TestSync(GDriveCase):

    # ------------------------------------------------------------------ upload & folders
    def test_upload_model_folders(self):
        att = self.attach(self.partner, "contract.pdf", b"%PDF contract")
        self.assertEqual(att.gdrive_state, "pending")
        self.sync(att)
        self.assertEqual(att.gdrive_state, "synced")
        f = self.drive_file(att)
        self.assertEqual(f["content"], b"%PDF contract")
        path = self.fake.path_of(f["id"])
        self.assertTrue(path.endswith("Odoo - %s/Contact/contract.pdf" % self.company.name), path)
        self.assertIn("drive.google.com", att.gdrive_url)
        self.assertTrue(att.gdrive_synced_at)
        self.assertFalse(att.gdrive_moved)
        self.assertEqual(f["appProperties"]["odoo_attachment"], str(att.id))
        log = self.env["nxb.gdrive.log"].search([("attachment_id", "=", att.id)])
        self.assertEqual(log.operation, "upload")
        self.assertEqual(log.status, "ok")
        # second file of the same type reuses the folder
        folders = len([x for x in self.fake.files.values() if x["mimeType"].endswith("folder")])
        att2 = self.attach(self.partner2, "b.txt")
        self.sync(att2)
        self.assertEqual(att2.gdrive_folder_id, att.gdrive_folder_id)
        self.assertEqual(folders, len([x for x in self.fake.files.values() if x["mimeType"].endswith("folder")]))

    def test_single_and_record_folders(self):
        self.company.sudo().gdrive_folder_mode = "single"
        a = self.attach(self.partner, "one.txt")
        self.sync(a)
        self.assertEqual(a.gdrive_folder_id, self.company.sudo().gdrive_root_folder_id)
        self.company.sudo().gdrive_folder_mode = "record"
        b = self.attach(self.partner, "two.txt")
        c = self.attach(self.partner, "three.txt")
        d = self.attach(self.partner2, "four.txt")
        self.sync(b | c | d)
        self.assertEqual(b.gdrive_folder_id, c.gdrive_folder_id)
        self.assertNotEqual(b.gdrive_folder_id, d.gdrive_folder_id)
        path = self.fake.path_of(b.gdrive_file_id)
        self.assertTrue(path.endswith("Contact/Acme Corp [%d]/two.txt" % self.partner.id), path)
        rec_folder = self.env["nxb.gdrive.folder"].search([("res_model", "=", "res.partner"),
                                                           ("res_id", "=", self.partner.id)])
        self.assertEqual(rec_folder.file_count, 2)

    def test_unattached_file_goes_to_other_folder(self):
        att = self.Att.create({"name": "loose.txt", "raw": b"loose"})
        self.sync(att)
        self.assertTrue(self.fake.path_of(att.gdrive_file_id).endswith("Other files/loose.txt"))

    def test_shared_drive_and_share_link(self):
        self.company.sudo().write({"gdrive_shared_drive_id": "shared1", "gdrive_share_link": True})
        att = self.attach(self.partner, "shared.txt")
        self.sync(att)
        f = self.drive_file(att)
        self.assertEqual(f["driveId"], "shared1")
        self.assertEqual(f["permissions"][0]["role"], "reader")
        uploads = [r for r in self.fake.requests if r[1].startswith("/upload/")]
        self.assertTrue(all(r[2].get("supportsAllDrives") == "true" for r in uploads))

    # ------------------------------------------------------------------ eligibility
    def test_eligibility_rules(self):
        c = self.company.sudo()
        img = self.Att.create({"name": "image_1920", "raw": PNG, "res_model": "res.partner",
                               "res_id": self.partner.id, "res_field": "image_1920"})
        self.assertFalse(img.gdrive_state, "field contents stay local by default")
        pub = self.attach(self.partner, "public.txt", public=True)
        self.assertFalse(pub.gdrive_state)
        view = self.Att.create({"name": "v", "raw": b"x", "res_model": "ir.ui.view", "res_id": 1})
        self.assertFalse(view.gdrive_state)
        url = self.Att.create({"name": "link", "type": "url", "url": "https://example.com"})
        self.assertFalse(url.gdrive_state)
        c.gdrive_min_size = 1
        small = self.attach(self.partner, "small.txt", b"x" * 100)
        big = self.attach(self.partner, "big.txt", b"x" * 2048)
        self.assertFalse(small.gdrive_state)
        self.assertEqual(big.gdrive_state, "pending")
        c.gdrive_min_size = 0
        c.write({"gdrive_model_scope": "selected",
                 "gdrive_model_ids": [(6, 0, self.env["ir.model"]._get("res.users").ids)]})
        self.assertFalse(self.attach(self.partner, "p.txt").gdrive_state)
        self.assertEqual(self.attach(self.env.user, "u.txt").gdrive_state, "pending")
        c.write({"gdrive_model_scope": "all", "gdrive_include_fields": True, "gdrive_include_public": True})
        img2 = self.Att.create({"name": "image_1920", "raw": PNG, "res_model": "res.partner",
                                "res_id": self.partner2.id, "res_field": "image_1920"})
        self.assertEqual(img2.gdrive_state, "pending")
        self.assertEqual(self.attach(self.partner, "public2.txt", public=True).gdrive_state, "pending")
        c.gdrive_enabled = False
        self.assertFalse(self.attach(self.partner, "off.txt").gdrive_state)

    def test_composer_upload_queued_when_linked(self):
        att = self.Att.create({"name": "draft.pdf", "raw": b"draft", "res_model": "mail.compose.message",
                               "res_id": 0})
        self.assertFalse(att.gdrive_state)
        att.write({"res_model": "res.partner", "res_id": self.partner.id})
        self.assertEqual(att.gdrive_state, "pending")

    def test_queue_existing(self):
        self.company.sudo().gdrive_enabled = False
        olds = self.attach(self.partner, "old1.txt") | self.attach(self.partner, "old2.txt")
        self.assertFalse(any(olds.mapped("gdrive_state")))
        self.company.sudo().gdrive_enabled = True
        self.company.action_gdrive_queue_existing()
        olds.invalidate_recordset()
        self.assertEqual(set(olds.mapped("gdrive_state")), {"pending"})
        self.sync()
        olds.invalidate_recordset()
        self.assertEqual(set(olds.mapped("gdrive_state")), {"synced"})

    # ------------------------------------------------------------------ changes
    def test_update_rename_relink(self):
        self.company.sudo().gdrive_folder_mode = "record"
        att = self.attach(self.partner, "v1.txt", b"version 1")
        self.sync(att)
        fid = att.gdrive_file_id
        att.write({"raw": b"version 2"})
        self.assertEqual(att.gdrive_state, "changed")
        self.sync(att)
        self.assertEqual(att.gdrive_file_id, fid, "same Drive file, new content")
        self.assertEqual(self.fake.files[fid]["content"], b"version 2")
        att.write({"name": "final.txt"})
        self.assertEqual(att.gdrive_state, "renamed")
        uploads_before = len([r for r in self.fake.requests if r[1].startswith("/upload/")])
        self.sync(att)
        self.assertEqual(self.fake.files[fid]["name"], "final.txt")
        self.assertEqual(uploads_before, len([r for r in self.fake.requests if r[1].startswith("/upload/")]),
                         "a rename does not upload the content again")
        att.write({"res_id": self.partner2.id})
        self.assertEqual(att.gdrive_state, "renamed")
        self.sync(att)
        self.assertTrue(self.fake.path_of(fid).endswith("Globex [%d]/final.txt" % self.partner2.id))
        self.assertEqual(att.gdrive_state, "synced")

    def test_file_deleted_on_drive_is_uploaded_again(self):
        att = self.attach(self.partner, "gone.txt", b"one")
        self.sync(att)
        old = att.gdrive_file_id
        del self.fake.files[old]
        att.write({"raw": b"two"})
        self.sync(att)
        self.assertNotEqual(att.gdrive_file_id, old)
        self.assertEqual(self.drive_file(att)["content"], b"two")

    def test_copy_is_a_new_file(self):
        att = self.attach(self.partner, "orig.txt")
        self.sync(att)
        dup = att.copy()
        self.assertFalse(dup.gdrive_file_id)
        self.assertEqual(dup.gdrive_state, "pending")

    # ------------------------------------------------------------------ deletion
    def test_delete_modes(self):
        Trash = self.env["nxb.gdrive.trash"]
        a = self.attach(self.partner, "trash.txt")
        b = self.attach(self.partner, "delete.txt")
        c = self.attach(self.partner, "keep.txt")
        self.sync(a | b | c)
        fa, fb, fc = a.gdrive_file_id, b.gdrive_file_id, c.gdrive_file_id
        a.unlink()
        self.company.sudo().gdrive_delete = "delete"
        b.unlink()
        self.company.sudo().gdrive_delete = "keep"
        c.unlink()
        self.assertEqual(Trash.search_count([]), 2)
        self.sync()
        self.assertTrue(self.fake.files[fa]["trashed"])
        self.assertNotIn(fb, self.fake.files)
        self.assertFalse(self.fake.files[fc]["trashed"])
        self.assertEqual(Trash.search_count([]), 0)

    def test_delete_of_missing_file_and_record(self):
        att = self.attach(self.partner2, "x.txt")
        self.sync(att)
        del self.fake.files[att.gdrive_file_id]
        self.partner2.unlink()  # removes its attachments
        self.assertFalse(att.exists())
        self.sync()
        self.assertEqual(self.env["nxb.gdrive.trash"].search_count([]), 0)

    def test_failed_delete_retries(self):
        att = self.attach(self.partner, "x.txt")
        self.sync(att)
        att.unlink()
        self.fake.fail_next = [("PATCH", "/files/", 500)] * 3
        with mute_logger("odoo.addons.nxb_gdrive_attachments.models.drive_client"):
            from unittest.mock import patch
            with patch("time.sleep"):
                self.sync()
        item = self.env["nxb.gdrive.trash"].search([])
        self.assertEqual(item.attempts, 1)
        self.assertTrue(item.next_try)
        item.action_retry()
        self.sync()
        self.assertFalse(item.exists())

    # ------------------------------------------------------------------ move mode
    def test_move_mode(self):
        self.company.sudo().gdrive_storage = "move"
        att = self.attach(self.partner, "moved.pdf", b"%PDF moved content")
        local = att.store_fname
        self.sync(att)
        self.assertTrue(att.store_fname.startswith(PREFIX))
        self.assertTrue(att.gdrive_moved)
        self.assertIn(att.gdrive_file_id, att.store_fname)
        checklist = os.path.join(self.Att._full_path("checklist"), local)
        self.assertTrue(os.path.exists(checklist), "the local file is handed to Odoo's garbage collector")
        self.clear_cache()
        gets_before = len([r for r in self.fake.requests if r[2].get("alt") == "media"])
        self.assertEqual(att.raw, b"%PDF moved content")
        att.invalidate_recordset()
        self.assertEqual(base64.b64decode(att.datas), b"%PDF moved content")
        gets_after = len([r for r in self.fake.requests if r[2].get("alt") == "media"])
        self.assertEqual(gets_after - gets_before, 1, "second read comes from the local cache")
        self.assertEqual(self.Att.search_count([("gdrive_moved", "=", True), ("id", "=", att.id)]), 1)
        # Odoo's own garbage collector keeps working and never touches Drive references
        self.Att._gc_file_store_unsafe()
        att.invalidate_recordset()
        self.assertEqual(att.raw, b"%PDF moved content")

    def test_switch_copy_to_move(self):
        a = self.attach(self.partner, "early.txt", b"copied first")
        b = self.attach(self.partner, "stale.txt", b"local truth")
        self.sync(a | b)
        self.assertFalse(a.gdrive_moved)
        self.fake.files[b.gdrive_file_id]["content"] = b"something else"  # Drive copy differs
        uploads = len([r for r in self.fake.requests if r[1].startswith("/upload/")])
        self.company.sudo().gdrive_storage = "move"
        (a | b).invalidate_recordset()
        self.assertEqual(set((a | b).mapped("gdrive_state")), {"move"})
        self.sync(a | b)
        self.assertTrue(a.gdrive_moved and b.gdrive_moved)
        self.assertEqual(len([r for r in self.fake.requests if r[1].startswith("/upload/")]) - uploads, 1,
                         "only the file whose Drive copy differs is uploaded again")
        self.assertEqual(self.drive_file(b)["content"], b"local truth")
        self.clear_cache()
        self.assertEqual((a | b).mapped("raw"), [b"copied first", b"local truth"])

    def test_move_mode_update_and_integrity(self):
        self.company.sudo().gdrive_storage = "move"
        att = self.attach(self.partner, "doc.txt", b"first")
        self.sync(att)
        fid = att.gdrive_file_id
        att.write({"raw": b"second version"})
        self.assertFalse(att.gdrive_moved, "new content is local until it is sent")
        self.assertEqual(att.gdrive_state, "changed")
        self.sync(att)
        self.assertTrue(att.gdrive_moved)
        self.assertEqual(att.gdrive_file_id, fid)
        self.clear_cache()
        self.assertEqual(att.raw, b"second version")
        # content changed on Drive behind Odoo's back -> refused (checksum), not silently served
        self.fake.files[fid]["content"] = b"tampered"
        self.clear_cache()
        att.invalidate_recordset()
        with mute_logger("odoo.addons.nxb_gdrive_attachments.models.ir_attachment"):
            self.assertEqual(att.raw, b"")

    def test_move_mode_with_db_storage(self):
        self.env["ir.config_parameter"].sudo().set_param("ir_attachment.location", "db")
        self.company.sudo().gdrive_storage = "move"
        att = self.attach(self.partner, "db.txt", b"stored in db")
        self.assertTrue(att.db_datas)
        self.sync(att)
        self.assertTrue(att.gdrive_moved)
        self.assertFalse(att.db_datas)
        self.clear_cache()
        self.assertEqual(att.raw, b"stored in db")

    def test_stream_of_moved_file(self):
        self.company.sudo().gdrive_storage = "move"
        att = self.attach(self.partner, "s.txt", b"stream me", mimetype="text/plain")
        self.sync(att)
        self.clear_cache()
        stream = att._to_http_stream()
        self.assertEqual(stream.type, "path")
        with open(stream.path, "rb") as f:
            self.assertEqual(f.read(), b"stream me")
        self.assertEqual(stream.download_name, "s.txt")

    def test_bring_back(self):
        # files moved by an earlier use of this database (not readable from the test server) are left aside
        self.env.cr.execute("UPDATE ir_attachment SET store_fname = NULL WHERE store_fname LIKE 'gdrive:%%'")
        self.Att.invalidate_model()
        self.company.sudo().gdrive_storage = "move"
        atts = self.attach(self.partner, "r1.txt", b"one") | self.attach(self.partner, "r2.txt", b"two")
        self.sync(atts)
        self.assertTrue(all(atts.mapped("gdrive_moved")))
        with self.assertRaises(UserError):
            _uninstall(self.env)
        with self.assertRaises(UserError):
            self.company.action_gdrive_disconnect()
        self.company.action_gdrive_bring_back()
        self.assertEqual(self.company.gdrive_storage, "mirror")
        self.clear_cache()
        self.sync()
        atts.invalidate_recordset()
        self.assertFalse(any(atts.mapped("gdrive_moved")))
        self.assertEqual(atts.mapped("raw"), [b"one", b"two"])
        self.assertEqual(set(atts.mapped("gdrive_state")), {"synced"})
        self.assertFalse(self.company.gdrive_restore)
        _uninstall(self.env)  # no longer refused

    def test_bring_back_skips_unreadable_files(self):
        self.env.cr.execute("UPDATE ir_attachment SET store_fname = NULL WHERE store_fname LIKE 'gdrive:%%'")
        self.Att.invalidate_model()
        self.company.sudo().gdrive_storage = "move"
        good, bad = self.attach(self.partner, "good.txt", b"good"), self.attach(self.partner, "bad.txt", b"bad")
        self.sync(good | bad)
        del self.fake.files[bad.gdrive_file_id]
        self.company.action_gdrive_bring_back()
        self.clear_cache()
        with mute_logger("odoo.addons.nxb_gdrive_attachments.models.ir_attachment",
                         "odoo.addons.nxb_gdrive_attachments.models.drive_client"):
            self.sync()
        (good | bad).invalidate_recordset()
        self.assertFalse(good.gdrive_moved)
        self.assertTrue(bad.gdrive_moved)
        self.assertTrue(bad.gdrive_next_try, "the unreadable file waits before the next try")
        self.assertIn("Could not download", bad.gdrive_error)
        self.assertTrue(self.company.gdrive_restore, "still bringing back until every file is home")

    # ------------------------------------------------------------------ errors
    def test_errors_and_retry(self):
        from unittest.mock import patch
        att = self.attach(self.partner, "err.txt")
        self.fake.fail_next = [("POST", "/upload/", 503)] * 3
        with patch("time.sleep"):
            self.sync(att)
        self.assertEqual(att.gdrive_state, "error")
        self.assertEqual(att.gdrive_attempts, 1)
        self.assertIn("503", att.gdrive_error)
        self.assertTrue(att.gdrive_next_try)
        self.assertEqual(self.env["nxb.gdrive.log"].search([("attachment_id", "=", att.id)], limit=1).status,
                         "error")
        self.sync(att)  # next try is in the future: skipped
        self.assertEqual(att.gdrive_state, "error")
        att.action_gdrive_sync()  # manual retry
        att.invalidate_recordset()
        self.assertEqual(att.gdrive_state, "synced")

    def test_permanent_error_stops_retrying(self):
        self.company.sudo().gdrive_refresh_token = "rt-revoked"
        att = self.attach(self.partner, "x.txt")
        self.sync(att)
        self.assertEqual(att.gdrive_state, "error")
        self.assertEqual(att.gdrive_attempts, MAX_ATTEMPTS)
        self.assertIn("revoked", att.gdrive_error)

    def test_multi_company(self):
        other = self.env["res.company"].create({"name": "Branch Two"})
        other.sudo().write({"gdrive_enabled": True, "gdrive_client_id": "c2", "gdrive_client_secret": "s2",
                            "gdrive_refresh_token": "rt-valid", "gdrive_folder_mode": "single"})
        att = self.Att.create({"name": "b2.txt", "raw": b"b2", "company_id": other.id})
        self.sync(att)
        self.assertTrue(self.fake.path_of(att.gdrive_file_id).endswith("Odoo - Branch Two/b2.txt"))
        other.sudo().gdrive_enabled = False
        self.assertFalse(self.Att.create({"name": "b3.txt", "raw": b"b3", "company_id": other.id}).gdrive_state)

    def test_settings_view_and_stats(self):
        att = self.attach(self.partner, "s.txt")
        self.sync(att)
        settings = self.env["res.config.settings"].create({})
        self.assertTrue(settings.gdrive_connected)
        self.assertGreaterEqual(settings.gdrive_count_synced, 1)
        self.assertTrue(settings.gdrive_redirect_uri.endswith("/nxb_gdrive/oauth/callback"))
        res = settings.action_gdrive_test()
        self.assertEqual(res["params"]["type"], "success")
        self.assertEqual(self.company.gdrive_account, "drive.user@example.com")
        self.company.sudo().gdrive_refresh_token = "rt-revoked"
        self.assertEqual(settings.action_gdrive_test()["params"]["type"], "danger")
