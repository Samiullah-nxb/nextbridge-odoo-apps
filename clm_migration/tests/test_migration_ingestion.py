# Part of NextContract. See LICENSE file for full copyright and licensing details.

from odoo.tests.common import TransactionCase
from odoo.exceptions import UserError
import base64
import io
import zipfile
import openpyxl

class TestMigrationIngestion(TransactionCase):

    def setUp(self):
        super().setUp()
        self.contract_type_nda = self.env.ref('clm.contract_type_nda', raise_if_not_found=False)
        self.batch = self.env['clm.migration.batch'].create({
            'name': 'Ingestion Test Batch'
        })

    def _create_zip_package(self, records_data, include_pdfs=True, path_traversal=False):
        """Helper to create a zip file in memory with contracts.xlsx and optional pdfs."""
        zip_buffer = io.BytesIO()
        
        # Create Excel workbook
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Contracts"
        
        headers = ['Vendor Name', 'Contract Title', 'Contract Number', 'Effective Date', 'Expiry Date', 'Contract Value', 'Document Path']
        ws.append(headers)
        
        for row in records_data:
            ws.append(row)
            
        xlsx_buffer = io.BytesIO()
        wb.save(xlsx_buffer)
        
        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
            zf.writestr('contracts.xlsx', xlsx_buffer.getvalue())
            
            if include_pdfs:
                for row in records_data:
                    doc_path = row[6] # Document Path is index 6
                    if doc_path and not path_traversal:
                        zf.writestr(doc_path, b"%PDF-1.4 Mock PDF Content")
                    elif doc_path and path_traversal:
                        # For path traversal we just don't write the file because the path logic catches it before reading
                        # Or if we want, we can write to some weird path but ZipFile handles it safely in Python. 
                        # The code logic validates the path strings themselves.
                        pass
                        
        zip_buffer.seek(0)
        return base64.b64encode(zip_buffer.read())

    def test_zip_packaging(self):
        # Assert that mocking a migration.zip containing contracts.xlsx and embedded PDFs 
        # successfully extracts and binds the attachments.
        records_data = [
            ['Test Vendor', 'Test Contract 1', 'CN-001', '2023-01-01', '2024-01-01', 1000.0, 'docs/contract1.pdf'],
            ['Test Vendor 2', 'Test Contract 2', 'CN-002', '2023-01-01', '2024-01-01', 2000.0, 'contract2.pdf'],
        ]
        
        zip_base64 = self._create_zip_package(records_data, include_pdfs=True)
        self.batch.upload_file = zip_base64
        self.batch.upload_file_name = 'migration.zip'
        
        self.batch.action_parse_file()
        
        self.assertEqual(self.batch.state, 'parsed')
        self.assertEqual(len(self.batch.record_ids), 2)
        
        rec1 = self.batch.record_ids.filtered(lambda r: r.contract_number == 'CN-001')
        self.assertTrue(rec1)
        self.assertEqual(rec1.document_filename, 'contract1.pdf')
        self.assertTrue(rec1.document_binary)
        self.assertIn(b"Mock PDF Content", base64.b64decode(rec1.document_binary))
        
        rec2 = self.batch.record_ids.filtered(lambda r: r.contract_number == 'CN-002')
        self.assertTrue(rec2)
        self.assertEqual(rec2.document_filename, 'contract2.pdf')

    def test_missing_pdfs(self):
        # Assert missing a referenced PDF immediately transitions the batch row to an error state.
        records_data = [
            ['Test Vendor', 'Test Contract 1', 'CN-001', '2023-01-01', '2024-01-01', 1000.0, 'missing.pdf'],
        ]
        
        # generate zip without the pdfs
        zip_base64 = self._create_zip_package(records_data, include_pdfs=False)
        self.batch.upload_file = zip_base64
        self.batch.upload_file_name = 'migration_missing.zip'
        
        self.batch.action_parse_file()
        
        self.assertEqual(self.batch.state, 'parsed')
        self.assertEqual(len(self.batch.record_ids), 1)
        
        record = self.batch.record_ids[0]
        self.assertEqual(record.state, 'error')
        self.assertIn('not found in zip', record.validation_notes)

    def test_traversal_defense(self):
        # Assert an injected ../../../ document path instantly raises a UserError security fault.
        records_data = [
            ['Hacker Vendor', 'Hack Contract', 'HCK-001', '2023-01-01', '2024-01-01', 1000.0, '../../../etc/passwd.pdf'],
        ]
        
        zip_base64 = self._create_zip_package(records_data, path_traversal=True)
        self.batch.upload_file = zip_base64
        self.batch.upload_file_name = 'migration_hack.zip'
        
        with self.assertRaises(UserError) as err:
            self.batch.action_parse_file()
            
        self.assertIn("Security violation: path traversal detected", str(err.exception))
