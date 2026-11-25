{
    'name': 'Stock Accounting Automation',
    'version': '19.0.1.0.0',
    'summary': 'Automated journal entries for inventory operations (perpetual valuation, interim accounts)',
    'description': """
Stock Accounting Automation
===========================
*Automated Journal Entries for Inventory Operations*

Odoo 19 no longer posts a journal entry when goods are received or delivered:
with perpetual valuation the stock account is only touched by vendor bills,
customer invoices (COGS lines) and moves to locations that carry their own
valuation account.

This module restores the classic perpetual (anglo-saxon) flow based on
interim accounts, for every product category in perpetual valuation:

* Receipt: Dr Stock Valuation / Cr Stock Interim (Received)
* Vendor bill: Dr Stock Interim (Received) / Cr Payable
* Delivery: Dr Stock Interim (Delivered) / Cr Stock Valuation
* Customer invoice: Dr COGS (Expense) / Cr Stock Interim (Delivered)
* Customer and vendor returns: the reversed counterparts
* Inventory adjustments, scrap and production: location valuation account,
  falling back to the product expense account
* Value change on a posted move (bill price, manual adjustment, quantity
  correction): adjustment entry for the difference
* Standard / average cost update: revaluation entry for the on-hand quantity
* Accrued expense / revenue entries aware of the interim balances

Interim lines are reconciled automatically between the stock entries and the
related bills / invoices.
    """,
    'category': 'Supply Chain/Inventory',
    "author": "Nextbridge",
    "maintainer": "Nextbridge",
    "company": "Nextbridge",
    "website": "https://www.nextbridge.pk",
    "support": "samiullah.3277@nxb.com.pk",
    "license": "OPL-1",
    "price": 25.00,
    "currency": "USD",
    'depends': ['stock_account', 'purchase_stock', 'sale_stock'],
    'data': [
        'views/res_config_settings_views.xml',
        'views/product_category_views.xml',
        'views/stock_move_views.xml',
    ],
    'images': ['static/description/banner.png'],
    'post_init_hook': '_post_init_hook',
    'installable': True,
    'application': False,
}
