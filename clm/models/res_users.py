# Part of NextContract. See LICENSE file for full copyright and licensing details.

from odoo import api, models


class ResUsers(models.Model):
    _inherit = 'res.users'

    @api.model
    def clm_get_home_board_tiles(self):
        """Web-safe entry point for the NextContract Home Board tiles."""
        return self.env['clm.home.board'].get_tiles()
