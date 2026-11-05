# Part of NextContract. See LICENSE file for full copyright and licensing details.
"""
Tests for the Contract Lifecycle overview.

The overview claims to show where a contract really is, so the tests drive
contracts through the real transition methods and then assert what the screen
would say: which stages are complete, which one is current, which were never
used, and where the contract was sent back.
"""

from odoo.tests.common import TransactionCase, new_test_user

from ..models.clm_contract_lifecycle_overview import _STAGES, _STATE_STAGE, _PHASES

_PHASES_BY_CODE = {phase['code']: phase for phase in _PHASES}


def _flatten(phases):
    """Every stage of every phase, in lifecycle order."""
    return [stage for phase in phases for stage in phase['stages']]


class TestLifecycleOverview(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.contract_type = cls.env.ref('clm.contract_type_services')
        cls.partner = cls.env['res.partner'].create({'name': 'Acme Corporation'})
        # Approving a workflow task is restricted to its assignee or a Legal
        # Admin; the test user drives the whole flow.
        cls.env.user.group_ids |= cls.env.ref('clm.group_clm_legal_admin')

    def _make_contract(self, **values):
        return self.env['clm.contract'].create({
            'name': 'IT Services Agreement',
            'contract_type_id': self.contract_type.id,
            'partner_id': self.partner.id,
            **values,
        })

    def _stages(self, contract):
        """The rendered stage list, keyed by stage code."""
        history = contract._lifecycle_state_history().get(contract.id, [])
        stages, position = contract._lifecycle_stage_states(history)
        return {stage['code']: stage for stage in stages}, position

    def _phases(self, contract):
        """The lifecycle as the screen groups it: phases owning their stages."""
        history = contract._lifecycle_state_history().get(contract.id, [])
        stages, _position = contract._lifecycle_stage_states(history)
        return contract._lifecycle_phases(stages)

    def _complete_open_tasks(self, contract):
        tasks = contract._lifecycle_open_tasks()
        tasks.with_env(self.env).action_approve()

    # -------------------------------------------------------------------------
    # The stage map
    # -------------------------------------------------------------------------

    def test_every_contract_state_belongs_to_a_stage(self):
        """No state may fall off the lifecycle except the ones that leave it."""
        selection = self.env['clm.contract'].fields_get(['state'])['state']['selection']
        unmapped = {
            code for code, _label in selection if code not in _STATE_STAGE
        }
        self.assertEqual(
            unmapped, {'cancelled'},
            'Only cancelled leaves the lifecycle; every other state must map to a stage.',
        )

    def test_stage_states_do_not_overlap(self):
        seen = set()
        for stage in _STAGES:
            overlap = seen & set(stage['states'])
            self.assertFalse(overlap, 'State(s) %s claimed by two stages.' % overlap)
            seen |= set(stage['states'])

    # -------------------------------------------------------------------------
    # Where the contract is
    # -------------------------------------------------------------------------

    def test_finer_stages_cover_counterparty_and_repository_outcomes(self):
        codes = [stage['code'] for stage in _STAGES]
        self.assertIn('sent_to_counterparty', codes)
        self.assertIn('negotiation', codes)
        self.assertNotIn('external_review', codes)
        self.assertNotIn('repository', codes)
        for code in (
            'active', 'expiring_soon', 'renewed',
            'expired', 'terminated', 'archived',
        ):
            self.assertIn(code, codes)

    def test_phases_cover_every_fine_stage_exactly_once(self):
        covered = []
        for phase in _PHASES:
            covered.extend(phase['stages'])
        self.assertEqual(sorted(covered), sorted(stage['code'] for stage in _STAGES))

    def test_chart_renders_phases_that_own_their_stages(self):
        contract = self._make_contract()
        contract.action_submit_for_review()
        chart = contract.lifecycle_chart_html
        self.assertIn('o_clm_phase_list', chart)
        self.assertIn('o_clm_stage_list', chart)
        for phase in _PHASES:
            self.assertIn(phase['label'], chart)

        phases = {phase['code']: phase for phase in self._phases(contract)}
        self.assertEqual(phases['draft']['status'], 'done')
        self.assertEqual(phases['review']['status'], 'current')
        self.assertEqual(phases['review']['detail'], 'Legal Review')
        self.assertEqual(phases['live']['status'], 'pending')

    def test_every_stage_appears_under_exactly_one_phase(self):
        """The hierarchy regroups the stages; it never duplicates them."""
        contract = self._make_contract()
        nested = [
            stage['code']
            for phase in self._phases(contract)
            for stage in phase['stages']
        ]
        self.assertEqual(
            sorted(nested), sorted(stage['code'] for stage in _STAGES),
            'Each stage belongs to one phase, and none is dropped or repeated.',
        )
        for phase in self._phases(contract):
            self.assertEqual(
                [stage['code'] for stage in phase['stages']],
                list(_PHASES_BY_CODE[phase['code']]['stages']),
                'A phase lists its own stages, in the declared order.',
            )

    def test_phase_progress_counts_completed_stages(self):
        contract = self._make_contract()
        contract.action_submit_for_review()
        self._complete_open_tasks(contract)          # Legal Review approved ...
        contract.action_complete_internal_review()   # ... → internal_review_done

        phases = {phase['code']: phase for phase in self._phases(contract)}
        draft, review, live = phases['draft'], phases['review'], phases['live']

        self.assertEqual((draft['done'], draft['total']), (1, 1))
        self.assertEqual(draft['percent'], 100)
        # Legal Review is done, Initiator Response was never used, Internal
        # Review is where the contract stands.
        self.assertEqual((review['done'], review['total']), (1, 3))
        self.assertEqual((live['done'], live['total']), (0, 4))
        self.assertEqual(live['percent'], 0)

        stages = {stage['code']: stage for stage in _flatten(self._phases(contract))}
        self.assertEqual(stages['initiator_response']['status'], 'skipped')
        self.assertEqual(stages['internal_review']['status'], 'current')

    def test_overall_progress_matches_the_completed_stages(self):
        contract = self._make_contract()
        contract.action_submit_for_review()

        stages = _flatten(self._phases(contract))
        done = sum(1 for stage in stages if stage['status'] == 'done')
        self.assertEqual(done, 1)
        self.assertEqual(
            contract.lifecycle_progress, round(done / len(_STAGES) * 100),
        )
        self.assertIn('%s / %s stages completed' % (done, len(_STAGES)),
                      contract.lifecycle_chart_html)

    def test_current_phase_is_expanded_and_pending_phases_are_not(self):
        contract = self._make_contract()
        contract.action_submit_for_review()

        phases = {phase['code']: phase for phase in self._phases(contract)}
        self.assertTrue(phases['review']['expanded'], 'The current phase opens.')
        self.assertFalse(phases['draft']['expanded'], 'Completed phases start closed.')
        self.assertFalse(phases['signing']['expanded'], 'Pending phases start closed.')
        # Rendered as a native disclosure, so expanding needs no script and
        # never writes to the contract.
        self.assertIn('<details', contract.lifecycle_chart_html)

    def test_a_cancelled_contract_opens_the_phase_it_stopped_on(self):
        contract = self._make_contract()
        contract.action_submit_for_review()
        contract.action_cancel()

        phases = {phase['code']: phase for phase in self._phases(contract)}
        self.assertEqual(phases['review']['status'], 'halted')
        self.assertTrue(phases['review']['expanded'])
        self.assertFalse(phases['approval']['expanded'])

    def test_phase_reports_returns_made_inside_it(self):
        contract = self._make_contract()
        contract.action_submit_for_review()
        contract.action_return_for_clarification()   # Legal Review → Draft
        contract.action_submit_for_review()

        phases = {phase['code']: phase for phase in self._phases(contract)}
        self.assertEqual(phases['draft']['returned'], 1)
        self.assertEqual(phases['approval']['returned'], 0)
        self.assertIn('Returned', contract.lifecycle_chart_html)

    def test_new_contract_sits_on_draft(self):
        contract = self._make_contract()
        stages, position = self._stages(contract)

        self.assertEqual(position['index'], 0)
        self.assertEqual(stages['draft']['status'], 'current')
        self.assertEqual(stages['legal_review']['status'], 'pending')
        self.assertEqual(stages['active']['status'], 'pending')
        self.assertEqual(stages['archived']['status'], 'pending')
        self.assertEqual(contract.lifecycle_stage_label, 'Draft')
        self.assertEqual(contract.lifecycle_stage_status, 'In Progress')
        self.assertEqual(contract.lifecycle_position, 'Stage 1 of %s' % len(_STAGES))

    def test_submitting_for_review_completes_draft(self):
        contract = self._make_contract()
        contract.action_submit_for_review()

        stages, position = self._stages(contract)
        self.assertEqual(position['index'], _STATE_STAGE['in_review'])
        self.assertEqual(stages['draft']['status'], 'done')
        self.assertEqual(stages['legal_review']['status'], 'current')
        self.assertEqual(stages['approval']['status'], 'pending')
        self.assertTrue(stages['draft']['date'], 'A completed stage shows when it happened.')

    def test_stage_never_visited_is_marked_skipped(self):
        """Initiator Response is optional — a contract that skipped it says so."""
        contract = self._make_contract()
        contract.action_submit_for_review()
        self._complete_open_tasks(contract)
        contract.action_complete_internal_review()

        stages, _position = self._stages(contract)
        self.assertEqual(stages['initiator_response']['status'], 'skipped')
        self.assertEqual(stages['legal_review']['status'], 'done')
        self.assertEqual(stages['internal_review']['status'], 'current')

    def test_stage_that_was_used_is_marked_done(self):
        contract = self._make_contract()
        contract.action_submit_for_review()
        contract.action_request_finance_input()
        self._complete_open_tasks(contract)
        contract.action_complete_internal_review()

        stages, _position = self._stages(contract)
        self.assertEqual(stages['initiator_response']['status'], 'done')

    def test_current_stage_names_the_underlying_state(self):
        """Stages that cover several states say which one the contract is in."""
        contract = self._make_contract()
        contract.action_submit_for_review()
        contract.action_request_procurement_input()

        stages, _position = self._stages(contract)
        self.assertEqual(stages['initiator_response']['status'], 'current')
        self.assertIn('Awaiting Procurement Input', stages['initiator_response']['notes'])

    # -------------------------------------------------------------------------
    # Returns and cancellation
    # -------------------------------------------------------------------------

    def test_return_for_changes_is_shown_on_the_stage_and_in_history(self):
        contract = self._make_contract()
        contract.action_submit_for_review()
        contract.action_return_for_clarification()   # back to Draft
        contract.action_submit_for_review()

        stages, position = self._stages(contract)
        self.assertEqual(stages['draft']['returned'], 1)
        self.assertEqual(position['index'], _STATE_STAGE['in_review'])
        self.assertEqual(contract.lifecycle_returned_count, 1)

        history = contract._lifecycle_state_history().get(contract.id, [])
        entries = contract._lifecycle_history_entries(history)
        returned = [entry for entry in entries if entry['returned']]
        self.assertEqual(len(returned), 1)
        self.assertEqual(returned[0]['label'], 'Draft Request')
        self.assertTrue(entries[-1]['current'])

    def test_cancelled_contract_stops_where_it_stood(self):
        contract = self._make_contract()
        contract.action_submit_for_review()
        contract.action_cancel()

        stages, position = self._stages(contract)
        self.assertIsNone(position['index'], 'A cancelled contract is on no stage.')
        self.assertEqual(stages['draft']['status'], 'done')
        self.assertEqual(stages['legal_review']['status'], 'halted')
        self.assertEqual(stages['approval']['status'], 'pending')
        self.assertEqual(contract.lifecycle_stage_status, 'Cancelled')
        self.assertIn('Cancelled', contract.lifecycle_chart_html)

    # -------------------------------------------------------------------------
    # The current-stage panel
    # -------------------------------------------------------------------------

    def test_assignee_comes_from_the_open_workflow_task(self):
        contract = self._make_contract()
        contract.action_submit_for_review()

        task = contract._lifecycle_open_tasks()
        self.assertTrue(task, 'Entering Legal Review raises the Legal Review task.')
        self.assertEqual(
            contract.lifecycle_stage_assignee,
            task[0].assigned_user_id.display_name,
        )

    def test_assignee_falls_back_to_the_responsible_party(self):
        owner = new_test_user(self.env, login='clm_owner', name='Dana Owner')
        contract = self._make_contract(user_id=owner.id)
        self.assertFalse(contract._lifecycle_open_tasks())
        self.assertEqual(contract.lifecycle_stage_assignee, 'Dana Owner')

    def test_external_stage_is_assigned_to_the_counterparty(self):
        contract = self._make_contract()
        contract.action_submit_for_review()
        self._complete_open_tasks(contract)          # → internal_review_done
        contract.action_send_to_negotiation()
        contract.workflow_task_ids.filtered(
            lambda task: task.status in ('pending', 'in_progress')
        ).write({'status': 'cancelled'})

        self.assertEqual(contract.state, 'in_negotiation')
        self.assertEqual(contract.lifecycle_stage_assignee, 'Acme Corporation')
        # Start Negotiation must pass through Sent to Counterparty so the
        # lifecycle does not mark that stage as "Not used".
        stages, _position = self._stages(contract)
        self.assertEqual(stages['sent_to_counterparty']['status'], 'done')
        self.assertEqual(stages['negotiation']['status'], 'current')
        self.assertNotEqual(stages['sent_to_counterparty']['status'], 'skipped')

    def test_started_is_when_the_contract_entered_its_state(self):
        contract = self._make_contract()
        contract.action_submit_for_review()

        history = contract._lifecycle_state_history().get(contract.id, [])
        entered = [entry for entry in history if entry['state'] == 'in_review']
        self.assertTrue(entered)
        self.assertEqual(contract.lifecycle_stage_started, entered[-1]['date'])

    def test_progress_advances_with_the_contract(self):
        contract = self._make_contract()
        first = contract.lifecycle_progress
        contract.action_submit_for_review()
        contract.invalidate_recordset()
        self.assertGreater(contract.lifecycle_progress, first)

    # -------------------------------------------------------------------------
    # Rendering
    # -------------------------------------------------------------------------

    def test_chart_renders_every_stage(self):
        contract = self._make_contract()
        contract.action_submit_for_review()
        chart = contract.lifecycle_chart_html

        for stage in _STAGES:
            self.assertIn(stage['label'], chart)
        self.assertIn('o_clm_stage_current', chart)
        self.assertIn('o_clm_stage_done', chart)
        self.assertIn('o_clm_stage_pending', chart)

    def test_every_phase_and_stage_carries_its_own_icon(self):
        """A step is recognisable before its label is read."""
        contract = self._make_contract()
        phases = self._phases(contract)

        for phase in phases:
            self.assertTrue(phase['icon'].startswith('fa-'))
            for stage in phase['stages']:
                self.assertTrue(stage['icon'].startswith('fa-'))

        # Distinct icons, or they carry no information.
        phase_icons = [phase['icon'] for phase in phases]
        self.assertEqual(len(set(phase_icons)), len(phase_icons))
        stage_icons = [stage['icon'] for stage in _flatten(phases)]
        self.assertEqual(len(set(stage_icons)), len(stage_icons))

    def test_status_icons_say_complete_from_incomplete(self):
        """The step icon says what it is; the status icon says how far it got."""
        contract = self._make_contract()
        contract.action_submit_for_review()
        stages, _position = self._stages(contract)

        self.assertEqual(stages['draft']['status_icon'], 'fa-check-circle')
        self.assertEqual(stages['legal_review']['status_icon'], 'fa-dot-circle-o')
        self.assertEqual(stages['signing']['status_icon'], 'fa-circle-o')
        # …and the step icon is never overwritten by the status one.
        self.assertEqual(stages['draft']['icon'], 'fa-pencil-square-o')
        self.assertIn('fa-check-circle', contract.lifecycle_chart_html)

    def test_chart_offers_one_expand_collapse_toggle(self):
        contract = self._make_contract()
        chart = contract.lifecycle_chart_html

        # One toggle, not a command pair: it advertises its state through
        # aria-expanded and offers the action that state implies.
        self.assertEqual(
            chart.count('data-clm-lifecycle='), 1,
            'The chart offers exactly one expand/collapse control.',
        )
        self.assertIn('data-clm-lifecycle="toggle"', chart)
        self.assertIn('data-tooltip=', chart)

        # The button names itself in text, so it needs no aria-label, and the
        # wording it opens with has to match the cards the server just built.
        phases = self._phases(contract)
        all_expanded = bool(phases) and all(p['expanded'] for p in phases)
        self.assertIn(
            'aria-expanded="%s"' % ('true' if all_expanded else 'false'), chart,
        )
        self.assertIn('Collapse all' if all_expanded else 'Expand all', chart)

    def test_history_renders_the_audit_trail(self):
        contract = self._make_contract()
        contract.action_submit_for_review()
        history_html = contract.lifecycle_history_html

        self.assertIn('Draft Request', history_html)
        self.assertIn('Under Legal Review', history_html)
        self.assertIn(self.env.user.display_name, history_html)

    def test_change_assessment_is_read_from_the_versions(self):
        contract = self._make_contract()
        self.env['clm.contract.version'].create({
            'contract_id': contract.id,
            'version_type': 'negotiated_draft',
            'change_classification': 'ok_to_proceed',
        })
        contract.invalidate_recordset()
        self.assertIn('OK to Proceed', contract.lifecycle_chart_html)

    def test_default_change_classification_is_not_reported(self):
        """'No Material Change' is the default on every version — it says nothing."""
        contract = self._make_contract()
        self.env['clm.contract.version'].create({
            'contract_id': contract.id,
            'version_type': 'internal_draft',
        })
        contract.invalidate_recordset()
        self.assertNotIn('No Material Change', contract.lifecycle_chart_html)

    def test_lifecycle_form_view_loads(self):
        view = self.env.ref('clm.view_clm_contract_lifecycle_form')
        arch = self.env['clm.contract'].get_view(view.id, 'form')['arch']
        self.assertIn('lifecycle_chart_html', arch)
        self.assertIn('lifecycle_history_html', arch)
        self.assertNotIn('widget="statusbar"', arch)

    def test_contracts_list_offers_view_lifecycle(self):
        arch = self.env['clm.contract'].get_view(
            self.env.ref('clm.view_clm_contract_list').id, 'list',
        )['arch']
        self.assertIn('action_open_lifecycle', arch)
        self.assertIn('View Lifecycle', arch)

    # -------------------------------------------------------------------------
    # Actions
    # -------------------------------------------------------------------------

    def test_action_open_lifecycle_targets_this_contract(self):
        contract = self._make_contract()
        action = contract.action_open_lifecycle()
        view = self.env.ref('clm.view_clm_contract_lifecycle_form')

        self.assertEqual(action['res_model'], 'clm.contract')
        self.assertEqual(action['res_id'], contract.id)
        self.assertEqual(action['views'], [(view.id, 'form')])

    def test_action_open_contract_record_returns_to_the_form(self):
        contract = self._make_contract()
        action = contract.action_open_contract_record()
        self.assertEqual(
            action['views'],
            [(self.env.ref('clm.view_clm_contract_form').id, 'form')],
        )

    # -------------------------------------------------------------------------
    # Permissions
    # -------------------------------------------------------------------------

    def test_overview_is_readable_by_every_clm_role(self):
        """The overview must not need read access on clm.contract.version.

        Only Business Requester, Legal Reviewer and Legal Admin have that;
        Read-Only, Approver, Finance and Procurement do not, and they can all
        read contracts.
        """
        contract = self._make_contract(confidentiality_level='internal')
        fields_to_read = [
            'lifecycle_stage_label', 'lifecycle_stage_status',
            'lifecycle_stage_assignee', 'lifecycle_position',
            'lifecycle_chart_html', 'lifecycle_history_html',
            'lifecycle_final_stored', 'final_contract_available',
            'final_contract_status', 'version_count',
        ]
        for group in ('group_clm_read_only', 'group_clm_manager',
                      'group_clm_finance', 'group_clm_procurement',
                      'group_clm_user', 'group_clm_legal'):
            user = new_test_user(
                self.env, login='clm_role_%s' % group,
                groups='base.group_user,clm.%s' % group,
            )
            record = contract.with_user(user)
            record.invalidate_recordset()
            values = record.read(fields_to_read)
            self.assertEqual(
                values[0]['lifecycle_stage_label'], 'Draft',
                'Role %s must be able to read the lifecycle overview.' % group,
            )

    def test_lifecycle_open_tasks_readable_by_legal_admin_who_is_not_assignee(self):
        """View Lifecycle must not AccessError when open tasks belong to someone else.

        Legal Admin implies Business Requester, whose record rule scopes tasks
        to the assignee. Without a Legal Admin rule, computing
        ``lifecycle_open_task_ids`` failed with AccessError.
        """
        assignee = new_test_user(
            self.env, login='clm_task_assignee',
            groups='base.group_user,clm.group_clm_legal',
        )
        other_admin = new_test_user(
            self.env, login='clm_other_legal_admin',
            groups='base.group_user,clm.group_clm_legal_admin',
        )
        contract = self._make_contract()
        contract.action_submit_for_review()
        open_tasks = contract._lifecycle_open_tasks()
        self.assertTrue(open_tasks)
        open_tasks.write({
            'assigned_user_id': assignee.id,
            'original_assigned_user_id': assignee.id,
        })

        record = contract.with_user(other_admin)
        record.invalidate_recordset(['lifecycle_open_task_ids', 'workflow_task_ids'])
        values = record.read(['lifecycle_open_task_ids', 'lifecycle_stage_assignee'])
        self.assertEqual(set(values[0]['lifecycle_open_task_ids']), set(open_tasks.ids))
        self.assertEqual(values[0]['lifecycle_stage_assignee'], assignee.name)

    def test_lifecycle_open_tasks_hidden_not_crashing_for_non_assignee_requester(self):
        """Requesters who are not the assignee still open View Lifecycle safely."""
        assignee = new_test_user(
            self.env, login='clm_task_owner',
            groups='base.group_user,clm.group_clm_legal',
        )
        requester = new_test_user(
            self.env, login='clm_lifecycle_requester',
            groups='base.group_user,clm.group_clm_user',
        )
        contract = self._make_contract(
            user_id=requester.id, confidentiality_level='internal',
        )
        contract.action_submit_for_review()
        open_tasks = contract._lifecycle_open_tasks()
        self.assertTrue(open_tasks)
        open_tasks.write({
            'assigned_user_id': assignee.id,
            'original_assigned_user_id': assignee.id,
        })

        record = contract.with_user(requester)
        record.invalidate_recordset(['lifecycle_open_task_ids', 'workflow_task_ids'])
        values = record.read(['lifecycle_open_task_ids', 'lifecycle_stage_assignee'])
        self.assertFalse(values[0]['lifecycle_open_task_ids'])
        self.assertEqual(values[0]['lifecycle_stage_assignee'], assignee.name)
