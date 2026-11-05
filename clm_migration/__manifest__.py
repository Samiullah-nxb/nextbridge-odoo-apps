# Part of NextContract. See LICENSE file for full copyright and licensing details.

{
    'name': 'NextContract Migration - Import Legacy Contracts',
    'version': '19.0.2.0.0',
    'category': 'Legal/Contract Management',
    'summary': 'Import existing contracts into NextContract from ZIP files and CSV or Excel sheets, with duplicate detection and optional AI field extraction.',
    # The AI import wizard queues clm.ai.job records.
    'depends': ['clm', 'clm_ai'],
    'data': [
        'security/ir.model.access.csv',
        'wizard/clm_migration_ai_wizard_views.xml',
        'wizard/clm_migration_force_import_wizard_views.xml',
        'views/clm_migration_batch_views.xml',
        'views/clm_migration_record_views.xml',
        'views/clm_migration_menus.xml',
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
