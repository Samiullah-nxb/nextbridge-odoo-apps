# Part of NextContract. See LICENSE file for full copyright and licensing details.

from odoo.tests.common import TransactionCase
from odoo.exceptions import UserError
import zipfile
import io
import base64
import struct

class TestMigrationZipSecurity(TransactionCase):

    def setUp(self):
        super().setUp()
        self.batch = self.env['clm.migration.batch'].create({
            'name': 'Security Test Batch'
        })

    def test_malicious_zip_path_traversal(self):
        # Build a malicious ZIP file in memory containing a path traversal member
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, 'w') as zf:
            # We must construct ZipInfo manually because zipfile.write() sanitizes paths
            evil_info = zipfile.ZipInfo('../evil.txt')
            zf.writestr(evil_info, b'malicious payload')
            
            # Also add a valid csv so it looks like a package
            zf.writestr('contracts.csv', b'Vendor Name\nTest')
            
        zip_data = base64.b64encode(zip_buffer.getvalue())
        
        self.batch.upload_file = zip_data
        self.batch.upload_file_name = 'migration.zip'
        
        # Action should raise UserError due to illegal path
        with self.assertRaisesRegex(UserError, "Security violation: Illegal path"):
            self.batch.action_parse_file()
            
    def test_malicious_zip_absolute_path(self):
        # Build a malicious ZIP file with an absolute path
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, 'w') as zf:
            evil_info = zipfile.ZipInfo('/etc/passwd')
            zf.writestr(evil_info, b'malicious payload')
            
        zip_data = base64.b64encode(zip_buffer.getvalue())
        
        self.batch.upload_file = zip_data
        self.batch.upload_file_name = 'migration.zip'
        
        with self.assertRaisesRegex(UserError, "Security violation: Illegal path"):
            self.batch.action_parse_file()

    def test_zip_total_uncompressed_size_limit(self):
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, 'w', compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr('huge_file.txt', b'small data')

        # zipfile resets file_size while writing, so declare 2 GB the way a real
        # zip bomb does: in the central directory header (uncompressed size is
        # 24 bytes after the 'PK' signature).
        raw = bytearray(zip_buffer.getvalue())
        header = raw.index(b'PK')
        struct.pack_into('<I', raw, header + 24, 2 * 1024 * 1024 * 1024)

        self.batch.upload_file = base64.b64encode(bytes(raw))
        self.batch.upload_file_name = 'migration.zip'

        with self.assertRaisesRegex(UserError, "Security violation: ZIP archive uncompressed size exceeds 1GB limit"):
            self.batch.action_parse_file()
