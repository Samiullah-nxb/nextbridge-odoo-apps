# Part of NextContract. See LICENSE file for full copyright and licensing details.

{
    'name': 'NextContract - Contract Lifecycle Management (CLM) for Odoo',
    'version': '19.0.2.0.0',
    'category': 'Legal/Contract Management',
    'summary': 'Contract management for Odoo 19 Enterprise: contract requests, Word templates and clause library, drafting, negotiation portal, e-signature, obligations, renewals, AI extraction, migration and purchase links.',
    'description': """
NextContract Suite
==================

Installs the complete NextContract contract lifecycle management suite:

* NextContract (core): intake, workflow, templates and clause library, Word editor, drafting,
  negotiation portal, Odoo Sign, obligations, renewals and reporting
* NextContract AI: contract field extraction and clause analysis with Google Gemini
* NextContract Migration: import legacy contracts from ZIP files and CSV or Excel sheets
* NextContract Purchase: link contracts to purchase orders
""",
    'license': 'OPL-1',
    'author': 'Nextbridge',
    'maintainer': 'Nextbridge',
    'company': 'Nextbridge',
    'website': 'https://www.nextbridge.pk',
    'support': 'samiullah.3277@nxb.com.pk',
    'depends': ['clm', 'clm_ai', 'clm_migration', 'clm_purchase'],
    'data': [],
    'images': ['static/description/banner.png'],
    'installable': True,
    'application': True,
    'auto_install': False,
}
