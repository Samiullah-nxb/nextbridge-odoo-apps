# -*- coding: utf-8 -*-
{
    "name": "AI Prospecting Agents for LinkedIn",
    "summary": "Prospect on LinkedIn with AI agents: find people, invite, chat, follow up, publish posts "
               "and hand warm leads to Odoo CRM. Multi-account, safe limits, live dashboard.",
    "description": """
AI Prospecting Agents for LinkedIn
==================================
Multi-account LinkedIn prospecting for Odoo 18 driven by AI agents (Connection, Chat, Follow-up and
Activity agents) through a secure MCP connector. Built-in browser engine for self-hosted servers or an
AI browser agent for Odoo.sh. Daily limits, send windows and random delays enforced by Odoo; warm leads
created in CRM; post drafting and scheduling; live dashboard with an offline prospects map.
""",
    "version": "18.0.1.0.0",
    "category": "Sales/CRM",
    "author": "Nextbridge",
    "maintainer": "Nextbridge",
    "company": "Nextbridge",
    "website": "https://www.nextbridge.pk",
    "support": "samiullah.3277@nxb.com.pk",

    "license": "OPL-1",
    "price": 500.00,
    "currency": "USD",
    "images": ["static/description/banner.png"],
    "depends": ["base", "mail", "crm", "calendar", "contacts", "sales_team"],
    "external_dependencies": {"python": []},
    "data": [
        "security/security.xml",
        "security/ir.model.access.csv",
        "data/ir_sequence.xml",
        "data/ir_cron.xml",
        "data/mail_activity_type.xml",
        "views/li_profile_views.xml",
        "views/li_service_views.xml",
        "views/li_persona_views.xml",
        "views/li_prospect_views.xml",
        "views/li_queue_views.xml",
        "views/li_post_views.xml",
        "views/li_log_views.xml",
        "views/li_token_views.xml",
        "views/crm_lead_views.xml",
        "views/res_config_settings_views.xml",
        "wizard/li_connect_wizard_views.xml",
        "views/li_dashboard_action.xml",
        "views/menus.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "nb_linkedin_ai_prospecting/static/src/dashboard/*.js",
            "nb_linkedin_ai_prospecting/static/src/dashboard/*.xml",
            "nb_linkedin_ai_prospecting/static/src/dashboard/*.scss",
        ],
    },
    "installable": True,
    "application": True,
}
