# Part of NextContract. See LICENSE file for full copyright and licensing details.

from odoo import api, models


class NextcontractHomeBoard(models.AbstractModel):
    """Tiles for the NextContract in-app home board."""

    _name = 'nextcontract.home.board'
    _description = 'NextContract Home Board'

    # (id, label, hint, fa icon, color, parent menu xmlid, fallback action xmlid, group xmlid)
    # Multi-child menus expand in place on the board; single-child tiles open directly.
    _TILES = (
        (
            'contracts', 'Contracts', 'All contracts',
            'fa-file-text-o', '#0f766e',
            'nextcontract.menu_nextcontract_contracts_main', 'nextcontract.action_nextcontract_contract', False,
        ),
        (
            'intake', 'Intake', 'Contract requests',
            'fa-inbox', '#2563eb',
            'nextcontract.menu_nextcontract_intake', 'nextcontract.action_nextcontract_contract_request', False,
        ),
        (
            'workflow', 'Workflow', 'My tasks',
            'fa-tasks', '#7c3aed',
            'nextcontract.menu_nextcontract_workflow', 'nextcontract.action_nextcontract_workflow_task', False,
        ),
        (
            'negotiation', 'Negotiation', 'Rounds & comparisons',
            'fa-handshake-o', '#db2777',
            'nextcontract.menu_nextcontract_negotiation',
            'nextcontract.action_nextcontract_negotiation_round', False,
        ),
        (
            'extraction', 'Extraction Reviews', 'AI review queue',
            'fa-magic', '#ea580c',
            'nextcontract.menu_nextcontract_ai_review_queue', 'nextcontract.action_nextcontract_ai_review_queue',
            'nextcontract.group_nextcontract_legal',
        ),
        (
            'library', 'Library', 'Templates & clauses',
            'fa-book', '#ca8a04',
            'nextcontract.menu_nextcontract_library', 'nextcontract.action_nextcontract_template', False,
        ),
        (
            'dashboards', 'Dashboards', 'Portfolio reports',
            'fa-bar-chart', '#0891b2',
            'nextcontract.nextcontract_reporting_menu_root',
            'nextcontract.action_nextcontract_reporting_kpi', False,
        ),
        (
            'audit', 'Audit', 'Audit events',
            'fa-shield', '#475569',
            'nextcontract.menu_nextcontract_audit', 'nextcontract.action_nextcontract_audit_event',
            'nextcontract.group_nextcontract_legal_admin',
        ),
        (
            'configuration', 'Configuration', 'Types & settings',
            'fa-cog', '#64748b',
            'nextcontract.menu_nextcontract_configuration', 'nextcontract.action_nextcontract_contract_type',
            'nextcontract.group_nextcontract_legal_admin',
        ),
    )

    @api.model
    def _leaf_menu_actions(self, menu):
        """Collect visible leaf menu items that open an action (header children)."""
        leaves = []
        children = menu.child_id.sorted('sequence')._filter_visible_menus()
        for child in children:
            if child.action:
                leaves.append({
                    'name': child.name,
                    'action_id': child.action.id,
                })
            else:
                leaves.extend(self._leaf_menu_actions(child))
        return leaves

    @api.model
    def _tile_destinations(self, menu_xmlid, fallback_action_xmlid):
        """Resolve direct action or expand-in-place children for one home tile."""
        menu = self.env.ref(menu_xmlid, raise_if_not_found=False) if menu_xmlid else False
        children = []
        if menu:
            children = self._leaf_menu_actions(menu)
            if not children and menu.action and menu._filter_visible_menus():
                return menu.action.id, []

        if len(children) > 1:
            return False, children
        if len(children) == 1:
            return children[0]['action_id'], []

        action = self.env.ref(fallback_action_xmlid, raise_if_not_found=False)
        return (action.id if action else False), []

    @api.model
    def get_tiles(self):
        """Return the module tiles the current user may open."""
        tiles = []
        for (
            tile_id, name, hint, icon, color,
            menu_xmlid, action_xmlid, group_xmlid,
        ) in self._TILES:
            if group_xmlid and not self.env.user.has_group(group_xmlid):
                continue
            if menu_xmlid and not self.env.ref(menu_xmlid, raise_if_not_found=False):
                if not self.env.ref(action_xmlid, raise_if_not_found=False):
                    continue
            action_id, children = self._tile_destinations(menu_xmlid, action_xmlid)
            if not action_id and not children:
                continue
            tiles.append({
                'id': tile_id,
                'name': name,
                'hint': hint if not children else 'Tap to choose',
                'icon': icon,
                'color': color,
                'action_id': action_id,
                'children': children,
            })
        return tiles
