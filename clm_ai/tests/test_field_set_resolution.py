# Part of NextContract. See LICENSE file for full copyright and licensing details.

from odoo.tests.common import TransactionCase


class TestFieldSetResolution(TransactionCase):

    def setUp(self):
        super().setUp()
        self.type_nda = self.env.ref('clm.contract_type_nda')
        self.type_vendor = self.env['clm.contract.type'].create({
            'name': 'Vendor', 'code': 'vendor_resolve',
        })
        self.contract = self.env['clm.contract'].create({
            'name': 'Resolution Contract',
            'contract_type_id': self.type_nda.id,
        })
        self.version = self.env['clm.contract.version'].create({
            'contract_id': self.contract.id,
            'version_type': 'internal_draft',
            'version_number': 1,
        })
        self.def_global = self.env['clm.contract.field.definition'].create({
            'name': 'Governing Law', 'code': 'gov_law_res',
            'value_type': 'char', 'ai_extractable': True,
            'ai_hint': 'The law governing this agreement.',
        })
        self.def_nda_only = self.env['clm.contract.field.definition'].create({
            'name': 'Confidentiality Period', 'code': 'conf_period_res',
            'value_type': 'float', 'ai_extractable': True,
            'contract_type_ids': [(6, 0, [self.type_nda.id])],
        })
        self.def_vendor_only = self.env['clm.contract.field.definition'].create({
            'name': 'SLA Target', 'code': 'sla_res',
            'value_type': 'float', 'ai_extractable': True,
            'contract_type_ids': [(6, 0, [self.type_vendor.id])],
        })
        self.def_not_extractable = self.env['clm.contract.field.definition'].create({
            'name': 'Internal Note', 'code': 'note_res',
            'value_type': 'text', 'ai_extractable': False,
        })
        self.job = self.env['clm.ai.job'].create({
            'contract_version_id': self.version.id,
            'analysis_type': 'field_extraction',
            'provider': 'mock',
        })

    def _codes(self):
        return {spec.code for spec in self.job._resolve_field_specs()}

    def test_includes_global_and_matching_type_definitions(self):
        codes = self._codes()
        self.assertIn('gov_law_res', codes)
        self.assertIn('conf_period_res', codes)

    def test_excludes_definitions_for_other_contract_types(self):
        self.assertNotIn('sla_res', self._codes())

    def test_excludes_non_extractable_definitions(self):
        self.assertNotIn('note_res', self._codes())

    def test_includes_core_allowlist_fields(self):
        codes = self._codes()
        self.assertIn('name', codes)
        self.assertIn('effective_date', codes)

    def test_excludes_archived_definitions(self):
        self.def_global.active = False
        self.assertNotIn('gov_law_res', self._codes())

    def test_snapshot_captures_configuration_at_run_time(self):
        specs = self.job._resolve_field_specs()
        self.job._snapshot_field_specs(specs)
        snapshot = self.job.field_snapshot_ids.filtered(
            lambda s: s.definition_code == 'gov_law_res')
        self.assertEqual(snapshot.ai_hint, 'The law governing this agreement.')

    def test_snapshot_survives_later_definition_edits(self):
        """Reproducibility: an edited hint must not rewrite history."""
        specs = self.job._resolve_field_specs()
        self.job._snapshot_field_specs(specs)

        self.def_global.ai_hint = 'COMPLETELY DIFFERENT INSTRUCTION'

        snapshot = self.job.field_snapshot_ids.filtered(
            lambda s: s.definition_code == 'gov_law_res')
        self.assertEqual(snapshot.ai_hint, 'The law governing this agreement.')

    def test_snapshot_records_selection_vocabulary(self):
        definition = self.env['clm.contract.field.definition'].create({
            'name': 'Payment Terms', 'code': 'pay_res',
            'value_type': 'selection', 'ai_extractable': True,
        })
        self.env['clm.contract.field.option'].create({
            'definition_id': definition.id, 'name': 'Net 30', 'code': 'net_30',
        })
        specs = self.job._resolve_field_specs()
        self.job._snapshot_field_specs(specs)
        snapshot = self.job.field_snapshot_ids.filtered(
            lambda s: s.definition_code == 'pay_res')
        self.assertIn('net_30', snapshot.option_codes)
