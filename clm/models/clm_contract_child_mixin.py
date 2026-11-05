# Part of NextContract. See LICENSE file for full copyright and licensing details.

import functools

from odoo import api, models
from odoo.tools import SQL


class ClmContractChildMixin(models.AbstractModel):
    """A record that is exactly as visible as the contract it belongs to.

    Record rules cannot do this: Odoo evaluates ``ir.rule`` domains with
    ``sudo()``, so a rule such as ``('contract_id', 'any', [])`` does not
    re-apply the *contract's* rules. Without this mixin, every CLM user could
    list and open the versions, clauses, alerts... of a Restricted contract that
    the contract rules hide from them.

    Both the search path (lists, filters, counts, many2one pickers) and the
    direct-access path (read / write / unlink by id) are covered, and both
    defer to the live ``clm.contract`` access rules, so the two never drift.
    """
    _name = 'clm.contract.child.mixin'
    _description = 'Record visible only with its contract'

    # Name of the many2one to clm.contract; a null link is not restricted.
    _clm_contract_field = 'contract_id'

    @api.model
    def _search(self, domain, offset=0, limit=None, order=None, *,
                active_test=True, bypass_access=False):
        query = super()._search(
            domain, offset=offset, limit=limit, order=order,
            active_test=active_test, bypass_access=bypass_access,
        )
        if self.env.su or bypass_access or query.is_empty():
            return query
        contracts = self.env['clm.contract']._search([])
        column = SQL.identifier(self._table, self._clm_contract_field)
        query.add_where(SQL(
            '(%s IS NULL OR %s IN %s)', column, column, contracts.subselect(),
        ))
        return query

    def _check_access(self, operation):
        result = super()._check_access(operation)
        if self.env.su:
            return result
        forbidden = result[0] if result else self.browse()
        field = self._clm_contract_field
        candidates = (self - forbidden).filtered(lambda rec: rec.sudo()[field])
        if not candidates:
            return result
        contracts = candidates.sudo()[field]
        hidden = contracts - contracts.with_env(self.env)._filtered_access('read')
        if not hidden:
            return result
        extra = candidates.filtered(lambda rec: rec.sudo()[field] in hidden)
        forbidden |= extra
        return forbidden, functools.partial(
            self.env['ir.rule']._make_access_error, operation, forbidden)
