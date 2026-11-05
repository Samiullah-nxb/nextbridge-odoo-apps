# Part of NextContract. See LICENSE file for full copyright and licensing details.

from odoo.tests.common import TransactionCase
from odoo.exceptions import ValidationError


class TestContractFieldValue(TransactionCase):

    def setUp(self):
        super().setUp()
        self.type_nda = self.env.ref('clm.contract_type_nda')
        self.type_vendor = self.env['clm.contract.type'].create({
            'name': 'Vendor', 'code': 'vendor_val_test',
        })
        self.contract = self.env['clm.contract'].create({
            'name': 'Value Test Contract',
            'contract_type_id': self.type_nda.id,
        })
        self.def_char = self.env['clm.contract.field.definition'].create({
            'name': 'Governing Law', 'code': 'gov_law_val', 'value_type': 'char',
        })
        self.def_date = self.env['clm.contract.field.definition'].create({
            'name': 'Renewal Date', 'code': 'renewal_val', 'value_type': 'date',
        })
        self.def_sel = self.env['clm.contract.field.definition'].create({
            'name': 'Payment Terms', 'code': 'pay_terms_val', 'value_type': 'selection',
        })
        self.opt_net30 = self.env['clm.contract.field.option'].create({
            'definition_id': self.def_sel.id, 'name': 'Net 30', 'code': 'net_30',
        })
        self.def_vendor_only = self.env['clm.contract.field.definition'].create({
            'name': 'SLA Target', 'code': 'sla_val', 'value_type': 'float',
            'contract_type_ids': [(6, 0, [self.type_vendor.id])],
        })

    def test_stores_value_in_typed_column(self):
        value = self.env['clm.contract.field.value'].create({
            'contract_id': self.contract.id,
            'definition_id': self.def_char.id,
            'value_char': 'England and Wales',
        })
        self.assertEqual(value.display_value, 'England and Wales')

    def test_rejects_column_mismatched_with_value_type(self):
        """A date definition cannot carry a float value."""
        with self.assertRaises(ValidationError):
            self.env['clm.contract.field.value'].create({
                'contract_id': self.contract.id,
                'definition_id': self.def_date.id,
                'value_float': 90.0,
            })

    def test_rejects_multiple_populated_columns(self):
        with self.assertRaises(ValidationError):
            self.env['clm.contract.field.value'].create({
                'contract_id': self.contract.id,
                'definition_id': self.def_char.id,
                'value_char': 'England and Wales',
                'value_date': '2026-01-01',
            })

    def test_rejects_option_from_another_definition(self):
        other_def = self.env['clm.contract.field.definition'].create({
            'name': 'Other Sel', 'code': 'other_sel_val', 'value_type': 'selection',
        })
        foreign_option = self.env['clm.contract.field.option'].create({
            'definition_id': other_def.id, 'name': 'Foreign', 'code': 'foreign',
        })
        with self.assertRaises(ValidationError):
            self.env['clm.contract.field.value'].create({
                'contract_id': self.contract.id,
                'definition_id': self.def_sel.id,
                'value_option_id': foreign_option.id,
            })

    def test_rejects_definition_not_applicable_to_contract_type(self):
        """The contract is an NDA; this definition is vendor-only."""
        with self.assertRaises(ValidationError):
            self.env['clm.contract.field.value'].create({
                'contract_id': self.contract.id,
                'definition_id': self.def_vendor_only.id,
                'value_float': 99.9,
            })

    def test_rejects_duplicate_definition_on_same_contract(self):
        self.env['clm.contract.field.value'].create({
            'contract_id': self.contract.id,
            'definition_id': self.def_char.id,
            'value_char': 'England and Wales',
        })
        with self.assertRaises(Exception):
            self.env['clm.contract.field.value'].create({
                'contract_id': self.contract.id,
                'definition_id': self.def_char.id,
                'value_char': 'Scotland',
            })
            self.env.flush_all()

    def test_constraints_apply_under_sudo(self):
        """Integrity is a model rule, not a UI rule -- sudo must not bypass it."""
        with self.assertRaises(ValidationError):
            self.env['clm.contract.field.value'].sudo().create({
                'contract_id': self.contract.id,
                'definition_id': self.def_date.id,
                'value_float': 12.0,
            })

    def test_set_typed_value_writes_correct_column(self):
        value = self.env['clm.contract.field.value'].create({
            'contract_id': self.contract.id,
            'definition_id': self.def_sel.id,
        })
        value.set_typed_value('net_30')
        self.assertEqual(value.value_option_id, self.opt_net30)
        self.assertEqual(value.display_value, 'Net 30')

    def test_boolean_false_string_does_not_become_true(self):
        """A suggested_value string 'False' must set value_bool to False,
        not True (bool('False') is True in plain Python)."""
        definition = self.env['clm.contract.field.definition'].create({
            'name': 'Auto Renews', 'code': 'auto_renews_bool_test',
            'value_type': 'boolean',
        })
        value = self.env['clm.contract.field.value'].create({
            'contract_id': self.contract.id,
            'definition_id': definition.id,
        })
        value.set_typed_value('False')
        self.assertFalse(value.value_bool)

        value.set_typed_value('True')
        self.assertTrue(value.value_bool)
