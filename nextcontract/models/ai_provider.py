# Part of NextContract. See LICENSE file for full copyright and licensing details.

import logging

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)

# Ids a hosted provider lists that can never answer a chat request, so they
# are left out when models are fetched rather than cluttering the picker.
_NON_CHAT_MARKERS = (
    'embedding', 'whisper', 'tts', 'dall-e', 'moderation', 'image',
    'audio', 'realtime', 'transcribe', 'davinci', 'babbage',
)


class NextcontractAiProvider(models.Model):
    """A configured connection to an AI vendor or self-hosted server.

    Several can exist side by side (two OpenAI accounts, a cloud Gemini and
    a local Ollama) and each job records which one produced its result.
    """
    _name = 'nextcontract.ai.provider'
    _description = 'AI Provider'
    _order = 'sequence, id'

    name = fields.Char(required=True, help='Shown when choosing a provider, e.g. "OpenAI (Legal team)".')
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    provider_type = fields.Selection([
        ('gemini', 'Google Gemini'),
        ('openai', 'OpenAI'),
        ('anthropic', 'Anthropic Claude'),
        ('custom', 'Custom (OpenAI-compatible)'),
    ], string='Type', required=True, default='openai',
        help='Which API dialect this provider speaks. "Custom" covers any '
             'server exposing an OpenAI-style /chat/completions endpoint: '
             'Ollama, vLLM, LM Studio, OpenRouter, an internal gateway.')
    api_key = fields.Char(
        string='API Key', groups='nextcontract.group_nextcontract_legal_admin,base.group_system',
        help='Stored on this record and readable only by CLM administrators. '
             'Leave empty for a local server that needs no key.')
    base_url = fields.Char(
        string='Base URL',
        help='API root, e.g. https://api.openai.com/v1. Leave empty to use '
             'the vendor default. Required for Custom.')
    model_ids = fields.One2many(
        'nextcontract.ai.provider.model', 'provider_id', string='Models')
    default_model_id = fields.Many2one(
        'nextcontract.ai.provider.model', string='Default Model',
        domain="[('provider_id', '=', id)]",
        help='Pre-selected when running an analysis with this provider.')
    fallback_model_id = fields.Many2one(
        'nextcontract.ai.provider.model', string='Fallback Model',
        domain="[('provider_id', '=', id)]",
        help='Tried once, automatically, when the chosen model stays '
             'overloaded after its retries. Pick a different model that is '
             'less likely to be busy at the same time. The job records which '
             'model actually answered.')
    default_model_name = fields.Char(
        compute='_compute_default_model_name',
        help='The default model, or the first active one when none is set.')
    structured_mode = fields.Selection([
        ('json_schema', 'JSON Schema (strict structured output)'),
        ('json_object', 'JSON object (schema given in the prompt)'),
        ('prompt', 'Prompt only (no response_format)'),
    ], string='Structured Output', default='json_schema', required=True,
        help='How the model is asked for JSON. Use a weaker mode if a '
             'self-hosted server rejects response_format. Applies to OpenAI '
             'and Custom types.')
    supports_vision = fields.Boolean(
        string='Reads Images', default=True,
        help='Untick for a text-only model. Scanned documents without a text '
             'layer cannot then be processed.')
    is_cloud = fields.Boolean(
        string='Hosted by a third party', default=True,
        help='Untick only for a server you operate yourself, on your own '
             'network. Contract content never leaves your control, so the '
             'cloud-processing consent is not required. Ignored for OpenAI, '
             'Anthropic and Gemini, which are always third parties.')
    sends_data_off_host = fields.Boolean(
        compute='_compute_sends_data_off_host', store=True,
        help='Whether contract content leaves your infrastructure, and so '
             'whether company consent is required before this provider runs.')
    max_output_tokens = fields.Integer(
        string='Max Output Tokens', default=8192,
        help='Reply length cap. Anthropic requires one; raise it if long '
             'extractions come back truncated.')

    _name_uniq = models.Constraint(
        'unique(name)', 'A provider with this name already exists.')

    @api.depends('provider_type', 'is_cloud')
    def _compute_sends_data_off_host(self):
        for provider in self:
            provider.sends_data_off_host = (
                provider.provider_type != 'custom' or provider.is_cloud)

    @api.depends('default_model_id', 'model_ids.name', 'model_ids.active')
    def _compute_default_model_name(self):
        for provider in self:
            model = provider.default_model_id or provider.model_ids.filtered(
                'active')[:1]
            provider.default_model_name = model.name or ''

    @api.constrains('provider_type', 'base_url')
    def _check_base_url(self):
        for provider in self:
            if provider.provider_type == 'custom' and not provider.base_url:
                raise ValidationError(
                    "A Custom provider needs a Base URL, e.g. "
                    "http://ollama:11434/v1.")
            if provider.base_url and not provider.base_url.startswith(
                    ('http://', 'https://')):
                raise ValidationError(
                    "The Base URL must start with http:// or https://.")

    @api.constrains('default_model_id', 'fallback_model_id')
    def _check_default_model(self):
        for provider in self:
            for model in (provider.default_model_id, provider.fallback_model_id):
                if model and model.provider_id != provider:
                    raise ValidationError(
                        "The default and fallback models must be one of this "
                        "provider's models.")

    def _build_engine(self, model_name=None):
        """Engine for a connection check: no contract content is involved."""
        from ..services.engine_factory import build_engine
        self.ensure_one()
        return build_engine(
            self.env, self.provider_type, self.env.company,
            provider_record=self, model_name=model_name,
            contract_content=False)

    def action_test_connection(self):
        """Send a one-line request and report what came back."""
        self.ensure_one()
        try:
            engine = self._build_engine()
            reply = engine.probe()
        except Exception as error:
            _logger.info("Provider %s connection test failed", self.name,
                         exc_info=True)
            return self._notify(
                'Connection failed', str(error), 'danger', sticky=True)
        return self._notify(
            'Connection works',
            "%s answered '%s' using model %s." % (
                self.name, reply, engine.model_name),
            'success')

    def action_fetch_models(self):
        """Add the models this key can call, keeping any already listed."""
        self.ensure_one()
        try:
            discovered = self._build_engine(
                model_name=self.default_model_name or 'unused').list_models()
        except UserError as error:
            return self._notify(
                'Could not fetch models', str(error), 'danger', sticky=True)
        except Exception as error:
            return self._notify(
                'Could not fetch models', str(error), 'danger', sticky=True)

        if self.provider_type == 'openai':
            discovered = [
                name for name in discovered
                if not any(marker in name.lower() for marker in _NON_CHAT_MARKERS)
            ]
        known = set(self.model_ids.with_context(active_test=False).mapped('name'))
        new_names = [name for name in discovered if name not in known]
        if new_names:
            self.env['nextcontract.ai.provider.model'].create([
                {'provider_id': self.id, 'name': name} for name in new_names
            ])
        if not self.default_model_id and self.model_ids:
            self.default_model_id = self.model_ids[:1]
        return self._notify(
            'Models updated',
            '%d new model(s) added; %d available in total.' % (
                len(new_names), len(self.model_ids)),
            'success')

    @staticmethod
    def _notify(title, message, kind, sticky=False):
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': title, 'message': message,
                'type': kind, 'sticky': sticky,
            },
        }


class NextcontractAiProviderModel(models.Model):
    """One model a provider can run, by the id the vendor's API expects."""
    _name = 'nextcontract.ai.provider.model'
    _description = 'AI Provider Model'
    _order = 'provider_id, name'

    provider_id = fields.Many2one(
        'nextcontract.ai.provider', required=True, ondelete='cascade', index=True)
    name = fields.Char(
        string='Model ID', required=True,
        help='Exactly as the vendor\'s API expects, e.g. gpt-4.1, '
             'claude-sonnet-5-5 or llama3.1:70b.')
    active = fields.Boolean(default=True)

    _provider_name_uniq = models.Constraint(
        'unique(provider_id, name)',
        'This model is already listed for the provider.')

    @api.depends('provider_id.name', 'name')
    def _compute_display_name(self):
        for model in self:
            model.display_name = model.name
