# Part of NextContract. See LICENSE file for full copyright and licensing details.

from odoo.modules.module import get_manifest
from odoo.tests.common import TransactionCase, tagged

# Former add-on modules, now part of nextcontract; they must not come back.
MERGED = ['nextcontract_ai', 'nextcontract_purchase', 'nextcontract_migration']


@tagged('post_install', '-at_install')
class TestSuiteInstall(TransactionCase):
    """NextContract is a single module: one purchase, one install."""

    def test_single_module_includes_purchase(self):
        depends = get_manifest('nextcontract')['depends']
        self.assertIn('purchase', depends)
        for name in MERGED:
            with self.subTest(module=name):
                self.assertNotIn(name, depends)

    def test_merged_modules_are_gone(self):
        for name in MERGED:
            with self.subTest(module=name):
                self.assertFalse(get_manifest(name), '%s must stay merged' % name)
                self.assertFalse(
                    self.env['ir.module.module'].search([('name', '=', name)]),
                    '%s still registered in ir.module.module' % name,
                )

    def test_merged_features_are_installed(self):
        for xmlid in (
            'nextcontract.action_nextcontract_ai_provider',
            'nextcontract.action_nextcontract_ai_review_queue',
            'nextcontract.ir_cron_process_pending_ai_jobs',
            'nextcontract.risk_factor_ai_deviation',
        ):
            with self.subTest(xmlid=xmlid):
                self.assertTrue(self.env.ref(xmlid, raise_if_not_found=False))
        for model in ('nextcontract.ai.job', 'nextcontract.migration.batch'):
            with self.subTest(model=model):
                self.assertIn(model, self.env)
        self.assertIn('nextcontract_contract_ids', self.env['purchase.order']._fields)

    def test_store_listing(self):
        manifest = get_manifest('nextcontract')
        self.assertLessEqual(len(manifest['name']), 25, 'Apps Store name limit')
        self.assertEqual(manifest['license'], 'OPL-1')
        self.assertTrue(manifest.get('images'), 'no store thumbnail')

    def test_needs_no_addon_outside_odoo(self):
        """The Word editor is bundled in nextcontract, not a separate addon."""
        self.assertNotIn('universal_docs', get_manifest('nextcontract')['depends'])
