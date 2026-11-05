# Part of NextContract. See LICENSE file for full copyright and licensing details.

import io

from odoo.tests.common import TransactionCase

from ..services.pdf_compare_highlight import (
    COLOR_ADD,
    COLOR_DELETE,
    annotate_pdf_blocks,
    annotate_pdf_highlights,
    diff_pdf_changes,
    phrases_from_comparison_result,
    phrases_from_pdf_bytes,
)


class TestPdfCompareHighlight(TransactionCase):

    def _sample_pdf(self, *lines):
        from reportlab.pdfgen import canvas
        from reportlab.lib.pagesizes import letter

        buf = io.BytesIO()
        pdf = canvas.Canvas(buf, pagesize=letter)
        y = 700
        for line in lines:
            pdf.drawString(72, y, line)
            y -= 18
        pdf.save()
        return buf.getvalue()

    def test_annotate_marks_changed_text_in_pdf(self):
        raw = self._sample_pdf('The Client will pay within 20 days.')
        marked = annotate_pdf_highlights(raw, ['20 days'], color=COLOR_DELETE)
        self.assertTrue(marked.startswith(b'%PDF'))
        self.assertNotEqual(marked, raw)

    def test_phrases_split_removed_and_added(self):
        result = {
            'hunks': [{
                'rows': [
                    {'kind': 'del', 'text': 'pay within 20 days'},
                    {'kind': 'add', 'text': 'pay within 30 days'},
                ],
            }],
            'changes': [{
                'removed': ['20'],
                'added': ['30'],
            }],
        }
        removed, added = phrases_from_comparison_result(result)
        self.assertTrue(any('20' in p for p in removed))
        self.assertTrue(any('30' in p for p in added))
        self.assertFalse(any(p.lower() == 'pay within' for p in removed))
        self.assertFalse(any(p.lower() == 'pay within' for p in added))

    def test_shared_branding_not_marked_on_both_sides(self):
        base = self._sample_pdf(
            'NEXTDOCS',
            'SOFTWARE / SAAS',
            'Software License and Agreement',
            'Pay within 20 days.',
        )
        target = self._sample_pdf(
            'NEXTDOCS',
            'SOFTWARE / SAAS',
            'Software License and Agreement',
            'Pay within 30 days.',
        )
        removed, added = phrases_from_pdf_bytes(base, target)
        for phrase in removed + added:
            lower = phrase.lower()
            self.assertNotIn('nextdocs', lower)
            self.assertNotEqual(lower, 'software')
            self.assertNotEqual(lower, 'software license')
        self.assertTrue(any('20' in p for p in removed))
        self.assertTrue(any('30' in p for p in added))

    def test_full_deleted_clause_is_one_contiguous_block(self):
        base = self._sample_pdf(
            'Limitation of Liability',
            'To the maximum extent permitted by law, neither Party is liable for punitive damages.',
            'Governing Law and Disputes',
            'Force Majeure',
            'Neither Party is liable for delay caused by natural disaster, war, or epidemic.',
            'If the delay continues more than sixty days, either Party may terminate by written notice.',
            '4. Notices and Contact Details',
        )
        target = self._sample_pdf(
            'Limitation of Liability',
            'To the maximum extent permitted by law, neither Party is liable for punitive damages.',
            'Governing Law and Disputes',
            '4. Notices and Contact Details',
        )
        removed_blocks, added_blocks, rem_phrases, add_phrases = diff_pdf_changes(base, target)
        self.assertTrue(removed_blocks, 'expected a deleted Force Majeure block')
        block = max(removed_blocks, key=len)
        joined = ' '.join(block)
        self.assertIn('Force Majeure', joined)
        self.assertIn('Neither Party is liable for delay', joined)
        self.assertIn('written notice', joined)
        # Shared Limitation wording must not become a free-floating delete phrase.
        self.assertFalse(any(
            'punitive damages' in p.lower() for p in rem_phrases
        ))

        marked = annotate_pdf_blocks(base, removed_blocks, color=COLOR_DELETE)
        self.assertTrue(marked.startswith(b'%PDF'))
        self.assertNotEqual(marked, base)

    def test_green_highlight_produces_marked_pdf(self):
        raw = self._sample_pdf('The Client will pay within 30 days.')
        marked = annotate_pdf_highlights(raw, ['30 days'], color=COLOR_ADD)
        self.assertTrue(marked.startswith(b'%PDF'))
        self.assertNotEqual(marked, raw)
