# Part of NextContract. See LICENSE file for full copyright and licensing details.

import json
import unittest.mock as mock

import requests

from odoo.exceptions import UserError, ValidationError
from odoo.tests.common import TransactionCase

SECRET = 'sk-test-SECRET-123'


def _payload(page_count=1, text=None):
    from odoo.addons.nextcontract.services.document_reader import (
        DocumentPayload, DocumentPage,
    )
    return DocumentPayload(
        mimetype='application/pdf', page_count=page_count, text=text,
        text_source='none' if text is None else 'native',
        pages=[
            DocumentPage(page_number=i + 1, image=b'PNGDATA', mimetype='image/png')
            for i in range(page_count)
        ],
    )


def _response(status=200, body=None):
    reply = mock.MagicMock()
    reply.status_code = status
    reply.json.return_value = body if body is not None else {}
    reply.text = json.dumps(body or {})
    return reply


def _openai_reply(content, prompt=11, completion=7):
    return _response(200, {
        'choices': [{'message': {'content': content}, 'finish_reason': 'stop'}],
        'usage': {'prompt_tokens': prompt, 'completion_tokens': completion},
    })


def _anthropic_reply(tool_input, inp=21, out=9):
    return _response(200, {
        'content': [{'type': 'tool_use', 'name': 'record_result', 'input': tool_input}],
        'stop_reason': 'tool_use',
        'usage': {'input_tokens': inp, 'output_tokens': out},
    })


_NAME_FIELD = {
    'name': {'value': 'Master Services Agreement', 'confidence': 91.0,
             'source_page': 1, 'source_text': 'MASTER SERVICES AGREEMENT'},
}


class TestProviderRecord(TransactionCase):

    def _provider(self, **values):
        vals = {'name': 'P %s' % len(values), 'provider_type': 'openai',
                'api_key': SECRET}
        vals.update(values)
        return self.env['nextcontract.ai.provider'].create(vals)

    def test_custom_provider_requires_base_url(self):
        with self.assertRaises(ValidationError):
            self._provider(name='Local', provider_type='custom')

    def test_base_url_must_be_http(self):
        with self.assertRaises(ValidationError):
            self._provider(name='Bad', base_url='ftp://nope')

    def test_hosted_types_always_send_data_off_host(self):
        """The 'self-hosted' flag is honoured for Custom only."""
        for kind in ('gemini', 'openai', 'anthropic'):
            provider = self._provider(
                name='H %s' % kind, provider_type=kind, is_cloud=False)
            self.assertTrue(provider.sends_data_off_host, kind)

    def test_custom_provider_can_be_marked_self_hosted(self):
        provider = self._provider(
            name='Ollama', provider_type='custom', is_cloud=False,
            base_url='http://ollama:11434/v1')
        self.assertFalse(provider.sends_data_off_host)

    def test_default_model_falls_back_to_first_active(self):
        provider = self._provider(
            name='M', model_ids=[(0, 0, {'name': 'gpt-a'}), (0, 0, {'name': 'gpt-b'})])
        self.assertEqual(provider.default_model_name, 'gpt-a')
        provider.default_model_id = provider.model_ids[1]
        self.assertEqual(provider.default_model_name, 'gpt-b')

    def test_default_model_must_belong_to_provider(self):
        first = self._provider(name='A', model_ids=[(0, 0, {'name': 'm1'})])
        second = self._provider(name='B', model_ids=[(0, 0, {'name': 'm2'})])
        with self.assertRaises(ValidationError):
            first.default_model_id = second.model_ids

    def test_api_key_hidden_from_plain_nextcontract_users(self):
        provider = self._provider(name='Secret')
        user = self.env['res.users'].create({
            'name': 'Plain CLM user', 'login': 'plain_nextcontract_user',
            'group_ids': [(6, 0, [
                self.env.ref('base.group_user').id,
                self.env.ref('nextcontract.group_nextcontract_user').id])],
        })
        with self.assertRaises(Exception):
            provider.with_user(user).api_key


class TestEngineFactoryProviders(TransactionCase):

    def setUp(self):
        super().setUp()
        self.company = self.env['res.company'].create({'name': 'Gate Co'})
        self.openai = self.env['nextcontract.ai.provider'].create({
            'name': 'OpenAI', 'provider_type': 'openai', 'api_key': SECRET,
            'model_ids': [(0, 0, {'name': 'gpt-x'})]})
        self.local = self.env['nextcontract.ai.provider'].create({
            'name': 'Local', 'provider_type': 'custom', 'is_cloud': False,
            'base_url': 'http://ollama:11434/v1',
            'model_ids': [(0, 0, {'name': 'llama3'})]})

    def test_hosted_provider_needs_consent(self):
        from odoo.addons.nextcontract_ai.services.engine_factory import (
            build_engine, CloudConsentError)
        with self.assertRaises(CloudConsentError):
            build_engine(self.env, 'openai', self.company,
                         provider_record=self.openai)

    def test_consent_precedes_missing_key(self):
        from odoo.addons.nextcontract_ai.services.engine_factory import (
            build_engine, CloudConsentError)
        self.openai.api_key = False
        with self.assertRaises(CloudConsentError):
            build_engine(self.env, 'openai', self.company,
                         provider_record=self.openai)

    def test_consented_company_gets_engine(self):
        from odoo.addons.nextcontract_ai.services.engine_factory import build_engine
        self.company.acknowledge_cloud_ai_processing()
        engine = build_engine(self.env, 'openai', self.company,
                              provider_record=self.openai)
        self.assertTrue(engine.is_cloud)
        self.assertEqual(engine.model_name, 'gpt-x')

    def test_self_hosted_custom_provider_needs_no_consent(self):
        from odoo.addons.nextcontract_ai.services.engine_factory import build_engine
        engine = build_engine(self.env, 'custom', self.company,
                              provider_record=self.local)
        self.assertFalse(engine.is_cloud)

    def test_connection_probe_skips_consent_but_nothing_else_does(self):
        from odoo.addons.nextcontract_ai.services.engine_factory import build_engine
        engine = build_engine(self.env, 'openai', self.company,
                              provider_record=self.openai,
                              contract_content=False)
        self.assertEqual(engine.model_name, 'gpt-x')

    def test_model_override_wins_over_default(self):
        from odoo.addons.nextcontract_ai.services.engine_factory import build_engine
        engine = build_engine(self.env, 'custom', self.company,
                              provider_record=self.local, model_name='other')
        self.assertEqual(engine.model_name, 'other')

    def test_hosted_provider_without_key_is_refused_clearly(self):
        from odoo.addons.nextcontract_ai.services.engine_factory import build_engine
        self.company.acknowledge_cloud_ai_processing()
        self.openai.api_key = False
        with self.assertRaises(UserError) as caught:
            build_engine(self.env, 'openai', self.company,
                         provider_record=self.openai)
        self.assertIn('API key', str(caught.exception))

    def test_non_gemini_type_needs_a_provider_record(self):
        from odoo.addons.nextcontract_ai.services.engine_factory import build_engine
        self.company.acknowledge_cloud_ai_processing()
        with self.assertRaises(UserError):
            build_engine(self.env, 'anthropic', self.company)

    def test_gemini_record_overrides_legacy_settings(self):
        from odoo.addons.nextcontract_ai.services.engine_factory import build_engine
        self.company.acknowledge_cloud_ai_processing()
        record = self.env['nextcontract.ai.provider'].create({
            'name': 'Gem', 'provider_type': 'gemini', 'api_key': 'k',
            'model_ids': [(0, 0, {'name': 'gemini-test'})]})
        with mock.patch('odoo.addons.nextcontract_ai.services.gemini_engine.genai.Client') as client:
            engine = build_engine(self.env, 'gemini', self.company,
                                  provider_record=record)
        self.assertEqual(engine.model_name, 'gemini-test')
        client.assert_called_once_with(api_key='k')


class TestSchemaFlattening(TransactionCase):

    def test_inlines_every_reference(self):
        from odoo.addons.nextcontract_ai.services.http_engines import plain_json_schema
        from odoo.addons.nextcontract_ai.services.llm_engine import ClauseAnalysisResult
        schema = plain_json_schema(ClauseAnalysisResult)
        text = json.dumps(schema)
        self.assertNotIn('$ref', text)
        self.assertNotIn('$defs', text)
        self.assertIn('matched_text', text)


class TestHttpEngines(TransactionCase):

    def setUp(self):
        super().setUp()
        self.company = self.env.company
        self.company.acknowledge_cloud_ai_processing()
        Provider = self.env['nextcontract.ai.provider']
        self.openai = Provider.create({
            'name': 'OpenAI', 'provider_type': 'openai', 'api_key': SECRET,
            'model_ids': [(0, 0, {'name': 'gpt-x'})]})
        self.claude = Provider.create({
            'name': 'Claude', 'provider_type': 'anthropic', 'api_key': SECRET,
            'max_output_tokens': 4000,
            'model_ids': [(0, 0, {'name': 'claude-x'})]})
        self.custom = Provider.create({
            'name': 'Custom', 'provider_type': 'custom', 'is_cloud': False,
            'base_url': 'http://llm.internal:8000/v1/',
            'structured_mode': 'json_object',
            'model_ids': [(0, 0, {'name': 'local-model'})]})

    def _engine(self, provider):
        from odoo.addons.nextcontract_ai.services.engine_factory import build_engine
        return build_engine(self.env, provider.provider_type, self.company,
                            provider_record=provider)

    def _schema(self):
        from odoo.addons.nextcontract_ai.services.schema_builder import (
            build_extraction_schema, FieldSpec)
        return build_extraction_schema([
            FieldSpec(code='name', label='Name', value_type='char', is_core=True)])

    # -- OpenAI -------------------------------------------------------------

    def test_openai_request_shape(self):
        engine = self._engine(self.openai)
        contents, _ = engine._build_contents(_payload(page_count=2), 'inline')
        with mock.patch('requests.post',
                        return_value=_openai_reply(json.dumps(_NAME_FIELD))) as post:
            response = engine._generate(contents, self._schema())

        args, kwargs = post.call_args
        self.assertEqual(args[0], 'https://api.openai.com/v1/chat/completions')
        self.assertEqual(kwargs['headers']['Authorization'], 'Bearer %s' % SECRET)
        body = kwargs['json']
        self.assertEqual(body['model'], 'gpt-x')
        self.assertNotIn('temperature', body)
        self.assertEqual(body['response_format']['type'], 'json_schema')
        content = body['messages'][0]['content']
        images = [c for c in content if c['type'] == 'image_url']
        self.assertEqual(len(images), 2)
        self.assertTrue(images[0]['image_url']['url'].startswith('data:image/png;base64,'))
        self.assertEqual(response.parsed.name.value, 'Master Services Agreement')
        self.assertEqual(response.usage, (11, 7))

    def test_openai_reply_in_code_fence_is_accepted(self):
        engine = self._engine(self.openai)
        fenced = '```json\n%s\n```' % json.dumps(_NAME_FIELD)
        with mock.patch('requests.post', return_value=_openai_reply(fenced)):
            response = engine._generate('hi', self._schema())
        self.assertEqual(response.parsed.name.confidence, 91.0)

    def test_nonconforming_reply_gives_no_parsed_object(self):
        engine = self._engine(self.openai)
        for reply in ('not json at all', '{"name": {"value": "x"}}', ''):
            with mock.patch('requests.post', return_value=_openai_reply(reply)):
                response = engine._generate('hi', self._schema())
            self.assertIsNone(response.parsed, reply)

    # -- custom / OpenAI-compatible -----------------------------------------

    def test_custom_engine_uses_base_url_without_key_and_sends_temperature(self):
        engine = self._engine(self.custom)
        self.assertFalse(engine.is_cloud)
        with mock.patch('requests.post',
                        return_value=_openai_reply(json.dumps(_NAME_FIELD))) as post:
            engine._generate('hi', self._schema())
        args, kwargs = post.call_args
        self.assertEqual(args[0], 'http://llm.internal:8000/v1/chat/completions')
        self.assertNotIn('Authorization', kwargs['headers'])
        body = kwargs['json']
        self.assertEqual(body['temperature'], 0)
        self.assertEqual(body['response_format'], {'type': 'json_object'})
        # json_object mode carries the schema in the prompt instead.
        self.assertIn('JSON Schema', body['messages'][0]['content'][0]['text'])

    def test_prompt_only_mode_sends_no_response_format(self):
        self.custom.structured_mode = 'prompt'
        engine = self._engine(self.custom)
        with mock.patch('requests.post',
                        return_value=_openai_reply(json.dumps(_NAME_FIELD))) as post:
            engine._generate('hi', self._schema())
        self.assertNotIn('response_format', post.call_args.kwargs['json'])

    def test_temperature_rejected_is_dropped_and_retried_once(self):
        engine = self._engine(self.custom)
        rejected = _response(400, {'error': {
            'message': "Unsupported value: 'temperature' does not support 0"}})
        ok = _openai_reply(json.dumps(_NAME_FIELD))
        with mock.patch('requests.post', side_effect=[rejected, ok]) as post:
            response = engine._generate('hi', self._schema())
        self.assertEqual(post.call_count, 2)
        self.assertNotIn('temperature', post.call_args.kwargs['json'])
        self.assertIsNotNone(response.parsed)

    def test_text_only_provider_sends_text_not_images(self):
        self.custom.supports_vision = False
        engine = self._engine(self.custom)
        self.assertFalse(engine.accepts_documents)
        from odoo.addons.nextcontract.services.document_reader import DocumentPayload
        contents, _ = engine._build_contents(
            DocumentPayload(mimetype='text/plain', text='Hello contract'),
            'inline')
        self.assertEqual([c['type'] for c in contents], ['text', 'text'])
        self.assertEqual(contents[1]['text'], 'Hello contract')

    def test_text_only_provider_with_scan_only_document_fails_clearly(self):
        from odoo.addons.nextcontract_ai.services.llm_engine import UnreadableDocument
        from odoo.addons.nextcontract.services.document_reader import DocumentPayload
        self.custom.supports_vision = False
        engine = self._engine(self.custom)
        with self.assertRaises(UnreadableDocument):
            engine._build_contents(DocumentPayload(mimetype='application/pdf'), 'inline')

    # -- Anthropic ----------------------------------------------------------

    def test_anthropic_request_shape_and_forced_tool(self):
        engine = self._engine(self.claude)
        contents, _ = engine._build_contents(_payload(page_count=1), 'inline')
        with mock.patch('requests.post',
                        return_value=_anthropic_reply(_NAME_FIELD)) as post:
            response = engine._generate(contents, self._schema())

        args, kwargs = post.call_args
        self.assertEqual(args[0], 'https://api.anthropic.com/v1/messages')
        self.assertEqual(kwargs['headers']['x-api-key'], SECRET)
        self.assertIn('anthropic-version', kwargs['headers'])
        body = kwargs['json']
        self.assertEqual(body['max_tokens'], 4000)
        self.assertEqual(body['tool_choice'], {'type': 'tool', 'name': 'record_result'})
        self.assertEqual(body['tools'][0]['input_schema']['type'], 'object')
        self.assertNotIn('$ref', json.dumps(body['tools'][0]['input_schema']))
        blocks = body['messages'][0]['content']
        image = next(b for b in blocks if b['type'] == 'image')
        self.assertEqual(image['source']['type'], 'base64')
        self.assertEqual(image['source']['media_type'], 'image/png')
        self.assertEqual(response.parsed.name.value, 'Master Services Agreement')
        self.assertEqual(response.usage, (21, 9))

    def test_anthropic_reply_without_tool_call_gives_no_parsed_object(self):
        engine = self._engine(self.claude)
        text_only = _response(200, {'content': [{'type': 'text', 'text': 'sorry'}],
                                    'usage': {}})
        with mock.patch('requests.post', return_value=text_only):
            self.assertIsNone(engine._generate('hi', self._schema()).parsed)

    # -- failure handling ---------------------------------------------------

    def test_auth_error_is_actionable_and_never_leaks_the_key(self):
        engine = self._engine(self.openai)
        denied = _response(401, {'error': {'message': 'Incorrect API key provided'}})
        with mock.patch('requests.post', return_value=denied):
            with self.assertRaises(UserError) as caught:
                engine._generate('hi', self._schema())
        message = str(caught.exception)
        self.assertIn('rejected the API key', message)
        self.assertIn('Incorrect API key provided', message)
        self.assertNotIn(SECRET, message)

    def test_unknown_model_points_at_fetch_models(self):
        engine = self._engine(self.openai)
        with mock.patch('requests.post', return_value=_response(
                404, {'error': {'message': 'model not found'}})):
            with self.assertRaises(UserError) as caught:
                engine._generate('hi', self._schema())
        self.assertIn('Fetch Models', str(caught.exception))

    def test_overload_is_retried_then_succeeds(self):
        engine = self._engine(self.openai)
        busy = _response(503, {'error': {'message': 'overloaded'}})
        ok = _openai_reply(json.dumps(_NAME_FIELD))
        with mock.patch('requests.post', side_effect=[busy, busy, ok]) as post, \
                mock.patch('time.sleep') as sleep:
            response = engine._generate('hi', self._schema())
        self.assertEqual(post.call_count, 3)
        self.assertEqual(sleep.call_count, 2)
        self.assertIsNotNone(response.parsed)

    def test_quota_error_is_not_retried(self):
        engine = self._engine(self.openai)
        with mock.patch('requests.post', return_value=_response(
                429, {'error': {'message': 'quota'}})) as post, \
                mock.patch('time.sleep'):
            with self.assertRaises(UserError):
                engine._generate('hi', self._schema())
        self.assertEqual(post.call_count, 1)

    def test_unreachable_server_names_the_url(self):
        engine = self._engine(self.custom)
        with mock.patch('requests.post', side_effect=requests.ConnectionError('boom')):
            with self.assertRaises(UserError) as caught:
                engine._generate('hi', self._schema())
        self.assertIn('llm.internal', str(caught.exception))

    # -- model discovery and the provider buttons ----------------------------

    def test_fetch_models_adds_new_ones_and_skips_non_chat_models(self):
        listing = _response(200, {'data': [
            {'id': 'gpt-x'}, {'id': 'gpt-y'},
            {'id': 'text-embedding-3-small'}, {'id': 'whisper-1'}]})
        with mock.patch('requests.get', return_value=listing):
            action = self.openai.action_fetch_models()
        self.assertEqual(
            sorted(self.openai.model_ids.mapped('name')), ['gpt-x', 'gpt-y'])
        self.assertEqual(action['params']['type'], 'success')

    def test_anthropic_model_listing(self):
        listing = _response(200, {'data': [{'id': 'claude-a'}, {'id': 'claude-b'}]})
        with mock.patch('requests.get', return_value=listing) as get:
            names = self._engine(self.claude).list_models()
        self.assertEqual(names, ['claude-a', 'claude-b'])
        self.assertEqual(get.call_args.args[0], 'https://api.anthropic.com/v1/models?limit=1000')

    def test_test_connection_succeeds_without_consent(self):
        """The probe carries no contract text, so it needs no consent."""
        self.company.revoke_cloud_ai_processing()
        with mock.patch('requests.post',
                        return_value=_openai_reply('{"status": "OK"}')):
            action = self.openai.action_test_connection()
        self.assertEqual(action['params']['type'], 'success')

    def test_test_connection_reports_failure_instead_of_raising(self):
        with mock.patch('requests.post', return_value=_response(
                401, {'error': {'message': 'bad key'}})):
            action = self.openai.action_test_connection()
        self.assertEqual(action['params']['type'], 'danger')
        self.assertIn('bad key', action['params']['message'])

    # -- end to end ----------------------------------------------------------

    def _job_for(self, provider):
        contract = self.env['nextcontract.contract'].create({
            'name': 'Provider Contract',
            'contract_type_id': self.env.ref('nextcontract.contract_type_nda').id,
            'company_id': self.company.id,
        })
        version = self.env['nextcontract.contract.version'].create({
            'contract_id': contract.id,
            'version_type': 'internal_draft', 'version_number': 1,
        })
        return self.env['nextcontract.ai.job'].create({
            'contract_version_id': version.id,
            'analysis_type': 'field_extraction',
            'provider_id': provider.id,
        })

    def _run(self, job, reply):
        with mock.patch.object(type(job), '_read_payload',
                               return_value=_payload(page_count=1)), \
                mock.patch('requests.post', return_value=reply):
            job.action_run_sync()

    def test_job_runs_end_to_end_on_openai(self):
        job = self._job_for(self.openai)
        self.assertEqual(job.provider, 'openai')
        self._run(job, _openai_reply(json.dumps(_NAME_FIELD)))
        self.assertEqual(job.status, 'completed', job.error_message)
        self.assertEqual(job.model, 'gpt-x')
        self.assertEqual((job.input_tokens, job.output_tokens), (11, 7))
        self.assertEqual(job.consent_provider, 'OpenAI')
        suggestion = job.suggestion_ids.filtered(lambda s: s.field_name == 'name')
        self.assertEqual(suggestion.suggested_value, 'Master Services Agreement')

    def test_job_runs_end_to_end_on_anthropic(self):
        job = self._job_for(self.claude)
        self._run(job, _anthropic_reply(_NAME_FIELD))
        self.assertEqual(job.status, 'completed', job.error_message)
        self.assertEqual(job.model, 'claude-x')
        self.assertTrue(job.suggestion_ids)

    def test_job_records_requested_model_and_rerun_keeps_it(self):
        other = self.env['nextcontract.ai.provider.model'].create({
            'provider_id': self.openai.id, 'name': 'gpt-other'})
        job = self._job_for(self.openai)
        job.provider_model_id = other
        self._run(job, _openai_reply(json.dumps(_NAME_FIELD)))
        self.assertEqual(job.model, 'gpt-other')
        rerun = job.action_rerun_extraction()
        self.assertEqual(rerun.provider_id, self.openai)
        self.assertEqual(rerun.provider_model_id, other)

    def test_malformed_reply_fails_the_job_with_response_invalid(self):
        job = self._job_for(self.openai)
        self._run(job, _openai_reply('I could not find anything.'))
        self.assertEqual(job.status, 'failed')
        self.assertEqual(job.failure_reason, 'response_invalid')

    def test_job_without_consent_never_reaches_the_network(self):
        self.company.revoke_cloud_ai_processing()
        job = self._job_for(self.openai)
        with mock.patch('requests.post') as post:
            job.action_run_sync()
        post.assert_not_called()
        self.assertEqual(job.failure_reason, 'consent_missing')

    def test_wizard_defaults_to_provider_and_its_default_model(self):
        self.openai.default_model_id = self.openai.model_ids[:1]
        wizard = self.env['nextcontract.ai.job.wizard'].new({})
        self.assertTrue(wizard.provider_id)
        wizard.provider_id = self.openai
        wizard._compute_provider()
        wizard._compute_provider_model_id()
        self.assertEqual(wizard.provider, 'openai')
        self.assertEqual(wizard.provider_model_id.name, 'gpt-x')


class TestJobReviewAndDispatch(TransactionCase):
    """Regressions for the review form's chatter and the worker wake-up."""

    def setUp(self):
        super().setUp()
        contract = self.env['nextcontract.contract'].create({
            'name': 'Dispatch Contract',
            'contract_type_id': self.env.ref('nextcontract.contract_type_nda').id,
            'company_id': self.env.company.id,
        })
        self.version = self.env['nextcontract.contract.version'].create({
            'contract_id': contract.id,
            'version_type': 'internal_draft', 'version_number': 1,
        })
        self.job = self.env['nextcontract.ai.job'].create({
            'contract_version_id': self.version.id, 'provider': 'mock'})

    def test_job_has_a_mail_thread_for_the_review_form_chatter(self):
        """The review form embeds <chatter/>; without a thread behind it the
        web client's mail request raised AttributeError and broke the page."""
        self.assertTrue(hasattr(self.job, '_get_thread_with_access'))
        self.job.message_post(body='Reviewed offline')
        self.assertIn('Reviewed offline', ''.join(self.job.message_ids.mapped('body')))

    def test_no_form_view_embeds_chatter_on_a_model_without_a_thread(self):
        import re
        offenders = []
        for view in self.env['ir.ui.view'].search(
                [('type', '=', 'form'), ('model', '!=', False)]):
            if re.search(r'<chatter|oe_chatter', view.arch_db or ''):
                model = self.env.get(view.model)
                if model is not None and 'message_ids' not in model._fields:
                    offenders.append((view.model, view.xml_id))
        self.assertFalse(offenders, offenders)

    def test_enqueueing_a_job_wakes_the_worker(self):
        triggers = self.env['ir.cron.trigger'].sudo()
        before = triggers.search_count([])
        self.job.action_run_async()
        self.assertEqual(triggers.search_count([]), before + 1)

    def test_wizard_creates_a_draft_and_does_not_wake_the_worker(self):
        triggers = self.env['ir.cron.trigger'].sudo()
        before = triggers.search_count([])
        wizard = self.env['nextcontract.ai.job.wizard'].create({
            'contract_version_id': self.version.id, 'provider': 'mock'})
        action = wizard.action_create_job()
        job = self.env['nextcontract.ai.job'].browse(action['res_id'])
        # Nothing runs until the reviewer clicks Run Analysis.
        self.assertEqual(job.status, 'draft')
        self.assertEqual(triggers.search_count([]), before)
