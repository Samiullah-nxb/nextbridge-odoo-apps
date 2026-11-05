# Part of NextContract. See LICENSE file for full copyright and licensing details.

from odoo.modules.module import get_manifest
from odoo.tests.common import TransactionCase, tagged

SUITE = ['clm', 'clm_ai', 'clm_purchase', 'clm_migration']
OPTIONAL = ['clm_ai', 'clm_purchase', 'clm_migration']


def _dependency_closure(name, seen=None):
    seen = set() if seen is None else seen
    for dep in get_manifest(name).get('depends', []):
        if dep not in seen:
            seen.add(dep)
            _dependency_closure(dep, seen)
    return seen


@tagged('post_install', '-at_install')
class TestSuiteInstall(TransactionCase):
    """NextContract is one app plus optional add-ons."""

    def test_core_app_needs_no_optional_addon_or_purchase(self):
        closure = _dependency_closure('clm')
        for name in OPTIONAL + ['purchase']:
            with self.subTest(module=name):
                self.assertNotIn(name, closure)

    def test_addons_build_on_the_core_app(self):
        for name in OPTIONAL:
            with self.subTest(module=name):
                self.assertIn('clm', get_manifest(name)['depends'])

    def test_purchase_bridge_installs_itself(self):
        manifest = get_manifest('clm_purchase')
        self.assertTrue(manifest['auto_install'])
        self.assertEqual(sorted(manifest['depends']), ['clm', 'purchase'])

    def test_suite_modules_ship_their_store_listing(self):
        for name in SUITE:
            with self.subTest(module=name):
                manifest = get_manifest(name)
                self.assertLessEqual(len(manifest['name']), 25, 'Apps Store name limit')
                self.assertEqual(manifest['license'], 'OPL-1')
                self.assertTrue(manifest.get('images'), 'no store thumbnail')

    def test_suite_needs_no_addon_outside_odoo_and_nextcontract(self):
        """The Word editor is bundled in clm, not a separate addon."""
        for name in SUITE:
            with self.subTest(module=name):
                self.assertNotIn('universal_docs', get_manifest(name)['depends'])
