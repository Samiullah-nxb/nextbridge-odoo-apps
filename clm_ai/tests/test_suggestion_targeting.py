# Part of NextContract. See LICENSE file for full copyright and licensing details.

from odoo.tests.common import TransactionCase
from odoo.exceptions import ValidationError, UserError


class TestSuggestionTargeting(TransactionCase):

    def setUp(self):
        super().setUp()
        self.type_nda = self.env.ref('clm.contract_type_nda')
        self.type_vendor = self.env['clm.contract.type'].create({
            'name': 'Vendor', 'code': 'vendor_target',
        })
        self.contract = self.env['clm.contract'].create({
            'name': 'Targeting Contract',
            'contract_type_id': self.type_nda.id,
        })
        self.version = self.env['clm.contract.version'].create({
            'contract_id': self.contract.id,
            'version_type': 'internal_draft', 'version_number': 1,
        })
        self.job = self.env['clm.ai.job'].create({
            'contract_version_id': self.version.id,
            'analysis_type': 'field_extraction', 'provider': 'mock',
        })
        self.definition = self.env['clm.contract.field.definition'].create({
            'name': 'Governing Law', 'code': 'gov_law_tgt', 'value_type': 'char',
        })
        self.def_vendor_only = self.env['clm.contract.field.definition'].create({
            'name': 'SLA Target', 'code': 'sla_tgt', 'value_type': 'float',
            'contract_type_ids': [(6, 0, [self.type_vendor.id])],
        })

    def _suggestion(self, **overrides):
        values = {
            'job_id': self.job.id,
            'field_name': 'gov_law_tgt',
            'suggested_value': 'England and Wales',
            'target_type': 'definition',
            'definition_id': self.definition.id,
            'confidence': 97.0,
        }
        values.update(overrides)
        return self.env['clm.ai.field.suggestion'].create(values)

    def test_definition_target_requires_a_definition(self):
        with self.assertRaises(ValidationError):
            self._suggestion(definition_id=False)

    def test_core_target_must_not_carry_a_definition(self):
        with self.assertRaises(ValidationError):
            self._suggestion(target_type='core', field_name='name')

    def test_definition_must_apply_to_the_contract_type(self):
        """AI must not be able to write an unrelated field onto a contract."""
        with self.assertRaises(ValidationError):
            self._suggestion(
                field_name='sla_tgt', definition_id=self.def_vendor_only.id)

    def test_approving_definition_target_creates_a_field_value(self):
        suggestion = self._suggestion()
        suggestion._apply_to_contract()
        value = self.env['clm.contract.field.value'].search([
            ('contract_id', '=', self.contract.id),
            ('definition_id', '=', self.definition.id),
        ])
        self.assertEqual(value.display_value, 'England and Wales')

    def test_approving_definition_target_updates_existing_value(self):
        self.env['clm.contract.field.value'].create({
            'contract_id': self.contract.id,
            'definition_id': self.definition.id,
            'value_char': 'Scotland',
        })
        self._suggestion()._apply_to_contract()
        values = self.env['clm.contract.field.value'].search([
            ('contract_id', '=', self.contract.id),
            ('definition_id', '=', self.definition.id),
        ])
        self.assertEqual(len(values), 1)
        self.assertEqual(values.display_value, 'England and Wales')

    def test_approving_core_target_writes_the_contract_column(self):
        suggestion = self._suggestion(
            target_type='core', field_name='name',
            suggested_value='Renamed By AI', definition_id=False)
        suggestion._apply_to_contract()
        self.assertEqual(self.contract.name, 'Renamed By AI')

    def test_core_target_outside_allowlist_is_refused(self):
        """The H-02 boundary must survive the new targeting mechanism."""
        suggestion = self._suggestion(
            target_type='core', field_name='state',
            suggested_value='active', definition_id=False)
        with self.assertRaises(UserError):
            suggestion._apply_to_contract()

    def test_supersede_marks_pending_suggestions(self):
        suggestion = self._suggestion()
        later_job = self.env['clm.ai.job'].create({
            'contract_version_id': self.version.id,
            'analysis_type': 'field_extraction', 'provider': 'mock',
        })
        self.job.suggestion_ids.supersede(later_job)
        self.assertEqual(suggestion.status, 'superseded')
        self.assertEqual(suggestion.superseded_by_job_id, later_job)

    def test_supersede_leaves_approved_suggestions_alone(self):
        """History is immutable: an applied decision is not un-decided."""
        suggestion = self._suggestion(status='approved')
        later_job = self.env['clm.ai.job'].create({
            'contract_version_id': self.version.id,
            'analysis_type': 'field_extraction', 'provider': 'mock',
        })
        self.job.suggestion_ids.supersede(later_job)
        self.assertEqual(suggestion.status, 'approved')
        self.assertFalse(suggestion.superseded_by_job_id)
