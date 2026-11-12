# Part of NextContract. See LICENSE file for full copyright and licensing details.

{
    'name': 'NextContract AI',
    'version': '19.0.2.1.0',
    'category': 'Legal/Contract Management',
    'summary': 'AI-assisted field extraction and semantic clause analysis',
    'description': """
Extracts contract fields and compares inbound clauses with your clause
library, as suggestions that a reviewer accepts or rejects. Jobs run in the
background.

External service: AI processing uses the AI provider you configure under
Configuration → AI Providers: Google Gemini, OpenAI, Anthropic Claude, or any
OpenAI-compatible server (including one you host yourself). You bring your own
API key, billed by that provider. Contract text and files are sent to the
provider for processing. Nothing is sent to a third-party provider until an
administrator has configured it and given cloud-processing consent in
Settings; consent can be revoked there.
    """,
    'depends': ['nextcontract'],
    'data': [
        'security/ai_security.xml',
        'security/ir.model.access.csv',
        'data/ai_cron.xml',
        'data/ai_sequence.xml',
        'data/risk_factor_data.xml',
        'views/ai_provider_views.xml',
        'views/res_config_settings_views.xml',
        'views/ai_job_views.xml',
        'views/ai_field_suggestion_views.xml',
        'views/clause_deviation_views.xml',
        'views/contract_version_views.xml',
        'views/ai_review_views.xml',
        'wizard/ai_job_wizard_views.xml',
        'wizard/ai_mapping_wizard_views.xml',
    ],
    'images': ['static/description/banner.png'],
    'installable': True,
    'application': False,
    'license': 'OPL-1',
    'author': 'Nextbridge',
    'maintainer': 'Nextbridge',
    'company': 'Nextbridge',
    'website': 'https://www.nextbridge.pk',
    'support': 'samiullah.3277@nxb.com.pk',
}
