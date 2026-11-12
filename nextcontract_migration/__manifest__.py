# Part of NextContract. See LICENSE file for full copyright and licensing details.

{
    'name': 'NextContract Migration',
    'version': '19.0.2.0.0',
    'category': 'Legal/Contract Management',
    'summary': 'Contract Migration Tools',
    # The AI import wizard queues nextcontract.ai.job records.
    'depends': ['nextcontract', 'nextcontract_ai'],
    'data': [
        'security/ir.model.access.csv',
        'wizard/migration_ai_wizard_views.xml',
        'wizard/migration_force_import_wizard_views.xml',
        'views/migration_batch_views.xml',
        'views/migration_record_views.xml',
        'views/migration_menus.xml',
    ],
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
