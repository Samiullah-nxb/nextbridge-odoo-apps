# -*- coding: utf-8 -*-
from odoo.exceptions import UserError

from . import models
from . import controllers


def _uninstall(env):
    """Refuse to uninstall while files exist only on Google Drive: Odoo could no longer read them."""
    moved = env["ir.attachment"].sudo().search_count(
        [("store_fname", "=like", "gdrive:%"), "|", ("res_field", "=", False), ("res_field", "!=", False)])
    if moved:
        raise UserError(env._("Files stored only on Google Drive: %s. Open Settings > Google Drive and click "
                          "'Bring all files back to Odoo', wait until it is done, then uninstall.", moved))
