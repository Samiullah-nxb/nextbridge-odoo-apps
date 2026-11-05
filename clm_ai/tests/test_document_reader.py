# Part of NextContract. See LICENSE file for full copyright and licensing details.

from odoo.tests.common import TransactionCase
from odoo.exceptions import UserError

from .fixtures import build_docx, build_text_pdf, build_scanned_pdf, attach


class TestDocumentReader(TransactionCase):

    def test_reads_docx_text_natively(self):
        from odoo.addons.clm.services.document_reader import resolve_reader
        attachment = attach(
            self.env,
            build_docx(['Effective Date: 2026-01-01', 'Governing law: England.']),
            'agreement.docx',
            'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        )
        payload = resolve_reader(self.env, attachment).read(attachment)
        self.assertIn('Effective Date: 2026-01-01', payload.text)
        self.assertEqual(payload.text_source, 'native')

    def test_reads_pdf_text_layer_natively(self):
        from odoo.addons.clm.services.document_reader import resolve_reader
        attachment = attach(
            self.env, build_text_pdf('Contract Value: 1250000'),
            'agreement.pdf', 'application/pdf',
        )
        payload = resolve_reader(self.env, attachment).read(attachment)
        self.assertIn('1250000', payload.text)
        self.assertEqual(payload.text_source, 'native')
        self.assertEqual(payload.page_count, 1)

    def test_scanned_pdf_yields_no_text_without_ocr(self):
        """A scan has no characters. Report that honestly rather than ''."""
        from odoo.addons.clm.services.document_reader import resolve_reader
        attachment = attach(
            self.env, build_scanned_pdf(), 'scan.pdf', 'application/pdf',
        )
        payload = resolve_reader(self.env, attachment).read(attachment)
        self.assertEqual(payload.text_source, 'none')
        self.assertFalse(payload.text)

    def test_rasterises_pages_when_images_requested(self):
        from odoo.addons.clm.services.document_reader import resolve_reader
        attachment = attach(
            self.env, build_scanned_pdf(), 'scan.pdf', 'application/pdf',
        )
        reader = resolve_reader(self.env, attachment, want_images=True)
        payload = reader.read(attachment, want_images=True)
        self.assertEqual(len(payload.pages), 1)
        self.assertEqual(payload.pages[0].page_number, 1)
        self.assertEqual(payload.pages[0].mimetype, 'image/png')
        self.assertTrue(payload.pages[0].image)

    def test_no_pages_rendered_when_images_not_requested(self):
        """Rasterising is expensive; never do it speculatively."""
        from odoo.addons.clm.services.document_reader import resolve_reader
        attachment = attach(
            self.env, build_text_pdf('Hello'), 'agreement.pdf', 'application/pdf',
        )
        payload = resolve_reader(self.env, attachment).read(attachment)
        self.assertEqual(payload.pages, [])

    def test_unsupported_mimetype_raises(self):
        from odoo.addons.clm.services.document_reader import resolve_reader
        attachment = attach(self.env, b'AAAA', 'thing.zip', 'application/zip')
        with self.assertRaises(UserError):
            resolve_reader(self.env, attachment)

    def test_version_records_text_layer_state(self):
        contract = self.env['clm.contract'].create({
            'name': 'Reader Contract',
            'contract_type_id': self.env.ref('clm.contract_type_nda').id,
        })
        version = self.env['clm.contract.version'].create({
            'contract_id': contract.id,
            'version_type': 'internal_draft',
            'version_number': 1,
        })
        self.assertEqual(version.text_layer_status, 'unavailable')
        version.write({
            'extracted_text': 'Effective Date: 2026-01-01',
            'text_source': 'native',
            'text_layer_status': 'available',
            'page_count': 3,
        })
        self.assertEqual(version.text_source, 'native')
