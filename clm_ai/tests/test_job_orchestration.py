# Part of NextContract. See LICENSE file for full copyright and licensing details.

from odoo.tests.common import TransactionCase


class TestJobOrchestration(TransactionCase):

    def setUp(self):
        super().setUp()
        self.company = self.env.company
        self.contract = self.env['clm.contract'].create({
            'name': 'Orchestration Contract',
            'contract_type_id': self.env.ref('clm.contract_type_nda').id,
            'company_id': self.company.id,
        })
        self.version = self.env['clm.contract.version'].create({
            'contract_id': self.contract.id,
            'version_type': 'internal_draft', 'version_number': 1,
        })

    def _job(self, provider='mock'):
        return self.env['clm.ai.job'].create({
            'contract_version_id': self.version.id,
            'analysis_type': 'field_extraction',
            'provider': provider,
        })

    def test_new_job_starts_pending_review(self):
        self.assertEqual(self._job().review_state, 'pending_review')

    def test_execution_and_review_states_are_independent(self):
        job = self._job()
        job.action_run_sync()
        self.assertEqual(job.status, 'completed')
        self.assertEqual(job.review_state, 'pending_review')

    def test_local_engine_records_no_cloud_consent(self):
        job = self._job()
        job.action_run_sync()
        self.assertFalse(job.cloud_processing_consent)

    def test_cloud_job_without_consent_fails_with_reason(self):
        """Refusal must be recorded, not just raised."""
        self.company.revoke_cloud_ai_processing()
        job = self._job(provider='gemini')
        job.action_run_sync()
        self.assertEqual(job.status, 'failed')
        self.assertEqual(job.failure_reason, 'consent_missing')

    def test_failure_is_isolated_to_its_own_job(self):
        self.company.revoke_cloud_ai_processing()
        failing = self._job(provider='gemini')
        healthy = self._job()
        failing.action_run_sync()
        healthy.action_run_sync()
        self.assertEqual(failing.status, 'failed')
        self.assertEqual(healthy.status, 'completed')

    def test_rerun_supersedes_previous_pending_suggestions(self):
        first = self._job()
        first.action_run_sync()
        self.assertTrue(first.suggestion_ids)

        second = first.action_rerun_extraction()

        self.assertTrue(all(
            s.status == 'superseded' for s in first.suggestion_ids))
        self.assertTrue(all(
            s.superseded_by_job_id == second for s in first.suggestion_ids))

    def test_superseded_run_is_not_reviewable(self):
        """A superseded run must not sit in the queue looking actionable."""
        first = self._job()
        first.action_run_sync()
        first.action_rerun_extraction()
        self.assertFalse(first.is_reviewable)

    def test_run_with_pending_suggestions_is_reviewable(self):
        job = self._job()
        job.action_run_sync()
        self.assertTrue(job.is_reviewable)

    def test_cron_claims_each_job_once(self):
        jobs = self._job() + self._job()
        # TransactionCase forbids a real cr.commit() (it would break the
        # test's own rollback). The cron relies on commit()'s *flush* to make
        # each job's new status visible to the next claim query, though — a
        # bare no-op stub leaves that write stranded in the ORM cache, so the
        # next SELECT re-claims the same still-'pending'-in-DB row and the
        # second job is never reached. Stub to flush() only: it pushes the
        # ORM write to real SQL (visible within this transaction) without
        # issuing the forbidden Postgres COMMIT.
        original_commit = self.env.cr.commit
        self.env.cr.commit = self.env.cr.flush
        self.addCleanup(setattr, self.env.cr, 'commit', original_commit)
        self.env['clm.ai.job']._cron_process_pending_jobs()
        for job in jobs:
            self.assertIn(job.status, ('completed', 'failed'))

    def test_full_analysis_survives_unreadable_clause_analysis(self):
        """A scanned/unsupported document must not fail the whole 'full'
        job just because legacy clause analysis can't read that format --
        the field extraction results it already produced must survive."""
        from odoo.addons.clm_ai.services.gemini_engine import UnreadableDocument
        from odoo.addons.clm_ai.services import engine_factory

        job = self.env['clm.ai.job'].create({
            'contract_version_id': self.version.id,
            'analysis_type': 'full', 'provider': 'mock',
        })

        original_build_engine = engine_factory.build_engine

        def patched_build_engine(env, provider, company):
            engine = original_build_engine(env, provider, company)
            engine.analyze_clauses = lambda self_job: (_ for _ in ()).throw(
                UnreadableDocument("no text layer"))
            return engine

        import unittest.mock as mock
        with mock.patch.object(engine_factory, 'build_engine', patched_build_engine):
            job.action_run_sync()

        self.assertEqual(job.status, 'completed')
        self.assertTrue(job.suggestion_ids)

    def test_retry_does_not_duplicate_snapshots_or_suggestions(self):
        job = self._job()
        job.action_run_sync()
        self.assertEqual(job.status, 'completed')
        first_snapshot_count = len(job.field_snapshot_ids)
        first_suggestion_count = len(job.suggestion_ids)
        self.assertTrue(first_snapshot_count)
        self.assertTrue(first_suggestion_count)

        job.status = 'failed'
        job.action_run_sync()

        self.assertEqual(job.status, 'completed')
        self.assertEqual(len(job.field_snapshot_ids), first_snapshot_count)
        self.assertEqual(len(job.suggestion_ids), first_suggestion_count)
