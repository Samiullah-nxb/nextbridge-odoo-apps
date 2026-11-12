# Part of NextContract. See LICENSE file for full copyright and licensing details.

from odoo import _, api, models, fields

class NextcontractAiJobWizard(models.TransientModel):
    _name = 'nextcontract.ai.job.wizard'
    _description = 'AI Analysis Wizard'

    contract_version_id = fields.Many2one('nextcontract.contract.version', required=True)
    
    analysis_type = fields.Selection([
        ('field_extraction', 'Field Extraction'),
        ('clause_analysis', 'Clause Analysis'),
        ('full', 'Full Analysis (Fields + Clauses)')
    ], string='Analysis Type', default='full', required=True)
    
    provider_id = fields.Many2one(
        'nextcontract.ai.provider', string='AI Provider',
        domain="[('active', '=', True)]",
        default=lambda self: self.env['nextcontract.ai.provider'].search([], limit=1),
        help='A connection configured under Configuration → AI Providers.')
    provider_model_id = fields.Many2one(
        'nextcontract.ai.provider.model', string='Model',
        domain="[('provider_id', '=', provider_id)]",
        compute='_compute_provider_model_id', store=True, readonly=False,
        help="Defaults to the provider's default model.")
    # The dialect behind provider_id. Stays editable only for the built-in
    # Mock engine and the legacy single-key Gemini settings, which have no
    # provider record.
    provider = fields.Selection([
        ('mock', 'Mock (Testing)'),
        ('gemini', 'Google Gemini (legacy settings)'),
        ('openai', 'OpenAI'),
        ('anthropic', 'Anthropic Claude'),
        ('custom', 'Custom (OpenAI-compatible)'),
    ], string='Built-in Provider', default='gemini', required=True,
        compute='_compute_provider', store=True, readonly=False)

    @api.depends('provider_id')
    def _compute_provider(self):
        for wizard in self:
            if wizard.provider_id:
                wizard.provider = wizard.provider_id.provider_type
            elif not wizard.provider:
                wizard.provider = 'gemini'

    @api.depends('provider_id')
    def _compute_provider_model_id(self):
        for wizard in self:
            provider = wizard.provider_id
            default = provider.default_model_id or provider.model_ids.filtered(
                'active')[:1]
            wizard.provider_model_id = default

    def _nothing_to_analyse_message(self):
        """Why this run cannot succeed, or False when it can.

        A real provider reads the version's document. Without one the job
        would be accepted, queued, and fail a moment later in the job list
        where the reviewer is least able to act on it. Mock needs no
        document, and clause analysis can run on text already extracted.
        """
        version = self.contract_version_id
        if self.provider == 'mock' or version.document_id.attachment_id:
            return False
        if self.analysis_type == 'clause_analysis' and version.extracted_text:
            return False
        return _(
            "Version %(version)s of '%(contract)s' has no document attached, "
            "so there is nothing for the AI to read. Upload or generate the "
            "contract document on this version, then run the analysis again.",
            version=version.version_number,
            contract=version.contract_id.display_name)

    @staticmethod
    def _toast(title, message, kind='danger'):
        """A notification that closes the dialog instead of blocking on it."""
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': title,
                'message': message,
                'type': kind,
                'sticky': True,
                'next': {'type': 'ir.actions.act_window_close'},
            },
        }

    def action_create_job(self):
        self.ensure_one()
        
        # Explicitly validate access to the underlying contract version first
        self.contract_version_id.contract_id.check_access_rule('read')
        problem = self._nothing_to_analyse_message()
        if problem:
            return self._toast(_("AI analysis not started"), problem)

        job = self.env['nextcontract.ai.job'].sudo().create({
            'contract_version_id': self.contract_version_id.id,
            'analysis_type': self.analysis_type,
            'provider': self.provider,
            'provider_id': self.provider_id.id,
            'provider_model_id': self.provider_model_id.id,
            # Nothing runs until the reviewer clicks Run Analysis. 'draft'
            # keeps the cron worker, which only claims 'pending', away.
            'status': 'draft',
        })

        return {
            'type': 'ir.actions.act_window',
            'name': 'AI Analysis Job',
            'res_model': 'nextcontract.ai.job',
            'res_id': job.id,
            'view_mode': 'form',
            'target': 'current',
        }
