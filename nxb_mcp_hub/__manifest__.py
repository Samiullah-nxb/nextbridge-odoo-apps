# -*- coding: utf-8 -*-
# Copyright 2026 Nextbridge. License OPL-1.
{
    "name": "Multi-Database MCP Server – AI Connector for Odoo",
    "summary": "Connect AI assistants to one or many Odoo databases through MCP: read and update data, "
               "build dashboards, manage CRM, campaigns, access rights, SEO and website pages.",
    "description": """
Multi-Database MCP Server – AI Connector for Odoo
=================================================
An MCP (Model Context Protocol) server inside Odoo. One connector reaches several Odoo databases, on this
server and on other Odoo servers. Switchable tool packs: data, dashboards, CRM, email campaigns, access control,
website SEO, website builder and read-only SQL. API keys with scopes, OAuth 2.1 sign-in, read-only mode,
rate limits and a complete activity log.
""",
    "version": "18.0.1.0.0",
    "category": "Productivity",
    "author": "Nextbridge",
    "maintainer": "Nextbridge",
    "company": "Nextbridge",
    "website": "https://www.nextbridge.pk",
    "support": "samiullah.3277@nxb.com.pk",
    "license": "OPL-1",
    "price": 99.00,
    "currency": "USD",
    "images": ["static/description/banner.png"],
    "depends": ["base", "web"],
    "data": [
        "security/security.xml",
        "security/ir.model.access.csv",
        "views/connection_views.xml",
        "views/key_views.xml",
        "views/log_views.xml",
        "views/oauth_views.xml",
        "views/oauth_templates.xml",
        "views/res_config_settings_views.xml",
        "views/menus.xml",
        "views/dashboard_views.xml",
        "views/brief_views.xml",
        "views/access_views.xml",
        "views/site_views.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "nxb_mcp_hub/static/src/board/*",
            "nxb_mcp_hub/static/src/dashboard/*",
            "nxb_mcp_hub/static/src/overview/*",
        ],
    },
    "post_init_hook": "_post_init",
    "uninstall_hook": "_uninstall",
    "installable": True,
    "application": True,
}
