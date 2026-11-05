# Part of NextContract. See LICENSE file for full copyright and licensing details.

from odoo import models, fields, api
from odoo.exceptions import UserError
import logging

_logger = logging.getLogger(__name__)

class ClmMigrationRecord(models.Model):
    _name = 'clm.migration.record'
    _description = 'Contract Migration Record'

    batch_id = fields.Many2one('clm.migration.batch', required=True, ondelete='cascade')
    
    # Raw Data
    vendor_name = fields.Char('Vendor Name')
    contract_title = fields.Char('Contract Title')
    contract_number = fields.Char('Contract Number')
    effective_date = fields.Date('Effective Date')
    expiry_date = fields.Date('Expiry Date')
    execution_date = fields.Date('Execution Date')
    contract_value = fields.Float('Contract Value')
    legacy_source_reference = fields.Char('Legacy Source Reference')
    document_path = fields.Char('Document Path', help="Relative path to the legacy PDF in the migration package")
    document_binary = fields.Binary('Document Binary', attachment=True)
    document_filename = fields.Char('Document Filename')
    
    # Mapped Data
    partner_id = fields.Many2one('res.partner', 'Matched Partner')
    contract_type_id = fields.Many2one('clm.contract.type', 'Contract Type')
    imported_contract_id = fields.Many2one('clm.contract', 'Imported Contract', readonly=True)
    
    state = fields.Selection([
        ('pending', 'Pending Validation'),
        ('valid', 'Valid'),
        ('potential_duplicate', 'Potential Duplicate'),
        ('high_confidence_duplicate', 'High Confidence Duplicate'),
        ('force_imported', 'Force Imported'),
        ('imported', 'Imported'),
        ('error', 'Error')
    ], default='pending')
    
    validation_notes = fields.Text('Validation Notes')
    force_import_reason = fields.Char('Force Import Reason')
    
    def action_validate(self):
        for record in self:
            notes = []
            state = 'valid'
            
            # Explicitly validate document presence
            if record.document_path and not record.document_binary:
                state = 'error'
                notes.append(f"Document '{record.document_path}' is missing from the migration package.")
            
            # Find partner
            partner = self.env['res.partner'].search([('name', '=ilike', record.vendor_name)], limit=1)
            if partner:
                record.partner_id = partner.id
            else:
                state = 'error'
                notes.append(f"Partner '{record.vendor_name}' not found. Please map manually or create it.")
                
            # Deduplication Engine
            duplicate_status, duplicate_evidence = record._check_duplicates()
            if state != 'error':
                if duplicate_status == 'high':
                    state = 'high_confidence_duplicate'
                    notes.append(f"High confidence duplicate. Evidence: {', '.join(duplicate_evidence)}")
                elif duplicate_status == 'potential':
                    state = 'potential_duplicate'
                    notes.append(f"Potential duplicate. Evidence: {', '.join(duplicate_evidence)}")
                
            record.state = state
            record.validation_notes = "\n".join(notes)
            
    def _check_duplicates(self):
        """
        Check for duplicates against live contracts using a weighted multi-factor model.
        Returns: (status, evidence_list)
        status: 'high', 'potential', or False
        """
        self.ensure_one()
        
        # We need a live contract to compare against. To optimize, we pre-filter candidates by partner or name.
        if self.partner_id:
            candidates = self.env['clm.contract'].search([('partner_id', '=', self.partner_id.id)])
        elif self.contract_title:
            candidates = self.env['clm.contract'].search([('name', '=ilike', self.contract_title)])
        else:
            return False, []
            
        highest_score = 0
        best_evidence = []
        
        for cand in candidates:
            cand_score = 0
            cand_evidence = []
            
            if self.partner_id and cand.partner_id == self.partner_id:
                cand_score += 30
                cand_evidence.append(f"Partner match ({cand.partner_id.name})")
                
            if self.contract_title and cand.name and self.contract_title.lower() in cand.name.lower():
                cand_score += 30
                cand_evidence.append(f"Title similarity ('{cand.name}')")
                
            if self.contract_number and cand.name and self.contract_number.lower() in cand.name.lower():
                cand_score += 40
                cand_evidence.append(f"Number match ('{self.contract_number}')")
                
            if self.effective_date and cand.effective_date == self.effective_date:
                cand_score += 20
                cand_evidence.append(f"Effective Date match ({self.effective_date})")
                
            if self.contract_value and cand.contract_value == self.contract_value:
                cand_score += 15
                cand_evidence.append(f"Value match ({self.contract_value})")
                
            if cand_score > highest_score:
                highest_score = cand_score
                best_evidence = [f"Matched Contract [{cand.id}]"] + cand_evidence
                
        if highest_score >= 70:
            return 'high', best_evidence
        elif highest_score >= 45:
            return 'potential', best_evidence
            
        return False, []
        
    def action_force_import(self):
        # Called from a wizard or directly
        return {
            'name': 'Force Import Reason',
            'type': 'ir.actions.act_window',
            'res_model': 'clm.migration.force.import.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_record_id': self.id}
        }
        
    def _import_record(self):
        self.ensure_one()
        if self.state not in ['valid', 'force_imported']:
            return
            
        try:
            partner = self.partner_id
                
            contract = self.env['clm.contract'].sudo().with_context(migration_mode=True).create({
                'name': self.contract_title or self.contract_number or 'Migrated Contract',
                'partner_id': partner.id if partner else False,
                'contract_type_id': self.contract_type_id.id if self.contract_type_id else self.env['clm.contract.type'].search([], limit=1).id,
                'effective_date': self.effective_date,
                'expiry_date': self.expiry_date,
                'contract_value': self.contract_value,
                'state': 'active', # Bypass workflow
                'source': 'migration',
                'is_legacy_contract': True,
                'migration_batch_id': self.batch_id.id,
                'migration_record_id': self.id,
                'signature_status': 'signed',
                'execution_date': self.execution_date or self.effective_date,
                'legacy_execution_date': self.execution_date,
                'legacy_source_reference': self.legacy_source_reference,
                'legacy_contract_number': self.contract_number,
            })
            
            # Create the executed version
            version = self.env['clm.contract.version'].sudo().create({
                'contract_id': contract.id,
                'version_type': 'executed',
                'execution_method': 'legacy_migration',
                'status': 'active',
            })
            
            # Handle document mock link
            if self.document_binary:
                try:
                    attachment = self.env['ir.attachment'].sudo().create({
                        'name': self.document_filename or 'imported_document',
                        'type': 'binary',
                        'datas': self.document_binary,
                        'res_model': 'clm.contract',
                        'res_id': contract.id,
                    })
                    document = self.env['documents.document'].sudo().create({
                        'name': attachment.name,
                        'type': 'binary',
                        'attachment_id': attachment.id,
                        'res_model': 'clm.contract',
                        'res_id': contract.id,
                    })
                    version.document_id = document.id
                except Exception as e:
                    raise UserError(f"Could not ingest actual PDF: {e}")
                
            self.imported_contract_id = contract.id
            self.state = 'imported'
        except Exception as e:
            self.state = 'error'
            self.validation_notes = f"Import Error: {str(e)}"
