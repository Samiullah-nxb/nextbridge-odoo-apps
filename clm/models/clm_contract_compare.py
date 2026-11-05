# Part of NextContract. See LICENSE file for full copyright and licensing details.

from odoo import models
from odoo.exceptions import UserError


class ClmContractCompare(models.Model):
    _inherit = 'clm.contract'

    def action_compare_versions(self):
        """Redline the current version against the nearest earlier differing one.

        Parent alone is often Signature Draft → Executed (same wording by design).
        Walk earlier versions until the redline engine finds a real difference.
        """
        self.ensure_one()
        target = self.current_version_id
        if not target:
            raise UserError('This contract has no versions to compare yet.')
        base = self._clm_previous_differing_version(target)
        if not base:
            raise UserError(
                'Compare Versions needs at least two versions. Create a new version first.'
            )
        return self.env['clm.document.comparison']._clm_compare(base, target)._clm_open_action()

    def _clm_previous_differing_version(self, target):
        """Nearest earlier version whose wording or DOCX text differs from target."""
        self.ensure_one()
        earlier = self.version_ids.filtered(
            lambda v: v.version_number < target.version_number
        ).sorted('version_number', reverse=True)
        if not earlier:
            return target.parent_version_id

        from ..services.redline_engine import HtmlRedlineEngine
        engine = HtmlRedlineEngine()
        for base in earlier:
            result = engine.build_result(base, target)
            if not result.get('identical'):
                return base

        # Every earlier version matches — still open parent/previous so the
        # empty state can explain (e.g. Signature Draft → Executed).
        return target.parent_version_id or earlier[0]
