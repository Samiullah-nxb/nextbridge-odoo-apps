# Part of NextContract. See LICENSE file for full copyright and licensing details.
"""
AI Engine Abstraction Layer

Provides the interface for field extraction and clause analysis.
Phase 6A provides MockAIEngine.
Phase 6B will provide real adapters like GeminiAIEngine.
"""

class AIEngine:
    """Base interface for AI capabilities."""

    # Does this engine consume raw documents (page images / PDF bytes), or
    # does it need text? The orchestrator reads this to decide which payload
    # shape to request from DocumentReader. Engines never choose for
    # themselves and never read documents directly.
    accepts_documents = False

    # Does this engine transmit content to a third party? Cloud engines are
    # gated on company consent in engine_factory.build_engine().
    is_cloud = False

    def extract_fields(self, job, payload=None):
        """
        Extract contract metadata (Title, Value, Dates) from the source document.
        Must create clm.ai.field.suggestion records linked to the job.
        """
        raise NotImplementedError

    def analyze_clauses(self, job):
        """
        Segment document into clauses, match against library, and classify deviations.
        Must create clm.clause.deviation records linked to the job.
        """
        raise NotImplementedError

    def _create_suggestion(self, job, values):
        values['job_id'] = job.id
        return job.env['clm.ai.field.suggestion'].create(values)

    def _create_deviation(self, job, values):
        values['job_id'] = job.id
        values['version_id'] = job.contract_version_id.id
        return job.env['clm.clause.deviation'].create(values)


class MockAIEngine(AIEngine):
    """
    Mock implementation for Phase 6A to test review workflows and architecture
    without requiring active API credentials.
    """
    
    def __init__(self, env):
        self.env = env
    
    def extract_fields(self, job):
        # Simulate creating field suggestions
        suggestions = [
            {
                'job_id': job.id,
                'field_name': 'name',
                'suggested_value': 'Master Services Agreement (AI Suggested)',
                'confidence': 98.5,
                'source_text': 'This MASTER SERVICES AGREEMENT...',
                'source_page': 1
            },
            {
                'job_id': job.id,
                'field_name': 'effective_date',
                'suggested_value': '2027-01-01',
                'confidence': 96.0,
                'source_text': '...shall be effective as of January 1st, 2027.',
                'source_page': 1
            },
            {
                'job_id': job.id,
                'field_name': 'expiry_date',
                'suggested_value': '2029-12-31',
                'confidence': 72.0,  # Lower confidence to simulate review need
                'source_text': '...for a term of three years ending Dec 31, 2029.',
                'source_page': 2
            }
        ]
        for suggestion in suggestions:
            self._create_suggestion(job, suggestion)

    def analyze_clauses(self, job):
        # Simulate analyzing clauses and creating deviations
        # We find a random published clause to link against for the mock
        clause = job.env['clm.clause'].search([('state', '=', 'published')], limit=1)
        
        deviations = [
            {
                'job_id': job.id,
                'version_id': job.contract_version_id.id,
                'clause_id': clause.id if clause else False,
                'matched_text': 'The provider limits liability to 100% of fees paid.',
                'ai_deviation_type': 'non_standard',
                'ai_risk_level': 'high',
                'ai_confidence': 92.5,
                'explanation': 'The limitation of liability cap is significantly lower than standard playbook.',
                'status': 'pending',
            },
            {
                'job_id': job.id,
                'version_id': job.contract_version_id.id,
                'clause_id': False,
                'matched_text': 'Disputes shall be resolved in Geneva, Switzerland.',
                'ai_deviation_type': 'modified',
                'ai_risk_level': 'medium',
                'ai_confidence': 88.0,
                'explanation': 'Non-standard jurisdiction identified.',
                'status': 'pending',
            }
        ]
        for deviation in deviations:
            self._create_deviation(job, deviation)
