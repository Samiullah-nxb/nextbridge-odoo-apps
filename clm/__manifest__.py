# Part of NextContract. See LICENSE file for full copyright and licensing details.

{
    'name': 'NextContract Core - Contract Lifecycle Management Engine',
    'version': '19.0.2.0.0',
    'category': 'Legal/Contract Management',
    'summary': 'Contract management for Odoo 19 Enterprise: contract requests, Word templates and clause library, drafting, negotiation portal, e-signature, obligations, renewals and reporting.',
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

* NextContract AI: clause review and field extraction with Google Gemini
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
        'security/clm_security.xml',
        'security/ir.model.access.csv',
        'security/clm_role_rules.xml',
        'security/clm_field_security.xml',
        'data/clm_sequence_data.xml',
        'data/clm_contract_type_data.xml',
        'data/clm_contract_field_definitions.xml',
        'data/clm_notification_rule_data.xml',
        'views/clm_base_views.xml',
        'views/clm_contract_field_views.xml',
        'views/clm_notification_rule_views.xml',
        'views/clm_home_board_views.xml',
        'views/clm_base_menus.xml',
        # Documents
        'security/ir.model.access-documents.csv',
        'views/clm_contract_version_views.xml',
        'views/clm_contract_documents_views.xml',
        # Library
        'security/ir.model.access-library.csv',
        'wizard/clm_base_docx_preview_wizard_views.xml',
        'views/clm_template_base_docx_version_views.xml',
        'views/clm_library_views.xml',
        'views/clm_library_menus.xml',
        # Drafting
        'security/ir.model.access-draft.csv',
        'report/clm_contract_document_templates.xml',
        'views/clm_contract_draft_views.xml',
        'views/clm_contract_version_content_views.xml',
        'wizard/clm_generate_draft_wizard_views.xml',
        'wizard/clm_contract_version_wizard_views.xml',
        'wizard/clm_pdf_preview_wizard_views.xml',
        # Intake
        'security/clm_intake_rules.xml',
        'security/ir.model.access-intake.csv',
        'data/clm_request_sequence.xml',
        'data/clm_mail_templates.xml',
        'views/clm_contract_request_views.xml',
        'views/clm_contract_views.xml',
        'wizard/clm_request_convert_wizard_views.xml',
        # Workflow
        'security/clm_workflow_security.xml',
        'security/ir.model.access-workflow.csv',
        'data/clm_workflow_rule_data.xml',
        'data/clm_escalation_cron.xml',
        'data/clm_mail_templates_workflow.xml',
        'views/clm_workflow_views.xml',
        'views/clm_contract_workflow_views.xml',
        'views/clm_workflow_menus.xml',
        'wizard/clm_force_approve_wizard_views.xml',
        'wizard/clm_delegate_wizard_views.xml',
        # Negotiation
        'security/ir.model.access-negotiation.csv',
        'security/clm_negotiation_rules.xml',
        'data/clm_mail_templates_negotiation.xml',
        'views/clm_negotiation_views.xml',
        'views/portal_templates.xml',
        'views/clm_negotiation_menus.xml',
        'wizard/clm_send_negotiation_wizard_views.xml',
        # Execution
        'security/clm_execution_security.xml',
        'security/ir.model.access-execution.csv',
        'data/clm_obligation_cron.xml',
        'data/clm_risk_factor_data.xml',
        'data/clm_contract_alert_cron.xml',
        'data/clm_mail_templates_execution.xml',
        'data/clm_activation_checklist_data.xml',
        'data/clm_lifecycle_notification_data.xml',
        'data/clm_sign_item_type_data.xml',
        'wizard/clm_manual_signature_wizard_views.xml',
        'wizard/clm_contract_lifecycle_wizard_views.xml',
        'wizard/clm_contract_final_wizard_views.xml',
        'views/clm_contract_execution_views.xml',
        'views/clm_contract_final_views.xml',
        'views/clm_contract_amendment_views.xml',
        'views/clm_risk_assessment_views.xml',
        'views/clm_obligation_views.xml',
        'views/clm_activation_checklist_views.xml',
        'views/clm_sign_portal_templates.xml',
        'views/clm_execution_menus.xml',
        # Lifecycle overview
        'views/clm_lifecycle_templates.xml',
        'views/clm_contract_lifecycle_views.xml',
        # Reporting
        'security/ir.model.access-reporting.csv',
        'views/clm_contract_reporting_views.xml',
        'views/clm_risk_reporting_views.xml',
        'views/clm_obligation_reporting_views.xml',
        'wizard/clm_excel_export_wizard_views.xml',
        'views/clm_reporting_menus.xml',
    ],
    'assets': {
        'web.assets_backend': [
            # Design system first: tokens and shared components for the suite.
            'clm/static/src/scss/clm_design_tokens.scss',
            'clm/static/src/scss/clm_layout.scss',
            'clm/static/src/scss/clm_components.scss',
            'clm/static/src/scss/clm_navbar.scss',
            'clm/static/src/xml/clm_navbar.xml',
            'clm/static/src/js/theme_toggle/theme_toggle.js',
            'clm/static/src/js/theme_toggle/theme_toggle.xml',
            'clm/static/src/js/home_board/home_board.js',
            'clm/static/src/js/home_board/home_board.xml',
            'clm/static/src/js/home_board/home_board.scss',
            'clm/static/src/js/clm_pdf_preview.js',
            'clm/static/src/docx_tag_editor/docx_tag_editor.js',
            'clm/static/src/docx_tag_editor/docx_preview_html_field.js',
            'clm/static/src/docx_tag_editor/docx_exclusive_active_field.js',
            'clm/static/src/docx_tag_editor/docx_used_icon_field.js',
            'clm/static/src/docx_tag_editor/docx_base_field.js',
            'clm/static/src/docx_tag_editor/docx_tag_editor.xml',
            'clm/static/src/docx_tag_editor/docx_tag_editor.scss',
            'clm/static/src/docx_editor/*',
            'clm/static/src/tag_editor_button/*',
            'clm/static/src/scss/clm_contract_editor.scss',
            'clm/static/src/scss/clm_redline.scss',
            'clm/static/src/js/clm_doc_compare.js',
            'clm/static/src/js/clm_compare_fullscreen.js',
            'clm/static/src/js/clm_pdf_compare_sync.js',
            'clm/static/src/scss/clm_lifecycle.scss',
            'clm/static/src/js/clm_lifecycle.js',
        ],
        'web.assets_frontend': [
            'clm/static/src/scss/clm_sign_portal.scss',
        ],
    },
    'images': ['static/description/banner.png'],
    'installable': True,
    'application': False,
    'license': 'OPL-1',
    'author': 'Nextbridge',
    'maintainer': 'Nextbridge',
    'company': 'Nextbridge',
    'website': 'https://www.nextbridge.pk',
    'support': 'samiullah.3277@nxb.com.pk',
    'price': 450.00,
    'currency': 'USD',
}
