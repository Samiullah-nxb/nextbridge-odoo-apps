# Part of NextContract. See LICENSE file for full copyright and licensing details.

from odoo.tests.common import TransactionCase
import base64

class TestMigrationDeduplication(TransactionCase):

    def setUp(self):
        super().setUp()
        self.contract_type_nda = self.env.ref('clm.contract_type_nda')
        self.partner = self.env['res.partner'].create({'name': 'Legacy Vendor'})
        # Create an existing live contract to test against
        self.existing_contract = self.env['clm.contract'].with_context(migration_mode=True).create({
            'name': 'Legacy Supply Agreement 2021',
            'contract_type_id': self.contract_type_nda.id,
            'partner_id': self.partner.id,
            'effective_date': '2021-01-01',
            'contract_value': 50000.0,
            'state': 'active'
        })
        
        self.batch = self.env['clm.migration.batch'].create({
            'name': 'Test Batch'
        })

    def test_deduplication_high_confidence(self):
        # Name match exactly + Vendor match
        record = self.env['clm.migration.record'].create({
            'batch_id': self.batch.id,
            'vendor_name': 'Legacy Vendor',
            'contract_title': 'Legacy Supply Agreement 2021',
            'contract_number': 'Legacy Supply Agreement 2021',
            'contract_type_id': self.contract_type_nda.id, # Acting as title/number
            'effective_date': '2021-01-01',
        })
        record.action_validate()
        self.assertEqual(record.state, 'high_confidence_duplicate')

    def test_weighted_duplicates(self):
        # Assert similar titles and identical partner matches correctly trigger potential_duplicate
        # Score calculation: partner match (30) + similar title match (30) = 60
        # 60 >= 45 -> potential
        record = self.env['clm.migration.record'].create({
            'batch_id': self.batch.id,
            'vendor_name': 'Legacy Vendor',
            'contract_title': 'Supply Agreement', # Similar title (substring of Legacy Supply Agreement 2021)
            'effective_date': '2023-01-01', # different date
            'contract_value': 10000.0 # different value
        })
        record.action_validate()
        self.assertEqual(record.state, 'potential_duplicate')

    def test_force_import(self):
        record = self.env['clm.migration.record'].create({
            'batch_id': self.batch.id,
            'vendor_name': 'Legacy Vendor',
            'contract_title': 'Legacy Supply Agreement 2021',
            'contract_number': 'Legacy Supply Agreement 2021',
            'contract_type_id': self.contract_type_nda.id,
        })
        record.action_validate()
        self.assertEqual(record.state, 'high_confidence_duplicate')
        
        # Bypassing duplicate via Force Import correctly creates an immutable audit trail record
        record.state = 'force_imported'
        record.force_import_reason = "It's an amendment"
        record._import_record()
        
        self.assertEqual(record.state, 'imported')
        self.assertTrue(record.imported_contract_id)
        self.assertEqual(record.imported_contract_id.migration_record_id.force_import_reason, "It's an amendment", "Audit trail reason must be persisted.")
        self.assertEqual(record.imported_contract_id.source, 'migration', "Source must be tracked for audit.")
