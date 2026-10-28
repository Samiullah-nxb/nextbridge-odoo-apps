# -*- coding: utf-8 -*-
# Copyright 2026 Nextbridge. License OPL-1.
import base64
import os
import shutil

from odoo.tests import TransactionCase

from ..models import drive_client
from .fake_drive import FakeDrive


class GDriveSetup:
    """Mixin: a fake Google Drive server and a company connected to it."""

    @classmethod
    def _setup_gdrive(cls):
        cls.fake = FakeDrive().start()
        cls.addClassCleanup(cls.fake.stop)
        icp = cls.env["ir.config_parameter"].sudo()
        for key, value in cls.fake.params().items():
            icp.set_param(key, value)
        cls.company = cls.env.company
        cls.company.sudo().write({
            "gdrive_enabled": True, "gdrive_auth": "oauth", "gdrive_client_id": "client-1",
            "gdrive_client_secret": "secret-1", "gdrive_refresh_token": "rt-valid", "gdrive_scope": "app",
            "gdrive_storage": "mirror", "gdrive_folder_mode": "model", "gdrive_root_folder_id": False,
            "gdrive_shared_drive_id": False, "gdrive_model_scope": "all", "gdrive_include_fields": False,
            "gdrive_include_public": False, "gdrive_min_size": 0, "gdrive_delete": "trash",
            "gdrive_share_link": False})
        cls.Att = cls.env["ir.attachment"]

    def setUp(self):
        super().setUp()
        drive_client._TOKENS.clear()
        cache = self.env["ir.attachment"]._gdrive_cache_dir()
        shutil.rmtree(cache, ignore_errors=True)
        os.makedirs(cache, exist_ok=True)

    def attach(self, record, name="file.txt", data=b"hello drive", **extra):
        vals = {"name": name, "raw": data, "res_model": record._name, "res_id": record.id}
        vals.update(extra)
        return self.Att.create(vals)

    def sync(self, atts=None):
        self.env["ir.attachment"]._gdrive_process(atts.ids if atts else None, commit=False)
        if atts:
            atts.invalidate_recordset()

    def drive_file(self, att):
        return self.fake.files.get(att.gdrive_file_id)

    def clear_cache(self):
        cache = self.env["ir.attachment"]._gdrive_cache_dir()
        for name in os.listdir(cache):
            os.unlink(os.path.join(cache, name))


class GDriveCase(GDriveSetup, TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_gdrive()
        cls.partner = cls.env["res.partner"].create({"name": "Acme Corp"})
        cls.partner2 = cls.env["res.partner"].create({"name": "Globex"})


def service_key_b64():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption()).decode()
    import json
    return base64.b64encode(json.dumps({
        "type": "service_account", "client_email": "odoo-sync@project.iam.gserviceaccount.com",
        "private_key": pem, "token_uri": ""}).encode())
