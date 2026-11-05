# Part of NextContract. See LICENSE file for full copyright and licensing details.
"""
Tests for the contract text parser.

The parser recovers structure from a contract's own text. Its two failure
modes both matter: missing a real heading buries a clause, and inventing one
splits a clause in half. These tests pin both directions.
"""

from odoo.tests.common import TransactionCase

from odoo.addons.clm.services.contract_text_parser import parse_contract_text


class TestContractTextParser(TransactionCase):

    @staticmethod
    def _headings(sections):
        return [section['heading'] for section in sections]

    @staticmethod
    def _text(sections):
        return ' '.join(
            block['text'] for section in sections for block in section['blocks']
        )

    def test_numbered_headings_are_recovered(self):
        sections = parse_contract_text(
            '1. Definitions\n'
            'In this Agreement the following terms apply.\n'
            '2. Term and Renewal\n'
            'This Agreement runs for twelve months.\n'
        )
        self.assertEqual(
            self._headings(sections), ['1. Definitions', '2. Term and Renewal']
        )

    def test_nested_numbering_sets_the_level(self):
        sections = parse_contract_text(
            '1. Liability\nGeneral position.\n'
            '1.1 Cap on Liability\nLiability is capped.\n'
            '1.1.2 Carve-outs\nFraud is excluded.\n'
        )
        self.assertEqual([section['level'] for section in sections], [1, 2, 3])

    def test_article_and_schedule_headings(self):
        sections = parse_contract_text(
            'ARTICLE IV — Confidentiality\nThe parties shall keep secrets.\n'
            'SCHEDULE 2: Fees\nFees are payable monthly.\n'
        )
        self.assertEqual(
            self._headings(sections),
            ['ARTICLE IV — Confidentiality', 'SCHEDULE 2 — Fees'],
        )

    def test_hard_wrapped_lines_become_one_paragraph(self):
        sections = parse_contract_text(
            '1. Payment\n'
            'The Customer shall pay each invoice within thirty (30)\n'
            'days of receipt, without set-off or deduction.\n'
        )
        self.assertIn(
            'within thirty (30) days of receipt', self._text(sections)
        )

    def test_words_split_across_lines_are_rejoined(self):
        sections = parse_contract_text(
            '1. Confidentiality\nThe Receiving Party shall protect all infor-\nmation disclosed.\n'
            '2. Term\nOne year.\n'
        )
        self.assertIn('information disclosed', self._text(sections))

    def test_lettered_sub_paragraphs_become_list_items(self):
        sections = parse_contract_text(
            '1. Services\nThe Supplier shall provide:\n'
            '(a) installation;\n'
            '(b) maintenance.\n'
            '2. Fees\nAs per Schedule 1.\n'
        )
        services = sections[0]
        kinds = [block['kind'] for block in services['blocks']]
        self.assertEqual(kinds.count('li'), 2)
        self.assertIn('installation', services['blocks'][1]['text'])

    def test_a_shouted_clause_is_not_mistaken_for_a_heading(self):
        """Legal drafting shouts disclaimers; they are body text, not titles."""
        sections = parse_contract_text(
            '10. Warranty\n'
            'NO WARRANTIES ARE MADE BY EITHER PARTY UNDER THIS\n'
            'AGREEMENT WHATSOEVER. The parties acknowledge the position.\n'
            '11. Miscellaneous\n'
            'Headings are for reference only.\n'
        )
        self.assertEqual(
            self._headings(sections), ['10. Warranty', '11. Miscellaneous']
        )
        self.assertIn('NO WARRANTIES ARE MADE', self._text(sections))

    def test_document_title_in_capitals_is_a_heading(self):
        sections = parse_contract_text(
            'NON-DISCLOSURE AGREEMENT\n'
            'THIS AGREEMENT is entered into between the parties.\n'
            '1. Definitions\nTerms have their usual meaning.\n'
        )
        self.assertEqual(sections[0]['heading'], 'NON-DISCLOSURE AGREEMENT')

    def test_recitals_are_labelled_from_their_own_wording(self):
        sections = parse_contract_text(
            'THIS AGREEMENT is made between Alpha Ltd and Beta Ltd, who agree as follows:\n'
            '1. Definitions\nTerms have their usual meaning.\n'
            '2. Term\nTwelve months.\n'
        )
        self.assertEqual(sections[0]['heading'], 'Recitals')

    def test_a_numbered_sentence_is_not_a_heading(self):
        sections = parse_contract_text(
            '1. Definitions\n'
            'Terms apply as follows.\n'
            '2. The Receiving Party shall not disclose any Confidential Information '
            'to any third party without prior written consent of the Disclosing Party.\n'
            '3. Term\nOne year.\n'
        )
        self.assertNotIn(
            '2. The Receiving Party shall not disclose any Confidential Information',
            self._headings(sections),
        )

    def test_unstructured_text_is_left_unstructured(self):
        sections = parse_contract_text(
            'This letter confirms our understanding regarding the supply of goods. '
            'Payment is due on delivery.\n'
        )
        self.assertEqual(len(sections), 1)
        self.assertEqual(sections[0]['heading'], '')
        self.assertIn('supply of goods', self._text(sections))

    def test_page_furniture_is_dropped(self):
        sections = parse_contract_text(
            '1. Definitions\nTerms apply.\n'
            'Page 1 of 3\n'
            'Acme Master Services Agreement\n'
            '2. Term\nOne year.\n'
            'Page 2 of 3\n'
            'Acme Master Services Agreement\n'
            '3. Fees\nMonthly.\n'
            'Page 3 of 3\n'
            'Acme Master Services Agreement\n'
        )
        body = self._text(sections)
        self.assertNotIn('Page 1 of 3', body)
        self.assertNotIn('Acme Master Services Agreement', body)
        self.assertEqual(
            self._headings(sections), ['1. Definitions', '2. Term', '3. Fees']
        )

    def test_empty_text_is_handled(self):
        self.assertEqual(parse_contract_text(''), [])
        self.assertEqual(parse_contract_text(None), [])
