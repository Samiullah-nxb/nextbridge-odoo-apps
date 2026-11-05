# Part of NextContract. See LICENSE file for full copyright and licensing details.

from odoo.tests.common import TransactionCase
from psycopg2 import IntegrityError
from odoo.tools import mute_logger


class TestContractFieldDefinition(TransactionCase):

    def setUp(self):
        super().setUp()
        self.type_nda = self.env.ref('clm.contract_type_nda')
        self.type_other = self.env['clm.contract.type'].create({
            'name': 'Vendor Agreement',
            'code': 'vendor_test',
        })

    def test_definition_with_no_types_applies_to_all(self):
        """Empty contract_type_ids means the definition is global."""
        # Not 'governing_law': that code ships as seed data (data/clm_contract_field_definitions.xml).
        definition = self.env['clm.contract.field.definition'].create({
            'name': 'Jurisdiction (test)',
            'code': 'test_global_field',
            'value_type': 'char',
        })
        self.assertTrue(definition.applies_to_type(self.type_nda))
        self.assertTrue(definition.applies_to_type(self.type_other))

    def test_definition_with_types_applies_only_to_those(self):
        definition = self.env['clm.contract.field.definition'].create({
            'name': 'Notice Period',
            'code': 'notice_period',
            'value_type': 'float',
            'contract_type_ids': [(6, 0, [self.type_nda.id])],
        })
        self.assertTrue(definition.applies_to_type(self.type_nda))
        self.assertFalse(definition.applies_to_type(self.type_other))

    def test_definition_code_is_unique(self):
        self.env['clm.contract.field.definition'].create({
            'name': 'Payment Terms',
            'code': 'payment_terms',
            'value_type': 'selection',
        })
        with self.assertRaises(IntegrityError), mute_logger('odoo.sql_db'):
            self.env['clm.contract.field.definition'].create({
                'name': 'Payment Terms Duplicate',
                'code': 'payment_terms',
                'value_type': 'char',
            })
            self.env.flush_all()

    def test_option_code_is_unique_per_definition(self):
        definition = self.env['clm.contract.field.definition'].create({
            'name': 'Payment Terms',
            'code': 'payment_terms_opt',
            'value_type': 'selection',
        })
        self.env['clm.contract.field.option'].create({
            'definition_id': definition.id, 'name': 'Net 30', 'code': 'net_30',
        })
        with self.assertRaises(IntegrityError), mute_logger('odoo.sql_db'):
            self.env['clm.contract.field.option'].create({
                'definition_id': definition.id, 'name': 'Net Thirty', 'code': 'net_30',
            })
            self.env.flush_all()

    def test_same_option_code_allowed_across_definitions(self):
        """Vocabulary is scoped to its definition, not global."""
        def_a = self.env['clm.contract.field.definition'].create({
            'name': 'Terms A', 'code': 'terms_a', 'value_type': 'selection',
        })
        def_b = self.env['clm.contract.field.definition'].create({
            'name': 'Terms B', 'code': 'terms_b', 'value_type': 'selection',
        })
        self.env['clm.contract.field.option'].create({
            'definition_id': def_a.id, 'name': 'Net 30', 'code': 'net_30',
        })
        option_b = self.env['clm.contract.field.option'].create({
            'definition_id': def_b.id, 'name': 'Net 30', 'code': 'net_30',
        })
        self.assertTrue(option_b.id)
