# -*- coding: utf-8 -*-
# Copyright 2026 Nextbridge. License OPL-1.
{
    "name": "Google Drive Attachment Storage",
    "summary": "Store Odoo attachments on Google Drive: automatic upload, folders per document type or record, "
               "update and delete sync, move files out of Odoo to save space, Shared Drives.",
    "description": """
Google Drive Attachment Storage
===============================
Sends the files attached to Odoo documents to Google Drive automatically: one folder, one folder per
document type, or one folder per record. Deleting, renaming or replacing a file in Odoo does the same on
Drive. Keep a copy in Odoo, or move the files out of Odoo and let Odoo read them from Drive when needed.
Google account (OAuth) or Workspace service account with Shared Drives. Existing attachments can be sent
in one click and brought back at any time.
""",
    "version": "18.0.1.0.0",
    "category": "Productivity/Documents",
    "author": "Nextbridge",
    "maintainer": "Nextbridge",
    "company": "Nextbridge",
    "website": "https://www.nextbridge.pk",
    "support": "samiullah.3277@nxb.com.pk",
    "license": "OPL-1",
    "price": 45.00,
    "currency": "USD",
    "images": ["static/description/banner.png"],
    "depends": ["base", "base_setup", "web"],
    "data": [
        "security/ir.model.access.csv",
        "data/ir_cron.xml",
        "views/gdrive_views.xml",
        "views/res_config_settings_views.xml",
        "views/menus.xml",
    ],
    "uninstall_hook": "_uninstall",
    "installable": True,
    "application": False,
}
