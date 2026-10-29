# -*- coding: utf-8 -*-
# Copyright 2026 Nextbridge. License OPL-1.
{
    "name": "Shared User Login with Employee PIN",
    "summary": "Several employees work on one shared Odoo user (shop floor, warehouse, front desk): each one "
               "signs in with a personal PIN and is shown as the author of chatter, Discuss and e-mails.",
    "description": """
Shared User Login with Employee PIN
===================================
After the shared user's normal login, each employee picks their name and types their PIN. Messages, logged
notes, field changes, Discuss messages and e-mails then carry the employee's name instead of the shared user's.
Switch employee in one click, automatic lock after inactivity, PIN lockout after failed attempts and a full
sign-in history.
""",
    "version": "18.0.1.0.0",
    "category": "Human Resources/Employees",
    "author": "Nextbridge",
    "maintainer": "Nextbridge",
    "company": "Nextbridge",
    "website": "https://www.nextbridge.pk",
    "support": "samiullah.3277@nxb.com.pk",
    "license": "OPL-1",
    "price": 149.00,
    "currency": "USD",
    "images": ["static/description/banner.png"],
    "depends": ["mail", "hr"],
    "data": [
        "security/ir.model.access.csv",
        "views/templates.xml",
        "views/res_users_views.xml",
        "views/hr_employee_views.xml",
        "views/log_views.xml",
        "views/res_config_settings_views.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "nxb_shared_user_login/static/src/js/user_menu.js",
        ],
    },
    "uninstall_hook": "_uninstall",
    "installable": True,
    "application": False,
}
