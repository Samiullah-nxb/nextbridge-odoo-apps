# Part of NextContract. See LICENSE file for full copyright and licensing details.

from odoo import api, models, fields

class NextcontractMigrationAiWizard(models.TransientModel):
    _name = 'nextcontract.migration.ai.wizard'
    _description = 'Migration AI Extraction Wizard'

    scope = fields.Selection([
        ('all', 'All Migrated Contracts in Batch'),
        ('selected', 'Selected Contracts'),
        ('missing', 'Only Contracts with Missing Fields')
    ], string='Extraction Scope', default='missing', required=True)

    batch_id = fields.Many2one('nextcontract.migration.batch')

    provider_id = fields.Many2one(
        'nextcontract.ai.provider', string='AI Provider',
        domain="[('active', '=', True)]",
        default=lambda self: self.env['nextcontract.ai.provider'].search([], limit=1),
        help='A connection configured under Configuration → AI Providers. '
             'Empty uses the legacy Gemini key in Settings.')
    provider_model_id = fields.Many2one(
        'nextcontract.ai.provider.model', string='Model',
        domain="[('provider_id', '=', provider_id)]",
        compute='_compute_provider_model_id', store=True, readonly=False,
        help="Defaults to the provider's default model.")

    @api.depends('provider_id')
    def _compute_provider_model_id(self):
        for wizard in self:
            provider = wizard.provider_id
            wizard.provider_model_id = provider.default_model_id or provider.model_ids.filtered(
                'active')[:1]

    def action_run_ai(self):
        self.ensure_one()

        contracts = self.env['nextcontract.contract']
        if self.scope == 'all':
            contracts = self.env['nextcontract.contract'].search([('migration_batch_id', '=', self.batch_id.id)])
        elif self.scope == 'missing':
            contracts = self.env['nextcontract.contract'].search([
                ('migration_batch_id', '=', self.batch_id.id),
                '|', '|',
                ('contract_value', '=', 0),
                ('effective_date', '=', False),
                ('expiry_date', '=', False)
            ])

        # Queue AI Jobs for extraction. A provider record sets the job's
        # provider type on create; without one the legacy Gemini settings run.
        for contract in contracts:
            executed_version = contract.version_ids.filtered(lambda v: v.version_type == 'executed')[:1]
            # A job needs a version to read; one contract without it must not
            # abort the queue for the rest of the batch.
            if not executed_version:
                continue
            self.env['nextcontract.ai.job'].create({
                'contract_id': contract.id,
                'contract_version_id': executed_version.id,
                'analysis_type': 'field_extraction',
                'provider': 'gemini',
                'provider_id': self.provider_id.id,
                'provider_model_id': self.provider_model_id.id,
                'status': 'pending'
            })
        if contracts:
            self.env['nextcontract.ai.job']._wake_worker()

        return {'type': 'ir.actions.act_window_close'}
