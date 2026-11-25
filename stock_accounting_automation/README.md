# Stock Accounting Automation

**Automated journal entries for inventory operations** in Odoo 19.

Odoo 19 no longer posts a journal entry when goods are received, delivered, returned, counted or
scrapped: with perpetual valuation the stock account only moves when bills and invoices are posted.
This module restores the classic perpetual (anglo-saxon) flow with two interim accounts, so the
Stock Valuation account always equals the value of the goods on hand.

| Event | Journal entry |
|---|---|
| Receipt | Dr Stock Valuation / Cr Stock Interim (Received) |
| Vendor bill | Dr Stock Interim (Received) / Cr Payable |
| Delivery | Dr Stock Interim (Delivered) / Cr Stock Valuation |
| Customer invoice (COGS) | Dr Expense / Cr Stock Interim (Delivered) |
| Customer / vendor return | The matching entry, reversed |
| Inventory count, scrap, production | Location valuation account, otherwise expense |
| Cost change, *Adjust Valuation* | Stock Valuation / Stock Variation for the quantity on hand |

## Main features

* An entry for every receipt, delivery, return, inventory adjustment, scrap and manufacturing move
  of storable products in **Perpetual** categories (FIFO, Average Cost, Standard Price).
* Automatic reconciliation of the interim accounts with bills, invoices, credit notes and refunds.
* Bill price differences: goods still in stock are revalued, goods already sold go to the price
  difference account. Bill reset to draft, cancel or reverse resynchronises the receipt.
* Adjustments dated on the stock move date and lock-date aware: the Inventory Valuation closing has
  nothing to correct.
* Accrued expense / revenue entries that do not duplicate the interim balances.
* Landed costs booked once; one initial entry per stock move, guaranteed.
* Per-company switch and accounts, category overrides; works for users without accounting rights.

## Compatibility and dependencies

| | |
|---|---|
| Odoo | 19.0 Community and Enterprise |
| Version | 19.0.1.0.0 |
| Depends on | `stock_account`, `purchase_stock`, `sale_stock` |
| Optional | `stock_landed_costs`, `mrp` |
| Author | Nextbridge |

## Installation

1. Back up the database and filestore. Install at the start of an accounting period: moves
   validated before installation are not back-posted.
2. Put the `stock_accounting_automation` folder on the addons path.
3. *Apps › Update Apps List*, search *Stock Accounting Automation*, **Activate**; or:

   ```bash
   odoo -c /etc/odoo/odoo.conf -d <db> -i stock_accounting_automation --stop-after-init
   ```

The installation creates *Stock Interim (Received)* and *Stock Interim (Delivered)* for each
company and enables *Inventory › Configuration › Settings › Valuation › Journal Entries on Stock
Moves*.

## Description

The Odoo Apps page, [`static/description/index.html`](static/description/index.html), covers the
features, the journal entries posted, configuration, the accounts used and screenshots.

## Tests

```bash
odoo -c /etc/odoo/odoo.conf -d <db> -u stock_accounting_automation --test-enable \
     --test-tags /stock_accounting_automation --http-port=8079 --stop-after-init
```

Landed-cost and manufacturing tests are skipped unless `stock_landed_costs` and `mrp_account` are
installed. Odoo's own `stock_account` tests assume the standard Odoo 19 posting scheme and fail when
this module is enabled; run this module's tests on their own.
