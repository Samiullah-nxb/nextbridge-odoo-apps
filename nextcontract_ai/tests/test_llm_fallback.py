# Part of NextContract. See LICENSE file for full copyright and licensing details.

import json
import unittest.mock as mock

from odoo.exceptions import UserError, ValidationError
from odoo.tests.common import TransactionCase

from .test_llm_providers import (
    SECRET, _NAME_FIELD, _openai_reply, _payload, _response,
)


class TestModelFallback(TransactionCase):
    """An overloaded model falls back once; nothing else does."""

    def setUp(self):
        super().setUp()
        self.company = self.env.company
        self.company.acknowledge_cloud_ai_processing()
        self.provider = self.env['nextcontract.ai.provider'].create({
            'name': 'OpenAI FB', 'provider_type': 'openai', 'api_key': SECRET,
            'model_ids': [(0, 0, {'name': 'primary'}), (0, 0, {'name': 'backup'})]})
        self.primary, self.backup = self.provider.model_ids
        self.provider.write({
            'default_model_id': self.primary.id,
            'fallback_model_id': self.backup.id})

    def _engine(self):
        from odoo.addons.nextcontract_ai.services.engine_factory import build_engine
        return build_engine(self.env, 'openai', self.company,
                            provider_record=self.provider)

    def _schema(self):
        from odoo.addons.nextcontract_ai.services.schema_builder import (
            build_extraction_schema, FieldSpec)
        return build_extraction_schema([
            FieldSpec(code='name', label='Name', value_type='char', is_core=True)])

    @staticmethod
    def _by_model(mapping):
        """requests.post stub answering according to the requested model."""
        calls = []

        def post(url, headers=None, json=None, timeout=None):
            calls.append(json['model'])
            return mapping[json['model']]
        return post, calls

    def test_overloaded_primary_falls_back_once_and_records_the_fallback(self):
        busy = _response(503, {'error': {'message': 'overloaded'}})
        ok = _openai_reply(json.dumps(_NAME_FIELD))
        post, calls = self._by_model({'primary': busy, 'backup': ok})
        engine = self._engine()
        with mock.patch('requests.post', post), mock.patch('time.sleep'):
            response = engine._request('hi', self._schema())
        # three tries on the primary, then one on the fallback.
        self.assertEqual(calls, ['primary'] * 3 + ['backup'])
        self.assertIsNotNone(response.parsed)
        self.assertEqual(engine.model_name, 'backup')

    def test_later_calls_in_the_job_stay_on_the_fallback(self):
        busy = _response(503, {'error': {'message': 'overloaded'}})
        ok = _openai_reply(json.dumps(_NAME_FIELD))
        post, calls = self._by_model({'primary': busy, 'backup': ok})
        engine = self._engine()
        with mock.patch('requests.post', post), mock.patch('time.sleep'):
            engine._request('first', self._schema())
            calls.clear()
            engine._request('second', self._schema())
        self.assertEqual(calls, ['backup'])

    def test_both_overloaded_raises_the_primary_error_and_restores_it(self):
        from odoo.addons.nextcontract_ai.services.llm_engine import ProviderOverloaded
        busy = _response(503, {'error': {'message': 'overloaded'}})
        post, calls = self._by_model({'primary': busy, 'backup': busy})
        engine = self._engine()
        with mock.patch('requests.post', post), mock.patch('time.sleep'):
            with self.assertRaises(ProviderOverloaded) as caught:
                engine._request('hi', self._schema())
        self.assertIn("'primary'", str(caught.exception))
        self.assertEqual(engine.model_name, 'primary')
        self.assertEqual(calls.count('backup'), 3)

    def test_no_fallback_configured_raises_after_the_normal_retries(self):
        from odoo.addons.nextcontract_ai.services.llm_engine import ProviderOverloaded
        self.provider.fallback_model_id = False
        busy = _response(503, {'error': {'message': 'overloaded'}})
        post, calls = self._by_model({'primary': busy})
        engine = self._engine()
        with mock.patch('requests.post', post), mock.patch('time.sleep'):
            with self.assertRaises(ProviderOverloaded):
                engine._request('hi', self._schema())
        self.assertEqual(calls, ['primary'] * 3)

    def test_auth_and_quota_errors_never_fall_back(self):
        """A second model cannot cure a bad key or an exhausted quota."""
        for status in (401, 404, 429):
            post, calls = self._by_model({
                'primary': _response(status, {'error': {'message': 'nope'}})})
            engine = self._engine()
            with mock.patch('requests.post', post), mock.patch('time.sleep'):
                with self.assertRaises(UserError):
                    engine._request('hi', self._schema())
            self.assertEqual(calls, ['primary'], status)

    def test_fallback_must_belong_to_the_provider(self):
        other = self.env['nextcontract.ai.provider'].create({
            'name': 'Other', 'provider_type': 'openai', 'api_key': SECRET,
            'model_ids': [(0, 0, {'name': 'foreign'})]})
        with self.assertRaises(ValidationError):
            self.provider.fallback_model_id = other.model_ids

    def test_gemini_overload_falls_back_too(self):
        """The Gemini adapter signals overload the same way the REST ones do."""
        gemini = self.env['nextcontract.ai.provider'].create({
            'name': 'Gem FB', 'provider_type': 'gemini', 'api_key': 'k',
            'model_ids': [(0, 0, {'name': 'g-primary'}), (0, 0, {'name': 'g-backup'})]})
        gemini.write({'default_model_id': gemini.model_ids[0].id,
                      'fallback_model_id': gemini.model_ids[1].id})

        class Overloaded(Exception):
            code = 503

        asked = []
        parsed = mock.MagicMock()

        def generate_content(model, contents, config):
            asked.append(model)
            if model == 'g-primary':
                raise Overloaded('high demand')
            return mock.MagicMock(parsed=parsed)

        from odoo.addons.nextcontract_ai.services.engine_factory import build_engine
        with mock.patch('odoo.addons.nextcontract_ai.services.gemini_engine.genai.Client') as client, \
                mock.patch('time.sleep'):
            client.return_value.models.generate_content = generate_content
            engine = build_engine(self.env, 'gemini', self.company,
                                  provider_record=gemini)
            response = engine._request('hi', self._schema())
        self.assertEqual(asked, ['g-primary'] * 3 + ['g-backup'])
        self.assertIs(response.parsed, parsed)

    def test_job_records_the_model_that_actually_answered(self):
        contract = self.env['nextcontract.contract'].create({
            'name': 'FB Contract',
            'contract_type_id': self.env.ref('nextcontract.contract_type_nda').id,
            'company_id': self.company.id})
        version = self.env['nextcontract.contract.version'].create({
            'contract_id': contract.id,
            'version_type': 'internal_draft', 'version_number': 1})
        job = self.env['nextcontract.ai.job'].create({
            'contract_version_id': version.id,
            'analysis_type': 'field_extraction',
            'provider_id': self.provider.id})
        busy = _response(503, {'error': {'message': 'overloaded'}})
        ok = _openai_reply(json.dumps(_NAME_FIELD))
        post, calls = self._by_model({'primary': busy, 'backup': ok})
        with mock.patch.object(type(job), '_read_payload',
                               return_value=_payload(page_count=1)), \
                mock.patch('requests.post', post), mock.patch('time.sleep'):
            job.action_run_sync()
        self.assertEqual(job.status, 'completed', job.error_message)
        self.assertEqual(job.model, 'backup')
