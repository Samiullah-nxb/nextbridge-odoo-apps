# Part of NextContract. See LICENSE file for full copyright and licensing details.
"""
Contract Lifecycle Overview — the read-only screen behind *View Lifecycle*.

Nothing here is a second workflow. The stages below are a presentation
grouping of the contract states declared in ``clm_base`` (``_CONTRACT_STATES``):
every stage owns a set of real states, and a contract is on exactly the stage
that owns its current state. Where a contract has been, when it got there and
who moved it is read back from the audit trail; who a stage is waiting on comes
from the workflow tasks; the change assessment comes from the document
versions. The buttons on the view call the transition methods that already
exist on ``clm.contract``.

Two of the stage names the business uses have no state of their own:

* *Change Assessment* and *OK to Proceed* are recorded on the document version
  (``clm.contract.version.change_classification``), not on the contract. They
  are shown as a note on the Sent to Counterparty / Negotiation stages rather
  than invented as states.
"""

from collections import defaultdict

from odoo import api, fields, models
from odoo.tools.misc import format_date, format_datetime

# ─── Lifecycle stages ────────────────────────────────────────────────────────
# Ordered presentation stages. `states` are the clm.contract states that put a
# contract on that stage; together they cover every state except 'cancelled',
# which leaves the lifecycle rather than sitting on it.
#
# Finer chart: counterparty send and negotiation are separate, and each
# post-execution repository outcome is its own stage. Initiator Response stays
# grouped — those four awaiting_* states are an optional side path.
_STAGES = (
    {'code': 'draft', 'label': 'Draft', 'icon': 'fa-pencil-square-o',
     'states': ('draft',)},
    {'code': 'legal_review', 'label': 'Legal Review', 'icon': 'fa-balance-scale',
     'states': ('in_review',)},
    {'code': 'initiator_response', 'label': 'Initiator Response',
     'icon': 'fa-comments-o', 'states': (
        'awaiting_business', 'awaiting_finance',
        'awaiting_procurement', 'awaiting_compliance',
    )},
    {'code': 'internal_review', 'label': 'Internal Review', 'icon': 'fa-users',
     'states': ('internal_review_done',)},
    {'code': 'sent_to_counterparty', 'label': 'Sent to Counterparty',
     'icon': 'fa-paper-plane-o', 'states': (
        'sent_to_counterparty',
    )},
    {'code': 'negotiation', 'label': 'Negotiation', 'icon': 'fa-exchange',
     'states': ('in_negotiation',)},
    {'code': 'approval', 'label': 'Approval', 'icon': 'fa-thumbs-o-up',
     'states': ('approved',)},
    {'code': 'signing', 'label': 'Signing', 'icon': 'fa-pencil',
     'states': ('pending_signature',)},
    {'code': 'executed', 'label': 'Executed', 'icon': 'fa-certificate',
     'states': ('executed',)},
    {'code': 'active', 'label': 'Active', 'icon': 'fa-play-circle',
     'states': ('active',)},
    {'code': 'expiring_soon', 'label': 'Expiring Soon', 'icon': 'fa-hourglass-half',
     'states': ('expiring_soon',)},
    {'code': 'renewed', 'label': 'Renewed', 'icon': 'fa-refresh',
     'states': ('renewed',)},
    {'code': 'expired', 'label': 'Expired', 'icon': 'fa-calendar-times-o',
     'states': ('expired',)},
    {'code': 'terminated', 'label': 'Terminated', 'icon': 'fa-times-circle-o',
     'states': ('terminated',)},
    {'code': 'archived', 'label': 'Archived', 'icon': 'fa-archive',
     'states': ('archived',)},
)

_STATE_STAGE = {
    state: index
    for index, stage in enumerate(_STAGES)
    for state in stage['states']
}

# Compact phase bar above the fine stage track (hybrid chart). Each phase owns
# one or more fine stages; status is derived from those stages.
_PHASES = (
    {'code': 'draft', 'label': 'Draft', 'icon': 'fa-pencil-square-o',
     'stages': ('draft',)},
    {'code': 'review', 'label': 'Review', 'icon': 'fa-balance-scale', 'stages': (
        'legal_review', 'initiator_response', 'internal_review',
    )},
    {'code': 'counterparty', 'label': 'Counterparty', 'icon': 'fa-handshake-o',
     'stages': (
        'sent_to_counterparty', 'negotiation',
    )},
    {'code': 'approval', 'label': 'Approval', 'icon': 'fa-thumbs-o-up',
     'stages': ('approval',)},
    {'code': 'signing', 'label': 'Signing', 'icon': 'fa-pencil',
     'stages': ('signing',)},
    {'code': 'live', 'label': 'Live', 'icon': 'fa-play-circle', 'stages': (
        'executed', 'active', 'expiring_soon', 'renewed',
    )},
    {'code': 'closed', 'label': 'Closed', 'icon': 'fa-archive', 'stages': (
        'expired', 'terminated', 'archived',
    )},
)

# States that stop the lifecycle instead of advancing it.
_OFF_PATH_STATES = ('cancelled',)

# The lifecycle has run its course in these states; the current stage is no
# longer "in progress".
_COMPLETED_STATES = ('executed', 'active', 'expiring_soon', 'renewed')
_CLOSED_STATES = ('expired', 'terminated', 'archived')

# Who a stage is waiting on, when no open workflow task names an assignee.
# First field with a value wins; `partner_id` means the counterparty.
_STATE_RESPONSIBLE = {
    'draft': ('user_id',),
    'in_review': ('legal_owner_id', 'user_id'),
    'awaiting_business': ('user_id',),
    'awaiting_finance': ('finance_owner_id', 'user_id'),
    'awaiting_procurement': ('procurement_owner_id', 'user_id'),
    'awaiting_compliance': ('legal_owner_id', 'user_id'),
    'internal_review_done': ('legal_owner_id', 'user_id'),
    'sent_to_counterparty': ('partner_id',),
    'in_negotiation': ('partner_id', 'legal_owner_id'),
    'approved': ('legal_owner_id', 'user_id'),
    'pending_signature': ('partner_id', 'user_id'),
    'executed': ('user_id', 'legal_owner_id'),
}

# Marker presentation per stage status. Colours come from Bootstrap utilities
# so the view follows the Odoo theme (including dark mode) rather than a
# palette of its own.
_STAGE_MARKERS = {
    'done': {
        'status_icon': 'fa-check-circle',
        'marker_class': 'bg-success text-white border-success',
        'label_class': 'text-body',
        'status_class': 'o_clm_status_done',
        'title': 'Completed',
    },
    'current': {
        'status_icon': 'fa-dot-circle-o',
        'marker_class': 'bg-primary text-white border-primary',
        'label_class': 'fw-bolder text-primary',
        'status_class': 'o_clm_status_current',
        'title': 'Current stage',
    },
    'pending': {
        'status_icon': 'fa-circle-o',
        'marker_class': 'bg-transparent text-muted border-secondary',
        'label_class': 'text-muted',
        'status_class': 'o_clm_status_pending',
        'title': 'Pending',
    },
    'skipped': {
        'status_icon': 'fa-minus-circle',
        'marker_class': 'bg-transparent text-muted border-secondary',
        'label_class': 'text-muted',
        'status_class': 'o_clm_status_skipped',
        'title': 'Not used on this contract',
    },
    'halted': {
        'status_icon': 'fa-times-circle',
        'marker_class': 'bg-danger text-white border-danger',
        'label_class': 'fw-bolder text-danger',
        'status_class': 'o_clm_status_halted',
        'title': 'Stopped here',
    },
}

# Shown when a stage or phase declares no icon of its own.
_DEFAULT_ICON = 'fa-circle-o'

# Short status wording for the phase cards and stage rows. The statuses
# themselves are the ones _lifecycle_stage_states() already computes.
_STATUS_LABELS = {
    'done': 'Completed',
    'current': 'Current',
    'pending': 'Pending',
    'skipped': 'Not used',
    'halted': 'Stopped',
}

# Change assessment chips, keyed by clm.contract.version.change_classification.
_ASSESSMENT_CLASSES = {
    'ok_to_proceed': 'text-bg-success',
    'material': 'text-bg-warning',
    'immaterial': 'text-bg-secondary',
}


class ClmContractLifecycleOverview(models.Model):
    _inherit = 'clm.contract'

    # ─── Current stage ───────────────────────────────────────────────────────
    lifecycle_stage_code = fields.Char(
        string='Lifecycle Stage Code', compute='_compute_lifecycle_overview',
    )
    lifecycle_stage_label = fields.Char(
        string='Lifecycle Stage', compute='_compute_lifecycle_overview',
    )
    lifecycle_stage_status = fields.Char(
        string='Stage Status', compute='_compute_lifecycle_overview',
        help='Whether the current stage is still running, finished, or stopped.',
    )
    lifecycle_stage_assignee = fields.Char(
        string='Assigned To', compute='_compute_lifecycle_overview',
        help='The open workflow task assignees for this stage, or the '
             'responsible party recorded on the contract.',
    )
    lifecycle_stage_started = fields.Datetime(
        string='Stage Started', compute='_compute_lifecycle_overview',
        help='When the contract entered its current state, from the audit trail.',
    )
    lifecycle_position = fields.Char(
        string='Lifecycle Position', compute='_compute_lifecycle_overview',
    )
    lifecycle_progress = fields.Integer(
        string='Lifecycle Progress', compute='_compute_lifecycle_overview',
        help='Percentage of the lifecycle stages this contract has completed.',
    )
    lifecycle_returned_count = fields.Integer(
        string='Times Returned', compute='_compute_lifecycle_overview',
        help='How often this contract was sent back to an earlier stage.',
    )
    lifecycle_open_task_ids = fields.Many2many(
        'clm.workflow.task', string='Open Workflow Tasks',
        compute='_compute_lifecycle_open_tasks',
    )
    lifecycle_final_stored = fields.Boolean(
        string='Stored in Documents', compute='_compute_lifecycle_final_stored',
        help='The final version has a file registered in Odoo Documents.',
    )

    # ─── Rendered blocks ─────────────────────────────────────────────────────
    lifecycle_chart_html = fields.Html(
        string='Contract Lifecycle', compute='_compute_lifecycle_overview',
        sanitize=False,
    )
    lifecycle_history_html = fields.Html(
        string='Contract History', compute='_compute_lifecycle_overview',
        sanitize=False,
    )

    # -------------------------------------------------------------------------
    # Computes
    # -------------------------------------------------------------------------

    @api.depends('state', 'user_id', 'legal_owner_id', 'finance_owner_id',
                 'procurement_owner_id', 'partner_id', 'write_date')
    def _compute_lifecycle_overview(self):
        history_by_contract = self._lifecycle_state_history()
        assessment_by_contract = self._lifecycle_change_assessment()
        state_labels = self._lifecycle_state_labels()
        total = len(_STAGES)

        for contract in self:
            history = history_by_contract.get(contract.id, [])
            assessment = assessment_by_contract.get(contract.id)
            stages, position = contract._lifecycle_stage_states(history, assessment)

            contract.lifecycle_returned_count = sum(stage['returned'] for stage in stages)
            contract.lifecycle_stage_started = contract._lifecycle_stage_start(history)

            index = position['index']
            if index is None:
                contract.lifecycle_stage_code = False
                contract.lifecycle_stage_label = state_labels.get(contract.state, contract.state)
                contract.lifecycle_position = ''
            else:
                stage = _STAGES[index]
                contract.lifecycle_stage_code = stage['code']
                contract.lifecycle_stage_label = stage['label']
                contract.lifecycle_position = 'Stage %s of %s' % (index + 1, total)

            # Progress counts the stages actually completed, so the bar and the
            # "x / y stages completed" line can never disagree.
            done_total = sum(1 for stage in stages if stage['status'] == 'done')
            contract.lifecycle_progress = round((done_total / total) * 100) if total else 0
            contract.lifecycle_stage_status = contract._lifecycle_stage_status()
            contract.lifecycle_stage_assignee = contract._lifecycle_stage_assignee()

            phases = contract._lifecycle_phases(stages)
            current_phase = next(
                (phase for phase in phases if phase['status'] == 'current'), None,
            )
            contract.lifecycle_chart_html = self.env['ir.qweb']._render(
                'clm.lifecycle_chart',
                {
                    'phases': phases,
                    'cancelled': contract.state in _OFF_PATH_STATES,
                    'cancelled_label': state_labels.get(contract.state, contract.state),
                    'progress': contract.lifecycle_progress,
                    'done_total': done_total,
                    'stage_total': total,
                    'current_phase_label': current_phase['label'] if current_phase else '',
                    'current_stage_label': contract.lifecycle_stage_label,
                    # Which way the one toolbar toggle points when the chart
                    # first renders. Presentation only: it describes the cards
                    # built above, and nothing reads it back.
                    'all_expanded': bool(phases) and all(
                        phase['expanded'] for phase in phases
                    ),
                },
            )
            contract.lifecycle_history_html = self.env['ir.qweb']._render(
                'clm.lifecycle_history',
                {'entries': contract._lifecycle_history_entries(history)},
            )

    @api.depends('workflow_task_ids.status')
    def _compute_lifecycle_open_tasks(self):
        # Re-search under the caller's env so record rules apply. Assigning
        # tasks the user cannot read raises AccessError on this computed M2M
        # ("Failed to write field lifecycle_open_task_ids") and blocks
        # View Lifecycle — e.g. a Business Requester opening a contract whose
        # open tasks are assigned to Legal.
        Task = self.env['clm.workflow.task']
        for contract in self:
            open_tasks = contract._lifecycle_open_tasks()
            contract.lifecycle_open_task_ids = (
                Task.search([('id', 'in', open_tasks.ids)]) if open_tasks else Task
            )

    @api.depends('final_contract_document_id')
    def _compute_lifecycle_final_stored(self):
        for contract in self:
            # bool() on the recordset — the documents.document record itself is
            # never read, so this stays safe for users without Documents access.
            contract.lifecycle_final_stored = bool(contract.final_contract_document_id)

    # -------------------------------------------------------------------------
    # Lifecycle data
    # -------------------------------------------------------------------------

    def _lifecycle_state_labels(self):
        """Translated labels for clm.contract.state."""
        selection = self.env['clm.contract'].fields_get(['state'])['state']['selection']
        return dict(selection)

    def _lifecycle_state_history(self):
        """State changes for these contracts, oldest first, keyed by contract.

        Read with ``sudo``: ``clm.audit.event`` is restricted to Legal Admin,
        but ``state`` is a tracked field, so the same "who moved it, when" is
        already in the chatter for anyone who can read the contract. Only
        state-change rows for these contracts are read, and only the state,
        actor and timestamp are used.
        """
        history = defaultdict(list)
        if not self.ids:
            return history
        labels = self._lifecycle_state_labels()
        events = self.env['clm.audit.event'].sudo().search(
            [('contract_id', 'in', self.ids), ('event_type', '=', 'state_change')],
            order='create_date asc, id asc',
        )
        for event in events:
            state = event.new_value
            if state not in labels:
                continue
            history[event.contract_id.id].append({
                'state': state,
                'label': labels[state],
                'actor': event.user_id.display_name,
                'date': event.create_date,
                'index': _STATE_STAGE.get(state),
            })
        return history

    def _lifecycle_change_assessment(self):
        """The latest meaningful change classification per contract.

        'No Material Change' is the default on every version, so it says
        nothing; only an actual assessment is reported.
        """
        assessment = {}
        if not self.ids:
            return assessment
        Version = self.env['clm.contract.version'].sudo()
        labels = dict(
            Version.fields_get(['change_classification'])['change_classification']['selection']
        )
        rows = Version.search_read(
            [
                ('contract_id', 'in', self.ids),
                ('change_classification', 'not in', (False, 'none')),
            ],
            ['contract_id', 'change_classification'],
            order='contract_id, version_number desc, id desc',
        )
        for row in rows:
            contract_id = row['contract_id'][0]
            if contract_id in assessment:
                continue
            code = row['change_classification']
            assessment[contract_id] = {
                'label': labels.get(code, code),
                'class': _ASSESSMENT_CLASSES.get(code, 'text-bg-secondary'),
            }
        return assessment

    def _lifecycle_stage_states(self, history, assessment=None):
        """Return ``(stages, position)`` describing where this contract stands.

        ``stages`` is one dict per lifecycle stage, ready to render.
        ``position`` carries the current stage index (``None`` off the path)
        and the highest stage index the contract ever reached.
        """
        self.ensure_one()
        visited = set()
        entered_at = {}
        returned = defaultdict(int)
        reached = -1
        for entry in history:
            index = entry['index']
            if index is None:
                continue
            if index < reached:
                returned[index] += 1
            reached = max(reached, index)
            visited.add(index)
            entered_at[index] = entry['date']

        cancelled = self.state in _OFF_PATH_STATES
        current = None if cancelled else _STATE_STAGE.get(self.state)
        if current is not None:
            visited.add(current)
            reached = max(reached, current)

        state_labels = self._lifecycle_state_labels()
        stages = []
        for index, stage in enumerate(_STAGES):
            if cancelled:
                status = 'halted' if index == reached else (
                    'done' if index < reached else 'pending'
                )
            elif current is None:
                status = 'done' if index in visited else 'pending'
            elif index < current:
                status = 'done' if index in visited else 'skipped'
            elif index == current:
                status = 'current'
            else:
                status = 'pending'

            notes = []
            if index == current and len(stage['states']) > 1:
                # Initiator Response covers several awaiting_* states — name
                # the one the contract is actually in.
                notes.append(state_labels.get(self.state, self.state))
            marker = _STAGE_MARKERS[status]
            entered = entered_at.get(index)
            stages.append({
                'code': stage['code'],
                'number': index + 1,
                'label': stage['label'],
                'status': status,
                'status_label': _STATUS_LABELS[status],
                # Two icons, two jobs: the stage's own icon says *what*
                # the step is; the status icon says how far it got. A
                # column of identical circles said neither.
                'icon': stage.get('icon', _DEFAULT_ICON),
                'status_icon': marker['status_icon'],
                'status_class': marker['status_class'],
                'marker_class': marker['marker_class'],
                'label_class': marker['label_class'],
                'title': marker['title'],
                # The track is narrow: show the day, keep the time for the tooltip.
                'date': format_date(
                    self.env, fields.Datetime.context_timestamp(self, entered).date()
                ) if entered else '',
                'date_title': format_datetime(self.env, entered) if entered else '',
                'notes': notes,
                'returned': returned.get(index, 0),
                # Change assessment is recorded during counterparty / negotiation.
                'assessment': assessment if stage['code'] in (
                    'sent_to_counterparty', 'negotiation',
                ) else None,
                # Whether the connector into / out of this stage has been
                # travelled — drawn solid up to the furthest stage reached.
                'link_in': 0 < index <= reached,
                'link_out': index + 1 <= reached,
            })
        return stages, {'index': current, 'reached': reached}

    def _lifecycle_phases(self, stages):
        """The lifecycle as phases, each owning the stages declared in _PHASES.

        Nothing is recomputed here: a phase's status, its progress and which
        of its stages is current all follow from the stage statuses that
        _lifecycle_stage_states() already worked out. The same stage dicts are
        handed through, so a stage is never duplicated.
        """
        by_code = {stage['code']: stage for stage in stages}
        phases = []
        for phase in _PHASES:
            members = [by_code[code] for code in phase['stages'] if code in by_code]
            statuses = [stage['status'] for stage in members]
            if 'halted' in statuses:
                status = 'halted'
            elif 'current' in statuses:
                status = 'current'
            elif statuses and all(s == 'skipped' for s in statuses):
                status = 'skipped'
            elif statuses and all(s in ('done', 'skipped') for s in statuses):
                status = 'done'
            else:
                status = 'pending'
            current_member = next(
                (stage for stage in members if stage['status'] == 'current'),
                None,
            )
            done = sum(1 for s in statuses if s == 'done')
            total = len(members)
            marker = _STAGE_MARKERS[status]
            phases.append({
                'code': phase['code'],
                'label': phase['label'],
                'status': status,
                'status_label': _STATUS_LABELS[status],
                'icon': phase.get('icon', _DEFAULT_ICON),
                'status_icon': marker['status_icon'],
                'status_class': marker['status_class'],
                'marker_class': marker['marker_class'],
                'label_class': marker['label_class'],
                'title': marker['title'],
                'detail': current_member['label'] if current_member else '',
                'stages': members,
                'done': done,
                'total': total,
                'percent': round(done / total * 100) if total else 0,
                'returned': sum(stage['returned'] for stage in members),
                # Open the phase the contract is on — or the one it stopped on.
                # Everything else starts collapsed; this is UI state only and
                # the reader can toggle any card.
                'expanded': status in ('current', 'halted'),
            })
        return phases

    def _lifecycle_stage_start(self, history):
        """When the contract entered its current state."""
        self.ensure_one()
        for entry in reversed(history):
            if entry['state'] == self.state:
                return entry['date']
        return False

    def _lifecycle_stage_status(self):
        """Coarse status of the current stage, in the words the business uses."""
        self.ensure_one()
        if self.state in _OFF_PATH_STATES:
            return 'Cancelled'
        if self.state in _CLOSED_STATES:
            return 'Closed'
        if self.state in _COMPLETED_STATES:
            return 'Completed'
        return 'In Progress'

    def _lifecycle_open_tasks(self):
        """Workflow tasks still owing on this contract.

        ``sudo`` because the task record rule scopes tasks to their assignee,
        and this view has to say who a stage is waiting on even when that is
        somebody else.
        """
        self.ensure_one()
        return self.sudo().workflow_task_ids.filtered(
            lambda task: task.status in ('pending', 'in_progress')
        )

    def _lifecycle_stage_assignee(self):
        """Who the current stage is waiting on."""
        self.ensure_one()
        names = [
            name for name in self._lifecycle_open_tasks().mapped(
                'assigned_user_id.display_name'
            ) if name
        ]
        if names:
            return ', '.join(dict.fromkeys(names))
        for field_name in _STATE_RESPONSIBLE.get(self.state, ('user_id',)):
            party = self[field_name]
            if party:
                return party.display_name
        return self.user_id.display_name or ''

    def _lifecycle_history_entries(self, history):
        """The state history as timeline entries, oldest first."""
        self.ensure_one()
        entries = []
        reached = -1
        for entry in history:
            index = entry['index']
            returned = index is not None and index < reached
            if index is not None:
                reached = max(reached, index)
            entries.append({
                'label': entry['label'],
                'stage': _STAGES[index]['label'] if index is not None else '',
                'actor': entry['actor'],
                'date': format_datetime(self.env, entry['date'], dt_format='medium'),
                'returned': returned,
                'current': False,
                'icon': 'fa-reply' if returned else 'fa-check',
                'marker_class': 'bg-warning text-white border-warning' if returned
                                else 'bg-success text-white border-success',
            })
        if entries:
            last = entries[-1]
            last['current'] = True
            if self.state in _OFF_PATH_STATES:
                last['icon'] = 'fa-ban'
                last['marker_class'] = 'bg-danger text-white border-danger'
            elif not last['returned']:
                last['icon'] = 'fa-dot-circle-o'
                last['marker_class'] = 'bg-primary text-white border-primary'
        return entries

    # -------------------------------------------------------------------------
    # UI actions
    # -------------------------------------------------------------------------

    def action_open_lifecycle(self):
        """Open the Contract Lifecycle overview for this contract."""
        self.ensure_one()
        view = self.env.ref('clm.view_clm_contract_lifecycle_form')
        return {
            'type': 'ir.actions.act_window',
            'name': 'Contract Lifecycle',
            'res_model': 'clm.contract',
            'res_id': self.id,
            'view_mode': 'form',
            'views': [(view.id, 'form')],
            'target': 'current',
        }

    def action_open_contract_record(self):
        """Leave the overview for the contract's own form."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': self.display_name,
            'res_model': 'clm.contract',
            'res_id': self.id,
            'view_mode': 'form',
            'views': [(self.env.ref('clm.view_clm_contract_form').id, 'form')],
            'target': 'current',
        }
