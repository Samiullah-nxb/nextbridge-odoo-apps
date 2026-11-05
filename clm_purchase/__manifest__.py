# Part of NextContract. See LICENSE file for full copyright and licensing details.

{
    'name': 'NextContract Purchase - Link Contracts to Purchase Orders',
    'version': '19.0.2.0.0',
    'category': 'Legal/Contract Management',
    'summary': 'Link NextContract contracts to Odoo purchase orders and see each side from the other.',
    'depends': ['clm', 'purchase'],
    'data': [
        'views/clm_contract_purchase_views.xml',
        'views/purchase_order_clm_views.xml',
    ],
    'images': ['static/description/banner.png'],
    'installable': True,
    'application': False,
    # Bridge module: installs itself once both NextContract and Purchase are.
    'auto_install': True,
    'license': 'OPL-1',
    'author': 'Nextbridge',
    'maintainer': 'Nextbridge',
    'company': 'Nextbridge',
    'website': 'https://www.nextbridge.pk',
    'support': 'samiullah.3277@nxb.com.pk',
}
