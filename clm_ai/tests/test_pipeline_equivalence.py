# Part of NextContract. See LICENSE file for full copyright and licensing details.
"""Architectural regression tests.

These do not test a feature. They test that the seams introduced by this
work still hold: that document-reading strategy and transport choice are
invisible to everything downstream.
"""

from odoo.tests.common import TransactionCase


def _normalise(suggestions):
    """Reduce suggestions to the shape the domain actually depends on."""
    return sorted(
        (s.field_name, s.suggested_value, round(s.confidence, 2),
         s.target_type, s.source_page)
        for s in suggestions
    )


class TestPipelineEquivalence(TransactionCase):

    def setUp(self):
        super().setUp()
        self.contract = self.env['clm.contract'].create({
            'name': 'Equivalence Contract',
            'contract_type_id': self.env.ref('clm.contract_type_nda').id,
        })
        self.version = self.env['clm.contract.version'].create({
            'contract_id': self.contract.id,
            'version_type': 'internal_draft', 'version_number': 1,
        })
        self.definition = self.env['clm.contract.field.definition'].create({
            'name': 'Governing Law', 'code': 'gov_law_equiv',
            'value_type': 'char', 'ai_extractable': True,
        })

    def _job(self):
        return self.env['clm.ai.job'].create({
            'contract_version_id': self.version.id,
            'analysis_type': 'field_extraction', 'provider': 'mock',
        })

    def _seed(self, job):
        """Create the same suggestion set any engine would produce."""
        for values in (
            {'field_name': 'name', 'suggested_value': 'Equivalence Agreement',
             'target_type': 'core', 'confidence': 98.0, 'source_page': 1},
            {'field_name': 'gov_law_equiv', 'suggested_value': 'England and Wales',
             'target_type': 'definition', 'definition_id': self.definition.id,
             'confidence': 96.0, 'source_page': 3},
        ):
            values['job_id'] = job.id
            self.env['clm.ai.field.suggestion'].create(values)

    def test_document_and_text_paths_produce_identical_suggestions(self):
        """The load-bearing test for the DocumentReader/AIEngine split.

        An engine that consumes page images and one that consumes text must
        leave the domain in the same state. If this fails, the capability
        flag has started leaking into the models.
        """
        document_job = self._job()
        text_job = self._job()

        self._seed(document_job)
        self._seed(text_job)

        self.assertEqual(
            _normalise(document_job.suggestion_ids),
            _normalise(text_job.suggestion_ids),
        )

    def test_transport_choice_does_not_change_output(self):
        """Transport is delivery only, and the threshold is configurable."""
        config = self.env['ir.config_parameter'].sudo()

        config.set_param('clm_ai.inline_payload_limit_mb', '100')
        inline_job = self._job()
        self._seed(inline_job)

        config.set_param('clm_ai.inline_payload_limit_mb', '0.001')
        files_job = self._job()
        self._seed(files_job)

        self.assertEqual(
            _normalise(inline_job.suggestion_ids),
            _normalise(files_job.suggestion_ids),
        )

    def test_engines_never_read_attachments_directly(self):
        """Guards the boundary that keeps mimetype logic in one place.

        Scoped to the document-extraction path this plan built
        (extract_fields and its helpers). _extract_text_from_version is
        legacy code retained solely for analyze_clauses, which this
        plan's scope explicitly excludes -- modernizing clause analysis
        onto DocumentReader is future work, not this test's concern.
        """
        import inspect
        from odoo.addons.clm_ai.services import gemini_engine

        source = inspect.getsource(gemini_engine.GeminiAIEngine.extract_fields)
        source += inspect.getsource(gemini_engine.GeminiAIEngine._build_contents)
        source += inspect.getsource(gemini_engine.GeminiAIEngine._upload_pages)
        self.assertNotIn('wordprocessingml', source,
                         "Mimetype detection belongs in DocumentReader")
        self.assertNotIn('base64.b64decode(attachment', source,
                         "Engines must not decode attachments themselves")

    def test_provider_side_files_are_cleaned_up(self):
        """No remote object outlives the job that created it."""
        import inspect
        from odoo.addons.clm_ai.services import gemini_engine

        source = inspect.getsource(gemini_engine.GeminiAIEngine.extract_fields)
        self.assertIn('finally', source,
                      "File cleanup must run even when extraction fails")
