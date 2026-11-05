# Part of NextContract. See LICENSE file for full copyright and licensing details.

import base64
import io
import zipfile

from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestClmExcelExport(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.contract = cls.env['clm.contract'].create({
            'name': 'Excel Export Contract',
            'contract_type_id': cls.env.ref('clm.contract_type_nda').id,
        })

    def _export(self, export_type, **values):
        wizard = self.env['clm.excel.export.wizard'].create({'export_type': export_type, **values})
        wizard.action_export()
        return wizard

    def test_contract_export_is_an_xlsx_containing_the_contract(self):
        wizard = self._export('contracts')
        self.assertEqual(wizard.state, 'done')
        self.assertTrue(wizard.file_name.endswith('.xlsx'))
        self.assertGreaterEqual(wizard.record_count, 1)
        with zipfile.ZipFile(io.BytesIO(base64.b64decode(wizard.file_data))) as workbook:
            strings = workbook.read('xl/sharedStrings.xml').decode()
        self.assertIn('Excel Export Contract', strings)

    def test_status_filter_excludes_non_matching_contracts(self):
        wizard = self._export('contracts', state_filter='active_only')
        with zipfile.ZipFile(io.BytesIO(base64.b64decode(wizard.file_data))) as workbook:
            strings = workbook.read('xl/sharedStrings.xml').decode()
        self.assertIn('Contract ID', strings, 'the header row is always written')
        self.assertNotIn('Excel Export Contract', strings, 'a draft contract is not active')

    def test_every_export_type_builds(self):
        for export_type in ('contracts', 'requests', 'obligations', 'tasks'):
            with self.subTest(export_type=export_type):
                wizard = self._export(export_type)
                self.assertEqual(wizard.state, 'done')
                self.assertTrue(wizard.file_data)

    def test_export_again_resets_the_wizard(self):
        wizard = self._export('contracts')
        wizard.action_export_again()
        self.assertEqual(wizard.state, 'choose')
        self.assertFalse(wizard.file_data)
