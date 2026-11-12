# Part of NextContract. See LICENSE file for full copyright and licensing details.
"""Turn the single Gemini key from Settings into the first AI Provider.

Gemini runs without a provider record still read the old settings, so this is
a convenience, not a requirement: it makes existing installs show up in the
provider picker. The old parameters are left in place so a downgrade works.
"""

from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    providers = env['nextcontract.ai.provider'].with_context(active_test=False)
    if providers.search_count([]):
        return
    params = env['ir.config_parameter']
    api_key = params.get_param('nextcontract_ai.gemini_api_key')
    if not api_key:
        return
    model_name = params.get_param('nextcontract_ai.gemini_model') or 'gemini-3.7-flash'
    provider = providers.create({
        'name': 'Google Gemini',
        'provider_type': 'gemini',
        'api_key': api_key,
        'model_ids': [(0, 0, {'name': model_name})],
    })
    provider.default_model_id = provider.model_ids[:1]
