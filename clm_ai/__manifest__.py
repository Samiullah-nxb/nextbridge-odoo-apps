# Part of NextContract. See LICENSE file for full copyright and licensing details.

{
    'name': 'NextContract AI - Contract Data Extraction and Clause Analysis',
    'version': '19.0.2.0.0',
    'category': 'Legal/Contract Management',
    'summary': 'AI for NextContract: extract contract fields from documents and compare clauses with your clause library to flag deviations and risk (Google Gemini, your own API key).',
    'description': """
Extracts contract fields and compares inbound clauses with your clause
library, as suggestions that a reviewer accepts or rejects. Jobs run in the
background.

External service: AI processing uses Google Gemini and needs your own Gemini
API key (billed by Google). Contract text and files are sent to Google for
processing. Nothing is sent until an administrator enters the key and gives
cloud-processing consent in Settings; consent can be revoked there.
    """,
    'depends': ['clm'],
    'data': [
        'security/clm_ai_security.xml',
        'security/ir.model.access.csv',
        'data/clm_ai_cron.xml',
        'data/clm_ai_sequence.xml',
        'data/clm_risk_factor_data.xml',
        'views/res_config_settings_views.xml',
        'views/clm_ai_job_views.xml',
        'views/clm_ai_field_suggestion_views.xml',
        'views/clm_clause_deviation_views.xml',
        'views/clm_contract_version_views.xml',
        'views/clm_ai_review_views.xml',
        'wizard/clm_ai_job_wizard_views.xml',
        'wizard/clm_ai_mapping_wizard_views.xml',
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
