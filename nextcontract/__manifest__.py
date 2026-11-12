# Part of NextContract. See LICENSE file for full copyright and licensing details.

{
    'name': 'NextContract',
    'version': '19.0.2.0.0',
    'category': 'Legal/Contract Management',
    'summary': 'Contract Lifecycle Management: intake, drafting, negotiation, signature and obligations',
    'description': """
NextContract
============

Contract Lifecycle Management for Odoo 19 Enterprise:

* Contracts, contract types, custom fields, roles and audit trail
* Intake requests and conversion to contracts
* Approval workflow with escalation and delegation
* Clause and template library with a built-in Word editor
* Drafting, versioning and DOCX/PDF generation
* Negotiation rounds, redlining and counterparty portal
* Signature (Odoo Sign), activation, renewal, amendment and obligations
* Lifecycle overview, dashboards and Excel export

Optional add-ons:

* NextContract AI: clause review and field extraction with Google Gemini,
  OpenAI, Anthropic Claude or a self-hosted OpenAI-compatible model
* NextContract Purchase: links contracts to purchase orders (installs itself
  when Purchase is installed)
* NextContract Migration: bulk import of legacy contracts

Requires Odoo 19 Enterprise apps: Documents, Sign, Employees (HR).
DOCX to PDF conversion needs LibreOffice on the Odoo server.
    """,
    # web_enterprise owns the color_scheme service/field the systray theme
    # toggle builds on, which makes the suite Enterprise-only.
    'depends': [
        'base',
        'mail',
        'hr',
        'portal',
        'documents',
        'html_editor',
        'sign',
        'web_enterprise',
    ],
    # PyPI names: Odoo 19 checks external dependencies by distribution name.
    'external_dependencies': {
        'python': ['python-docx', 'docxtpl', 'xlsxwriter'],
    },
    'data': [
        # Base
        'security/nextcontract_security.xml',
        'security/ir.model.access.csv',
        'security/role_rules.xml',
        'security/field_security.xml',
        'data/sequence_data.xml',
        'data/contract_type_data.xml',
        'data/contract_field_definitions.xml',
        'data/notification_rule_data.xml',
        'views/base_views.xml',
        'views/contract_field_views.xml',
        'views/notification_rule_views.xml',
        'views/home_board_views.xml',
        'views/base_menus.xml',
        # Documents
        'security/ir.model.access-documents.csv',
        'views/contract_version_views.xml',
        'views/contract_documents_views.xml',
        # Library
        'security/ir.model.access-library.csv',
        'wizard/base_docx_preview_wizard_views.xml',
        'views/template_base_docx_version_views.xml',
        'views/library_views.xml',
        'views/library_menus.xml',
        # Drafting
        'security/ir.model.access-draft.csv',
        'report/contract_document_templates.xml',
        'views/contract_draft_views.xml',
        'views/contract_version_content_views.xml',
        'wizard/generate_draft_wizard_views.xml',
        'wizard/contract_version_wizard_views.xml',
        'wizard/pdf_preview_wizard_views.xml',
        # Intake
        'security/intake_rules.xml',
        'security/ir.model.access-intake.csv',
        'data/request_sequence.xml',
        'data/mail_templates.xml',
        'views/contract_request_views.xml',
        'views/contract_views.xml',
        'wizard/request_convert_wizard_views.xml',
        # Workflow
        'security/workflow_security.xml',
        'security/ir.model.access-workflow.csv',
        'data/workflow_rule_data.xml',
        'data/escalation_cron.xml',
        'data/mail_templates_workflow.xml',
        'views/workflow_views.xml',
        'views/contract_workflow_views.xml',
        'views/workflow_menus.xml',
        'wizard/force_approve_wizard_views.xml',
        'wizard/delegate_wizard_views.xml',
        # Negotiation
        'security/ir.model.access-negotiation.csv',
        'security/negotiation_rules.xml',
        'data/mail_templates_negotiation.xml',
        'views/negotiation_views.xml',
        'views/portal_templates.xml',
        'views/negotiation_menus.xml',
        'wizard/send_negotiation_wizard_views.xml',
        # Execution
        'security/execution_security.xml',
        'security/ir.model.access-execution.csv',
        'data/obligation_cron.xml',
        'data/risk_factor_data.xml',
        'data/contract_alert_cron.xml',
        'data/mail_templates_execution.xml',
        'data/activation_checklist_data.xml',
        'data/lifecycle_notification_data.xml',
        'data/sign_item_type_data.xml',
        'wizard/manual_signature_wizard_views.xml',
        'wizard/contract_lifecycle_wizard_views.xml',
        'wizard/contract_final_wizard_views.xml',
        'views/contract_execution_views.xml',
        'views/contract_final_views.xml',
        'views/contract_amendment_views.xml',
        'views/risk_assessment_views.xml',
        'views/obligation_views.xml',
        'views/activation_checklist_views.xml',
        'views/sign_portal_templates.xml',
        'views/execution_menus.xml',
        # Lifecycle overview
        'views/lifecycle_templates.xml',
        'views/contract_lifecycle_views.xml',
        # Reporting
        'security/ir.model.access-reporting.csv',
        'views/contract_reporting_views.xml',
        'views/risk_reporting_views.xml',
        'views/obligation_reporting_views.xml',
        'wizard/excel_export_wizard_views.xml',
        'views/reporting_menus.xml',
    ],
    'assets': {
        'web.assets_backend': [
            # Design system first: tokens and shared components for the suite.
            'nextcontract/static/src/scss/design_tokens.scss',
            'nextcontract/static/src/scss/layout.scss',
            'nextcontract/static/src/scss/components.scss',
            'nextcontract/static/src/scss/navbar.scss',
            'nextcontract/static/src/xml/navbar.xml',
            'nextcontract/static/src/js/theme_toggle/theme_toggle.js',
            'nextcontract/static/src/js/theme_toggle/theme_toggle.xml',
            'nextcontract/static/src/js/home_board/home_board.js',
            'nextcontract/static/src/js/home_board/home_board.xml',
            'nextcontract/static/src/js/home_board/home_board.scss',
            'nextcontract/static/src/js/pdf_preview.js',
            'nextcontract/static/src/docx_tag_editor/docx_tag_editor.js',
            'nextcontract/static/src/docx_tag_editor/docx_preview_html_field.js',
            'nextcontract/static/src/docx_tag_editor/docx_exclusive_active_field.js',
            'nextcontract/static/src/docx_tag_editor/docx_used_icon_field.js',
            'nextcontract/static/src/docx_tag_editor/docx_base_field.js',
            'nextcontract/static/src/docx_tag_editor/docx_tag_editor.xml',
            'nextcontract/static/src/docx_tag_editor/docx_tag_editor.scss',
            'nextcontract/static/src/docx_editor/*',
            'nextcontract/static/src/tag_editor_button/*',
            'nextcontract/static/src/scss/contract_editor.scss',
            'nextcontract/static/src/scss/redline.scss',
            'nextcontract/static/src/js/doc_compare.js',
            'nextcontract/static/src/js/compare_fullscreen.js',
            'nextcontract/static/src/js/pdf_compare_sync.js',
            'nextcontract/static/src/scss/lifecycle.scss',
            'nextcontract/static/src/js/lifecycle.js',
        ],
        'web.assets_frontend': [
            'nextcontract/static/src/scss/sign_portal.scss',
        ],
    },
    'images': [
        'static/description/banner.png',
        'static/description/editor_screenshot.png',
        'static/description/contracts_screenshot.png',
        'static/description/contract_screenshot.png',
        'static/description/lifecycle_screenshot.png',
        'static/description/template_screenshot.png',
    ],
    'installable': True,
    'application': True,
    'license': 'OPL-1',
    'author': 'Nextbridge',
    'maintainer': 'Nextbridge',
    'company': 'Nextbridge',
    'website': 'https://www.nextbridge.pk',
    'support': 'samiullah.3277@nxb.com.pk',
    'price': 499.00,
    'currency': 'USD',
}
