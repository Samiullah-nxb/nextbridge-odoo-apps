# Part of NextContract. See LICENSE file for full copyright and licensing details.

import base64
import io
import re

from odoo.tests.common import HttpCase, tagged

_DOCX_MIME = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'


def _real_docx(text='Counterparty revision'):
    import docx
    buf = io.BytesIO()
    document = docx.Document()
    document.add_paragraph(text)
    document.save(buf)
    return buf.getvalue()

@tagged('post_install', '-at_install')
class TestPortalUpload(HttpCase):
    
    def setUp(self):
        super().setUp()
        self.contract_type_nda = self.env.ref('clm.contract_type_nda')
        
        # Create portal user
        self.portal_user = self.env['res.users'].create({
            'name': 'Portal User',
            'login': 'portal_user@test.com',
            'password': 'password',
            'group_ids': [(6, 0, [self.env.ref('base.group_portal').id])],
        })
        self.partner = self.portal_user.partner_id
        
        # Create contract
        self.contract = self.env['clm.contract'].with_context(migration_mode=True).create({
            'name': 'Negotiation Test Contract',
            'contract_type_id': self.contract_type_nda.id,
            'partner_id': self.partner.id,
            'state': 'in_negotiation',
        })
        
        # Grant portal access
        self.env['clm.contract.portal.access'].create({
            'contract_id': self.contract.id,
            'partner_id': self.partner.id,
            'can_view': True,
            'can_upload': True,
        })
        
        # Create outbound version
        self.outbound_version = self.env['clm.contract.version'].create({
            'contract_id': self.contract.id,
            'version_type': 'negotiated_draft',
            'change_summary': 'Initial outbound version',
        })
        
        # Create active negotiation round
        self.round = self.env['clm.negotiation.round'].create({
            'contract_id': self.contract.id,
            'round_number': 1,
            'outbound_version_id': self.outbound_version.id,
            'status': 'in_progress',
        })

    def test_portal_contract_upload(self):
        """Test that a portal user can successfully upload a .docx file to a contract"""
        self.authenticate('portal_user@test.com', 'password')
        
        # A genuine Word package: the portal now rejects anything that is not one.
        file_content = _real_docx()
        files = {
            'upload_file': ('test_revision.docx', file_content, 'application/vnd.openxmlformats-officedocument.wordprocessingml.document')
        }
        
        # Get CSRF Token from the form
        get_resp = self.url_open(f'/my/contracts/{self.contract.id}')
        import re
        csrf_token = re.search(r'name="csrf_token" value="([^"]+)"', get_resp.text).group(1)

        # Make the POST request to the upload endpoint
        response = self.url_open(
            f'/my/contracts/{self.contract.id}/upload',
            data={'notes': 'My changes', 'csrf_token': csrf_token},
            files=files,
            timeout=15
        )
        
        # It should redirect on success (303 or 200 depending on requests config)
        self.assertTrue(response.status_code in [200, 303, 302])
        
        # Verify the round is completed
        self.assertEqual(self.round.status, 'completed')
        self.assertTrue(self.round.inbound_version_id)
        
        # Verify the inbound version was created properly
        inbound = self.round.inbound_version_id
        self.assertEqual(inbound.version_type, 'negotiated_draft')
        self.assertEqual(inbound.change_summary, 'My changes')
        self.assertTrue(inbound.document_id)

    def _upload(self, filename, content):
        self.authenticate('portal_user@test.com', 'password')
        page = self.url_open(f'/my/contracts/{self.contract.id}')
        csrf_token = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)
        return self.url_open(
            f'/my/contracts/{self.contract.id}/upload',
            data={'notes': 'bad file', 'csrf_token': csrf_token},
            files={'upload_file': (filename, content, _DOCX_MIME)},
            timeout=15, allow_redirects=False,
        )

    def test_corrupt_docx_is_rejected_not_a_server_error(self):
        """A file named .docx that is not a Word package gets a clean error (was HTTP 500)."""
        response = self._upload('fake.docx', b'dummy docx content')
        self.assertEqual(response.status_code, 303, response.text[:200])
        self.assertIn('error=', response.headers['Location'])
        self.round.invalidate_recordset()
        self.assertEqual(self.round.status, 'in_progress', 'The round must stay open')
        self.assertFalse(self.round.inbound_version_id)

    def test_zip_that_is_not_word_is_rejected(self):
        import zipfile
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w') as archive:
            archive.writestr('hello.txt', 'not a word document')
        response = self._upload('renamed.docx', buf.getvalue())
        self.assertEqual(response.status_code, 303)
        self.assertIn('error=', response.headers['Location'])
        self.round.invalidate_recordset()
        self.assertEqual(self.round.status, 'in_progress')
