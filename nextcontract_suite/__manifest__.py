# Part of NextContract. See LICENSE file for full copyright and licensing details.

{
    'name': 'NextContract - Complete Suite (Core, AI, Migration, Purchase)',
    'version': '19.0.2.0.0',
    'category': 'Legal/Contract Management',
    'summary': 'One purchase installs all of NextContract: the Contract Lifecycle Management core app, '
               'NextContract AI, NextContract Migration and NextContract Purchase',
    'description': """
NextContract - Complete Suite
=============================

Installs the full NextContract package in one step: the NextContract app and its AI, Migration and
Purchase add-ons. All features are in the NextContract app; this module only brings the modules together.
""",
    'depends': [
        'nextcontract',
        'nextcontract_ai',
        'nextcontract_migration',
        'nextcontract_purchase',
    ],
    'data': [],
    'images': ['static/description/banner.png'],
    'installable': True,
    'application': False,
    'auto_install': False,
    'license': 'OPL-1',
    'author': 'Nextbridge',
    'maintainer': 'Nextbridge',
    'company': 'Nextbridge',
    'website': 'https://www.nextbridge.pk',
    'support': 'samiullah.3277@nxb.com.pk',
}
