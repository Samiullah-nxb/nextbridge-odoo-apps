# Part of NextContract. See LICENSE file for full copyright and licensing details.

from odoo import api, fields, models

class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    clm_ai_gemini_api_key = fields.Char(
        string='Gemini API Key',
        config_parameter='clm_ai.gemini_api_key',
        help='Secure API key for Google Gemini'
    )
    # Google retires model versions, and a retired name fails every job with a
    # bare 404 from the SDK. The rolling '-latest' aliases are listed first
    # for that reason: they keep working across a retirement, at the cost of
    # the underlying model changing beneath a deployment. Pin a version only
    # where reproducible extractions matter more than surviving a retirement.
    clm_ai_gemini_model = fields.Selection([
        ('gemini-flash-latest', 'Gemini Flash (Latest — tracks current release)'),
        ('gemini-pro-latest', 'Gemini Pro (Latest — tracks current release)'),
        ('gemini-3.7-flash', 'Gemini 3.7 Flash (Recommended)'),
        ('gemini-3.8-flash', 'Gemini 3.8 Flash'),
        ('gemini-3.5-flash', 'Gemini 3.5 Flash'),
        ('gemini-3.5-flash-lite', 'Gemini 3.5 Flash Lite (Cheapest)'),
    ], string='Gemini Model', config_parameter='clm_ai.gemini_model',
        default='gemini-3.7-flash')
    
    clm_ai_enabled = fields.Boolean(
        string='Enable AI Features',
        config_parameter='clm_ai.enabled',
        default=True
    )

    clm_ai_confidence_high = fields.Float(
        string='High Confidence Threshold (%)',
        config_parameter='clm_ai.confidence_high', default=95.0,
        help='At or above this, a suggestion is pre-selected for the reviewer.'
    )
    clm_ai_confidence_low = fields.Float(
        string='Low Confidence Threshold (%)',
        config_parameter='clm_ai.confidence_low', default=80.0,
        help='Below this, a suggestion blocks review confirmation.'
    )
    clm_ai_max_pages = fields.Integer(
        string='Maximum Pages Per Document',
        config_parameter='clm_ai.max_pages', default=300,
        help='Documents longer than this fail rather than being truncated.'
    )
    clm_ai_inline_payload_limit_mb = fields.Float(
        string='Inline Payload Limit (MB)',
        config_parameter='clm_ai.inline_payload_limit_mb', default=15.0,
        help='Requests larger than this are uploaded via the provider Files API.'
    )
    clm_ai_generate_text_layer = fields.Boolean(
        string='Generate Searchable Text Layer',
        config_parameter='clm_ai.generate_text_layer', default=False,
        help='Ask the vision model to transcribe scanned pages for full-text '
             'search. Off by default: transcription costs output tokens on '
             'every page of every document.'
    )

    clm_ai_cloud_processing_acknowledged = fields.Boolean(
        related='company_id.clm_ai_cloud_processing_acknowledged',
        readonly=True,
        string='Cloud AI Processing Acknowledged',
        help='Whether this company has explicitly opted in to sending '
             'contract content to a cloud AI provider (e.g. Gemini).'
    )
    clm_ai_cloud_consent_user_id = fields.Many2one(
        'res.users', related='company_id.clm_ai_cloud_consent_user_id',
        readonly=True, string='Consent Given By',
    )
    clm_ai_cloud_consent_date = fields.Datetime(
        related='company_id.clm_ai_cloud_consent_date',
        readonly=True, string='Consent Given On',
    )

    def action_acknowledge_cloud_ai_processing(self):
        self.ensure_one()
        self.company_id.acknowledge_cloud_ai_processing()

    def action_revoke_cloud_ai_processing(self):
        self.ensure_one()
        self.company_id.revoke_cloud_ai_processing()
