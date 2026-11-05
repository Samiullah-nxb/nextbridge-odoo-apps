# Part of NextContract. See LICENSE file for full copyright and licensing details.

from odoo.tests.common import TransactionCase


class TestSchemaBuilder(TransactionCase):

    def _spec(self, **overrides):
        from odoo.addons.clm_ai.services.schema_builder import FieldSpec
        values = {
            'code': 'governing_law',
            'label': 'Governing Law',
            'value_type': 'char',
            'ai_hint': '',
            'options': None,
        }
        values.update(overrides)
        return FieldSpec(**values)

    def test_schema_contains_one_entry_per_spec(self):
        from odoo.addons.clm_ai.services.schema_builder import build_extraction_schema
        schema = build_extraction_schema([
            self._spec(),
            self._spec(code='notice_period', label='Notice Period', value_type='float'),
        ])
        self.assertIn('governing_law', schema.model_fields)
        self.assertIn('notice_period', schema.model_fields)

    def test_selection_is_constrained_to_option_codes(self):
        from odoo.addons.clm_ai.services.schema_builder import build_extraction_schema
        schema = build_extraction_schema([
            self._spec(code='payment_terms', label='Payment Terms',
                       value_type='selection', options=['net_15', 'net_30']),
        ])
        payload = {'payment_terms': {
            'value': 'net_30', 'confidence': 96.0,
            'source_page': 4, 'source_text': 'Payment within 30 days.',
        }}
        parsed = schema.model_validate(payload)
        self.assertEqual(parsed.payment_terms.value, 'net_30')

    def test_selection_rejects_value_outside_vocabulary(self):
        from pydantic import ValidationError
        from odoo.addons.clm_ai.services.schema_builder import build_extraction_schema
        schema = build_extraction_schema([
            self._spec(code='payment_terms', label='Payment Terms',
                       value_type='selection', options=['net_15', 'net_30']),
        ])
        with self.assertRaises(ValidationError):
            schema.model_validate({'payment_terms': {
                'value': 'thirty days from invoice', 'confidence': 96.0,
                'source_page': 4, 'source_text': 'Payment within 30 days.',
            }})

    def test_selection_with_no_options_fails_before_any_call(self):
        """A selection field with an empty vocabulary is a configuration bug.

        Failing here means it never reaches the provider and never silently
        degrades to an unconstrained string.
        """
        from odoo.addons.clm_ai.services.schema_builder import (
            build_extraction_schema, SchemaBuildError,
        )
        with self.assertRaises(SchemaBuildError):
            build_extraction_schema([
                self._spec(code='payment_terms', value_type='selection', options=[]),
            ])

    def test_empty_spec_list_fails(self):
        from odoo.addons.clm_ai.services.schema_builder import (
            build_extraction_schema, SchemaBuildError,
        )
        with self.assertRaises(SchemaBuildError):
            build_extraction_schema([])

    def test_ai_hint_lands_in_field_metadata_not_prompt(self):
        """The hint must be schema metadata, never a prompt instruction."""
        from odoo.addons.clm_ai.services.schema_builder import build_extraction_schema
        hint = 'Notice period for termination for convenience, in days.'
        schema = build_extraction_schema([
            self._spec(code='notice_period', value_type='float', ai_hint=hint),
        ])
        json_schema = schema.model_json_schema()
        self.assertIn(hint, str(json_schema))

    def test_source_text_is_length_capped(self):
        from pydantic import ValidationError
        from odoo.addons.clm_ai.services.schema_builder import (
            build_extraction_schema, SOURCE_TEXT_MAX_CHARS,
        )
        schema = build_extraction_schema([self._spec()])
        with self.assertRaises(ValidationError):
            schema.model_validate({'governing_law': {
                'value': 'England', 'confidence': 99.0, 'source_page': 1,
                'source_text': 'x' * (SOURCE_TEXT_MAX_CHARS + 1),
            }})

    def test_text_layer_field_is_absent_by_default(self):
        """Transcription costs output tokens on every page; never opt in silently."""
        from odoo.addons.clm_ai.services.schema_builder import build_extraction_schema
        schema = build_extraction_schema([self._spec()])
        self.assertNotIn('document_text', schema.model_fields)

    def test_text_layer_field_added_when_requested(self):
        from odoo.addons.clm_ai.services.schema_builder import build_extraction_schema
        schema = build_extraction_schema([self._spec()], include_text_layer=True)
        self.assertIn('document_text', schema.model_fields)

    def test_missing_field_is_allowed(self):
        """Fields the model cannot find are omitted, not invented."""
        from odoo.addons.clm_ai.services.schema_builder import build_extraction_schema
        schema = build_extraction_schema([
            self._spec(),
            self._spec(code='notice_period', value_type='float'),
        ])
        parsed = schema.model_validate({'governing_law': {
            'value': 'England', 'confidence': 99.0,
            'source_page': 1, 'source_text': 'Governed by English law.',
        }})
        self.assertIsNone(parsed.notice_period)

    def test_date_values_are_parsed_as_dates(self):
        import datetime
        from odoo.addons.clm_ai.services.schema_builder import build_extraction_schema
        schema = build_extraction_schema([
            self._spec(code='effective_date', value_type='date'),
        ])
        parsed = schema.model_validate({'effective_date': {
            'value': '2026-01-01', 'confidence': 97.0,
            'source_page': 1, 'source_text': 'effective as of January 1, 2026',
        }})
        self.assertEqual(parsed.effective_date.value, datetime.date(2026, 1, 1))
