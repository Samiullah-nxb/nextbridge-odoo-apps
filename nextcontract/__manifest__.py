# Part of NextContract. See LICENSE file for full copyright and licensing details.

{
    'name': 'NextContract',
    'version': '19.0.1.0.0',
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
* AI clause review and field extraction with Google Gemini, OpenAI,
  Anthropic Claude or a self-hosted OpenAI-compatible model
* Links between contracts and purchase orders
* Bulk migration of legacy contracts

External service: AI processing uses the AI provider you configure under
Configuration → AI Providers. You bring your own API key, billed by that
provider. Contract text and files are sent to the provider for processing.
Nothing is sent to a third-party provider until an administrator has
configured it and given cloud-processing consent in Settings; consent can be
revoked there.

Requires Odoo 19 Enterprise apps: Documents, Sign, Employees (HR), and the
Purchase app. DOCX to PDF conversion needs LibreOffice on the Odoo server.
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
        'purchase',
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
        # AI
        'security/ai_security.xml',
        'data/ai_cron.xml',
        'data/ai_sequence.xml',
        'data/ai_risk_factor_data.xml',
        'views/ai_provider_views.xml',
        'views/res_config_settings_views.xml',
        'views/ai_job_views.xml',
        'views/ai_field_suggestion_views.xml',
        'views/clause_deviation_views.xml',
        'views/contract_version_ai_views.xml',
        'views/ai_review_views.xml',
        'wizard/ai_job_wizard_views.xml',
        'wizard/ai_mapping_wizard_views.xml',
        # Migration
        'wizard/migration_ai_wizard_views.xml',
        'wizard/migration_force_import_wizard_views.xml',
        'views/migration_batch_views.xml',
        'views/migration_record_views.xml',
        'views/migration_menus.xml',
        # Purchase
        'views/contract_purchase_views.xml',
        'views/purchase_order_nextcontract_views.xml',
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
        'static/description/feature_portfolio.png',
        'static/description/feature_lifecycle.png',
        'static/description/feature_portal.png',
        'static/description/feature_word_editor.png',
        'static/description/feature_ai.png',
    ],
    'installable': True,
    'application': True,
    'license': 'OPL-1',
}
