# Part of NextContract. See LICENSE file for full copyright and licensing details.
"""A job must hand the worker back before Odoo's real-time limit.

Odoo restarts the whole server when a cron thread outlives that limit, which
takes every open page down. These tests pin the behaviour that prevents it.
"""

import json
import time
import unittest.mock as mock

from odoo.tests.common import TransactionCase

from .test_llm_providers import SECRET, _NAME_FIELD, _openai_reply, _response


class TestTimeBudget(TransactionCase):

    def setUp(self):
        super().setUp()
        self.company = self.env.company
        self.company.acknowledge_cloud_ai_processing()
        self.provider = self.env['nextcontract.ai.provider'].create({
            'name': 'Budget', 'provider_type': 'openai', 'api_key': SECRET,
            'model_ids': [(0, 0, {'name': 'primary'}), (0, 0, {'name': 'backup'})]})
        self.provider.write({
            'default_model_id': self.provider.model_ids[0].id,
            'fallback_model_id': self.provider.model_ids[1].id})

    def _engine(self, seconds_left):
        from odoo.addons.nextcontract_ai.services.engine_factory import build_engine
        engine = build_engine(self.env, 'openai', self.company,
                              provider_record=self.provider)
        engine._deadline = time.monotonic() + seconds_left
        return engine

    def _schema(self):
        from odoo.addons.nextcontract_ai.services.schema_builder import (
            build_extraction_schema, FieldSpec)
        return build_extraction_schema([
            FieldSpec(code='name', label='Name', value_type='char', is_core=True)])

    def test_budget_follows_odoo_worker_limit(self):
        from odoo.addons.nextcontract_ai.services.llm_engine import worker_time_budget
        from odoo.tools import config
        with mock.patch.dict(config.options, {
                'limit_time_real_cron': -1, 'limit_time_real': 120}):
            self.assertEqual(worker_time_budget(), 90.0)
        with mock.patch.dict(config.options, {'limit_time_real_cron': 600}):
            self.assertEqual(worker_time_budget(), 570.0)
        with mock.patch.dict(config.options, {'limit_time_real_cron': 0}):
            self.assertGreaterEqual(worker_time_budget(), 3600.0)

    def test_no_time_left_fails_before_any_network_call(self):
        from odoo.addons.nextcontract_ai.services.llm_engine import JobTimeBudgetExceeded
        engine = self._engine(seconds_left=2)
        with mock.patch('requests.post') as post:
            with self.assertRaises(JobTimeBudgetExceeded):
                engine._request('hi', self._schema())
        post.assert_not_called()

    def test_request_timeout_is_capped_to_the_time_left(self):
        engine = self._engine(seconds_left=40)
        with mock.patch('requests.post',
                        return_value=_openai_reply(json.dumps(_NAME_FIELD))) as post:
            engine._request('hi', self._schema())
        connect, read = post.call_args.kwargs['timeout']
        self.assertLessEqual(read, 40)
        self.assertGreater(read, 30)

    def test_overload_with_little_time_left_does_not_retry_or_fall_back(self):
        from odoo.addons.nextcontract_ai.services.llm_engine import ProviderOverloaded
        busy = _response(503, {'error': {'message': 'overloaded'}})
        engine = self._engine(seconds_left=12)
        with mock.patch('requests.post', return_value=busy) as post, \
                mock.patch('time.sleep') as sleep:
            with self.assertRaises(ProviderOverloaded):
                engine._request('hi', self._schema())
        self.assertEqual(post.call_count, 1)
        sleep.assert_not_called()

    def test_ample_time_still_retries_and_falls_back(self):
        busy = _response(503, {'error': {'message': 'overloaded'}})
        ok = _openai_reply(json.dumps(_NAME_FIELD))
        calls = []

        def post(url, headers=None, json=None, timeout=None):
            calls.append(json['model'])
            return busy if json['model'] == 'primary' else ok

        engine = self._engine(seconds_left=85)
        with mock.patch('requests.post', post), mock.patch('time.sleep'):
            engine._request('hi', self._schema())
        self.assertEqual(calls, ['primary'] * 3 + ['backup'])

    def test_gemini_requests_carry_a_timeout_from_the_budget(self):
        gemini = self.env['nextcontract.ai.provider'].create({
            'name': 'Gem Budget', 'provider_type': 'gemini', 'api_key': 'k',
            'model_ids': [(0, 0, {'name': 'g1'})]})
        seen = {}

        def generate_content(model, contents, config):
            seen['timeout'] = config.http_options.timeout
            return mock.MagicMock(parsed=object())

        from odoo.addons.nextcontract_ai.services.engine_factory import build_engine
        with mock.patch('odoo.addons.nextcontract_ai.services.gemini_engine.genai.Client') as client:
            client.return_value.models.generate_content = generate_content
            engine = build_engine(self.env, 'gemini', self.company,
                                  provider_record=gemini)
            engine._deadline = time.monotonic() + 50
            engine._request('hi', self._schema())
        self.assertLessEqual(seen['timeout'], 50000)
        self.assertGreater(seen['timeout'], 40000)

    def test_shared_deadline_comes_from_context(self):
        deadline = time.monotonic() + 33
        from odoo.addons.nextcontract_ai.services.engine_factory import build_engine
        engine = build_engine(
            self.env(context=dict(self.env.context, nextcontract_ai_deadline=deadline)),
            'openai', self.company, provider_record=self.provider)
        self.assertLessEqual(engine._time_left(), 33)
        self.assertGreater(engine._time_left(), 30)

    def test_cron_stops_between_jobs_and_wakes_the_worker(self):
        """Out of budget with work left: stop cleanly, ask for a fresh run."""
        contract = self.env['nextcontract.contract'].create({
            'name': 'Budget Contract',
            'contract_type_id': self.env.ref('nextcontract.contract_type_nda').id,
            'company_id': self.company.id})
        version = self.env['nextcontract.contract.version'].create({
            'contract_id': contract.id,
            'version_type': 'internal_draft', 'version_number': 1})
        job = self.env['nextcontract.ai.job'].create({
            'contract_version_id': version.id, 'provider': 'mock'})
        triggers = self.env['ir.cron.trigger'].sudo()
        before = triggers.search_count([])

        original_commit = self.env.cr.commit
        self.env.cr.commit = self.env.cr.flush
        self.addCleanup(setattr, self.env.cr, 'commit', original_commit)
        with mock.patch(
                'odoo.addons.nextcontract_ai.services.llm_engine.worker_time_budget',
                return_value=5.0):
            self.env['nextcontract.ai.job']._cron_process_pending_jobs()

        self.assertEqual(job.status, 'pending')
        self.assertEqual(triggers.search_count([]), before + 1)
