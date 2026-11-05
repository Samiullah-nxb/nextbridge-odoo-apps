# Part of NextContract. See LICENSE file for full copyright and licensing details.

from odoo import models, fields, api
import base64
import csv
import io
import logging
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

class ClmMigrationBatch(models.Model):
    _name = 'clm.migration.batch'
    _description = 'Contract Migration Batch'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    
    name = fields.Char('Batch Name', required=True, default='New Migration Batch')
    state = fields.Selection([
        ('draft', 'Draft'),
        ('parsed', 'Parsed'),
        ('validated', 'Validated'),
        ('partially_imported', 'Partially Imported'),
        ('imported', 'Imported'),
        ('failed', 'Failed')
    ], default='draft', tracking=True)
    
    upload_file = fields.Binary('Migration Package (ZIP)', attachment=True)
    upload_file_name = fields.Char('File Name')
    
    record_ids = fields.One2many('clm.migration.record', 'batch_id', string='Migration Records')
    
    record_count = fields.Integer(compute='_compute_record_count')
    
    @api.depends('record_ids')
    def _compute_record_count(self):
        for batch in self:
            batch.record_count = len(batch.record_ids)
            
    def action_parse_file(self):
        self.ensure_one()
        if not self.upload_file:
            raise UserError("Please upload a file.")
            
        file_name = (self.upload_file_name or '').lower()
        if not file_name.endswith('.zip'):
            raise UserError("Only .zip files are supported.")
            
        # Clear existing non-imported records
        self.record_ids.filtered(lambda r: r.state != 'imported').unlink()
        
        import tempfile
        import zipfile
        import os
        import shutil
        
        temp_dir = tempfile.mkdtemp()
        try:
            # Check maximum archive compressed size (e.g. 200MB)
            if len(self.upload_file) > 200 * 1024 * 1024:
                raise UserError("Security violation: ZIP archive compressed size exceeds 200MB limit.")
                
            zip_data = base64.b64decode(self.upload_file)
            
            total_size = 0
            file_count = 0
            
            with zipfile.ZipFile(io.BytesIO(zip_data)) as zf:
                for member in zf.infolist():
                    file_count += 1
                    if file_count > 2000:
                        raise UserError("Security violation: ZIP archive contains too many files (max 2,000).")
                        
                    total_size += member.file_size
                    if total_size > 1 * 1024 * 1024 * 1024:
                        raise UserError("Security violation: ZIP archive uncompressed size exceeds 1GB limit.")
                        
                    # Compression Ratio Protection
                    if member.compress_size > 0:
                        ratio = member.file_size / member.compress_size
                        if ratio > 100:
                            raise UserError(f"Security violation: ZIP archive member '{member.filename}' has a highly suspicious compression ratio ({ratio:.1f}:1).")
                            
                    # Reject Symlinks
                    if (member.external_attr >> 16) & 0o120000 == 0o120000:
                        raise UserError(f"Security violation: Symlinks are not permitted in ZIP archives '{member.filename}'.")
                        
                    # Security validation before extraction
                    if member.filename.startswith('/') or member.filename.startswith('\\\\') or '..' in member.filename:
                        raise UserError(f"Security violation: Illegal path in ZIP member '{member.filename}'")
                    
                    if member.file_size > 50 * 1024 * 1024: # 50MB zip bomb limit
                        raise UserError(f"Security violation: File '{member.filename}' exceeds 50MB size limit.")
                        
                    member_path = os.path.normpath(os.path.join(temp_dir, member.filename))
                    if os.path.commonpath([temp_dir, member_path]) != temp_dir:
                        raise UserError(f"Security violation: path traversal detected in ZIP member '{member.filename}'")
                        
                    zf.extract(member, temp_dir)
                
            csv_path = os.path.join(temp_dir, 'contracts.csv')
            xlsx_path = os.path.join(temp_dir, 'contracts.xlsx')
            
            records = []
            
            if os.path.exists(csv_path):
                with open(csv_path, 'r', encoding='utf-8') as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        rec_dict = {
                            'batch_id': self.id,
                            'vendor_name': row.get('Vendor Name', ''),
                            'contract_title': row.get('Contract Title', ''),
                            'contract_number': row.get('Contract Number', ''),
                            'effective_date': row.get('Effective Date') or False,
                            'expiry_date': row.get('Expiry Date') or False,
                            'execution_date': row.get('Execution Date') or False,
                            'legacy_source_reference': row.get('Legacy Source Reference', ''),
                            'contract_value': float(row.get('Contract Value', 0.0) or 0.0),
                            'state': 'pending'
                        }
                        
                        doc_path = str(row.get('Document Path') or '')
                        if doc_path:
                            if not doc_path.lower().endswith('.pdf'):
                                rec_dict['state'] = 'error'
                                rec_dict['validation_notes'] = f"Document '{doc_path}' is invalid. Only .pdf legacy documents are permitted."
                            else:
                                full_path = os.path.normpath(os.path.join(temp_dir, doc_path))
                                if os.path.commonpath([temp_dir, full_path]) != temp_dir:
                                    raise UserError(f"Security violation: path traversal detected in {doc_path}")
                                if os.path.exists(full_path):
                                    with open(full_path, 'rb') as df:
                                        rec_dict['document_binary'] = base64.b64encode(df.read())
                                        rec_dict['document_filename'] = os.path.basename(full_path)
                                else:
                                    rec_dict['state'] = 'error'
                                    rec_dict['validation_notes'] = f"Document {doc_path} not found in zip."
                                
                        records.append(rec_dict)
            elif os.path.exists(xlsx_path):
                try:
                    import openpyxl
                except ImportError:
                    raise UserError("The 'openpyxl' Python library is required to parse .xlsx files. Please install it.")
                    
                wb = openpyxl.load_workbook(xlsx_path, data_only=True)
                sheet = wb.active
                headers = [str(cell.value) for cell in sheet[1]]
                
                for row_idx, row in enumerate(sheet.iter_rows(values_only=True), start=1):
                    if row_idx == 1:
                        continue # skip header
                    row_dict = dict(zip(headers, row))
                    
                    if not any(row_dict.values()):
                        continue
                        
                    rec_dict = {
                        'batch_id': self.id,
                        'vendor_name': str(row_dict.get('Vendor Name') or ''),
                        'contract_title': str(row_dict.get('Contract Title') or ''),
                        'contract_number': str(row_dict.get('Contract Number') or ''),
                        'effective_date': row_dict.get('Effective Date') or False,
                        'expiry_date': row_dict.get('Expiry Date') or False,
                        'execution_date': row_dict.get('Execution Date') or False,
                        'legacy_source_reference': str(row_dict.get('Legacy Source Reference') or ''),
                        'contract_value': float(row_dict.get('Contract Value', 0.0) or 0.0),
                        'state': 'pending'
                    }
                    
                    doc_path = str(row_dict.get('Document Path') or '')
                    if doc_path:
                        if not doc_path.lower().endswith('.pdf'):
                            rec_dict['state'] = 'error'
                            rec_dict['validation_notes'] = f"Document '{doc_path}' is invalid. Only .pdf legacy documents are permitted."
                        else:
                            full_path = os.path.normpath(os.path.join(temp_dir, doc_path))
                            if os.path.commonpath([temp_dir, full_path]) != temp_dir:
                                raise UserError(f"Security violation: path traversal detected in {doc_path}")
                            if os.path.exists(full_path):
                                with open(full_path, 'rb') as df:
                                    rec_dict['document_binary'] = base64.b64encode(df.read())
                                    rec_dict['document_filename'] = os.path.basename(full_path)
                            else:
                                rec_dict['state'] = 'error'
                                rec_dict['validation_notes'] = f"Document {doc_path} not found in zip."
                            
                    records.append(rec_dict)
            else:
                raise UserError("Could not find contracts.csv or contracts.xlsx in the zip package.")
                
            if records:
                self.env['clm.migration.record'].create(records)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)
            
        self.state = 'parsed'
        
    def action_validate(self):
        self.ensure_one()
        # Triggers deduplication and validation logic on all records
        self.record_ids.action_validate()
        self.state = 'validated'
        
    def action_import(self):
        self.ensure_one()
        # Process valid/force imported records
        valid_records = self.record_ids.filtered(lambda r: r.state in ['valid', 'force_imported'])
        for record in valid_records:
            record._import_record()
            
        if any(r.state == 'error' for r in self.record_ids):
            self.state = 'partially_imported'
        else:
            self.state = 'imported'
