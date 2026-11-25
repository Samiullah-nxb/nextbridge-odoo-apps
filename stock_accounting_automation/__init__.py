from . import models
from . import wizard


def _post_init_hook(env):
    """Create the interim accounts for every company that already has a chart of accounts."""
    companies = env['res.company'].search([('chart_template', '!=', False)], order='parent_path')
    companies._stock_move_accounting_setup_interim_accounts()
