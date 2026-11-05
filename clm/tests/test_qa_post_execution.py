# Part of NextContract. See LICENSE file for full copyright and licensing details.
"""QA phase 16: renewal, termination, archive, expiry processing, alerts."""
import datetime

from odoo.exceptions import UserError
from odoo.tests.common import tagged

from . import test_qa_lifecycle_security as qa

TODAY = datetime.date.today()


@tagged('post_install', '-at_install', 'clm_qa')
class TestPostExecution(qa.TestClmQaBase):

    def _active(self, **vals):
        base = {
            'name': 'QA Active Contract', 'contract_type_id': self.nda.id,
            'partner_id': self.partner.id, 'state': 'active',
            'user_id': self.requester.id, 'legal_owner_id': self.legal.id,
            'effective_date': TODAY - datetime.timedelta(days=300),
            'expiry_date': TODAY + datetime.timedelta(days=65),
            'notice_period_days': 30,
        }
        base.update(vals)
        contract = self.env['clm.contract'].with_context(migration_mode=True).create(base)
        return contract.with_context(migration_mode=False)

    def _renew_wizard(self, contract, **vals):
        values = {'contract_id': contract.id, 'effective_date': TODAY,
                  'expiry_date': TODAY + datetime.timedelta(days=365)}
        values.update(vals)
        return self.env['clm.contract.renew.wizard'].with_user(self.admin).create(values)

    # -- renewal ---------------------------------------------------------------
    def test_renewal_creates_a_linked_active_successor(self):
        contract = self._active()
        contract._create_default_alerts()
        self._renew_wizard(contract, renewal_mode='successor').action_confirm_renew()
        successor = self.env['clm.contract'].search([('parent_contract_id', '=', contract.id)])
        self.assertEqual(len(successor), 1)
        self.assertEqual(contract.state, 'renewed')
        self.assertEqual(successor.state, 'active')
        self.assertEqual(successor.partner_id, contract.partner_id)
        self.assertEqual(successor.effective_date, TODAY)
        self.assertEqual(successor.expiry_date, TODAY + datetime.timedelta(days=365))
        self.assertFalse(contract.alert_ids.filtered('active'), 'old alerts are switched off')
        self.assertTrue(successor.alert_ids, 'successor gets its own expiry/renewal alerts')

    def test_in_place_renewal_updates_the_dates(self):
        contract = self._active()
        self._renew_wizard(contract, renewal_mode='in_place').action_confirm_renew()
        self.assertEqual(contract.state, 'active')
        self.assertEqual(contract.expiry_date, TODAY + datetime.timedelta(days=365))
        self.assertEqual(contract.renewal_date, TODAY)

    def test_renewal_rejects_inverted_dates_and_missing_expiry(self):
        contract = self._active()
        with self.assertRaises(UserError):
            self._renew_wizard(
                contract, effective_date=TODAY,
                expiry_date=TODAY - datetime.timedelta(days=1)).action_confirm_renew()
        with self.assertRaises(UserError):
            self._renew_wizard(contract, expiry_date=False).action_confirm_renew()

    def test_only_active_contracts_can_be_renewed(self):
        draft = self._contract()
        with self.assertRaises(UserError):
            draft.with_user(self.admin).action_open_renew_wizard()

    # -- termination / archive -------------------------------------------------
    def test_termination_needs_confirmation_and_records_reason(self):
        contract = self._active()
        contract._create_default_alerts()
        wizard = self.env['clm.contract.terminate.wizard'].with_user(self.admin).create({
            'contract_id': contract.id, 'reason': 'Breach', 'confirm': False})
        with self.assertRaises(UserError):
            wizard.action_confirm_terminate()
        wizard.confirm = True
        wizard.action_confirm_terminate()
        self.assertEqual(contract.state, 'terminated')
        self.assertEqual(contract.termination_reason, 'Breach')
        self.assertTrue(contract.termination_date)
        self.assertFalse(contract.alert_ids.filtered('active'))

    def test_archived_contract_is_terminal(self):
        contract = self._active(state='expired')
        self.env['clm.contract.archive.wizard'].with_user(self.admin).create({
            'contract_id': contract.id, 'reason': 'Retention', 'confirm': True,
        }).action_confirm_archive()
        self.assertEqual(contract.state, 'archived')
        self.assertTrue(contract.archived_date)
        for target in ('active', 'draft', 'renewed', 'terminated'):
            with self.assertRaises(UserError, msg=target):
                contract.write({'state': target})

    def test_draft_contract_cannot_be_archived(self):
        draft = self._contract()
        with self.assertRaises(UserError):
            draft.with_user(self.admin).action_open_archive_wizard()

    # -- expiry processing -----------------------------------------------------
    def test_cron_moves_contract_to_expiring_soon_then_expired(self):
        soon = self._active(expiry_date=TODAY + datetime.timedelta(days=10))
        past = self._active(name='Past', expiry_date=TODAY - datetime.timedelta(days=1))
        far = self._active(name='Far', expiry_date=TODAY + datetime.timedelta(days=200))
        self.env['clm.contract']._cron_process_contract_alerts()
        self.assertEqual(soon.state, 'expiring_soon')
        self.assertEqual(past.state, 'expired')
        self.assertEqual(far.state, 'active')

    def test_cron_expires_even_when_auto_renewal_is_flagged(self):
        """OBS-04 (characterisation): `auto_renewal` has no automation behind it.

        A contract flagged Auto-Renewal is simply expired by the cron when its
        date passes. If automatic renewal is intended this is a functional gap;
        this test documents the behaviour so a change is a conscious one.
        """
        contract = self._active(
            auto_renewal=True, expiry_date=TODAY - datetime.timedelta(days=1))
        self.env['clm.contract']._cron_process_contract_alerts()
        self.assertEqual(contract.state, 'expired')

    def test_alert_inside_its_lead_window_notifies_the_legal_owner(self):
        contract = self._active(expiry_date=TODAY + datetime.timedelta(days=20))
        contract._create_default_alerts()
        fired = contract._process_contract_alerts(force=True)
        self.assertTrue(fired, 'the 90-day expiry alert is inside its window')
        activities = self.env['mail.activity'].sudo().search([
            ('res_model', '=', 'clm.contract'), ('res_id', '=', contract.id)])
        self.assertTrue(activities)
        self.assertEqual(activities.mapped('user_id'), self.legal)
        self.assertTrue(contract.alert_ids.mapped('last_notified'))

    def test_alert_outside_its_window_does_not_fire(self):
        contract = self._active(expiry_date=TODAY + datetime.timedelta(days=400))
        contract._create_default_alerts()
        self.assertFalse(contract._process_contract_alerts(force=False))


@tagged('post_install', '-at_install', 'clm_qa')
class TestViews(qa.TestClmQaBase):
    """UI structure checks that need no browser."""

    FORM_MODELS = (
        'clm.contract', 'clm.contract.request', 'clm.contract.version', 'clm.clause',
        'clm.template', 'clm.workflow.task', 'clm.workflow.rule', 'clm.obligation',
        'clm.risk.assessment', 'clm.negotiation.round', 'clm.document.comparison',
        'clm.contract.type', 'clm.audit.event', 'clm.migration.batch',
    )

    def test_every_clm_form_and_list_renders(self):
        # clm.migration.batch comes from the optional clm_migration add-on.
        for model in (m for m in self.FORM_MODELS if m in self.env):
            for view_type in ('form', 'list'):
                with self.subTest(model=model, view=view_type):
                    self.env[model].get_view(view_type=view_type)

    def test_contract_form_has_no_duplicate_tab(self):
        """BUG-21: two notebook pages were both named `signature` (same title)."""
        from lxml import etree
        arch = etree.fromstring(self.env['clm.contract'].get_view(view_type='form')['arch'])
        names = [page.get('name') for page in arch.xpath('//notebook/page') if page.get('name')]
        duplicates = sorted({n for n in names if names.count(n) > 1})
        self.assertFalse(duplicates, 'Duplicate tabs on the contract form: %s' % duplicates)

    def test_contract_form_renders_for_every_role(self):
        for user in (self.requester, self.legal, self.admin, self.approver, self.readonly):
            with self.subTest(user=user.login):
                self.env['clm.contract'].with_user(user).get_view(view_type='form')
