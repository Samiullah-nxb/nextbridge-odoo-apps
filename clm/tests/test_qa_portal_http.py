# Part of NextContract. See LICENSE file for full copyright and licensing details.
"""QA: portal, editor and signature endpoints must not leak across users (IDOR)."""
import base64
import io

from odoo.tests.common import HttpCase, tagged


def _valid_pdf():
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    pdf = canvas.Canvas(buf)
    pdf.drawString(72, 720, 'QA')
    pdf.save()
    return buf.getvalue()


@tagged('post_install', '-at_install', 'clm_qa')
class TestPortalEndpoints(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        cls.nda = env.ref('clm.contract_type_nda')

        def portal_user(login):
            return env['res.users'].create({
                'name': login, 'login': login, 'password': login, 'email': login,
                'group_ids': [(6, 0, [env.ref('base.group_portal').id])],
            })

        cls.user_a = portal_user('qa_portal_a@example.com')
        cls.user_b = portal_user('qa_portal_b@example.com')

        def contract(name, user):
            record = env['clm.contract'].with_context(migration_mode=True).create({
                'name': name, 'contract_type_id': cls.nda.id,
                'partner_id': user.partner_id.id, 'state': 'in_negotiation',
            })
            attachment = env['ir.attachment'].create({
                'name': 'v.pdf', 'type': 'binary', 'datas': base64.b64encode(_valid_pdf())})
            document = env['documents.document'].create({
                'name': 'v.pdf', 'attachment_id': attachment.id})
            version = env['clm.contract.version'].create({
                'contract_id': record.id, 'version_number': 1,
                'version_type': 'negotiated_draft', 'document_id': document.id,
                'status': 'active'})
            env['clm.contract.portal.access'].create({
                'contract_id': record.id, 'partner_id': user.partner_id.id})
            return record, version

        cls.contract_a, cls.version_a = contract('QA Portal Contract A', cls.user_a)
        cls.contract_b, cls.version_b = contract('QA Portal Contract B', cls.user_b)

    def test_anonymous_is_sent_to_login(self):
        response = self.url_open(f'/my/contracts/{self.contract_a.id}', allow_redirects=False)
        self.assertIn(response.status_code, (301, 302, 303))
        self.assertIn('/web/login', response.headers.get('Location', ''))

    def test_own_contract_page_loads(self):
        self.authenticate('qa_portal_a@example.com', 'qa_portal_a@example.com')
        response = self.url_open(f'/my/contracts/{self.contract_a.id}')
        self.assertEqual(response.status_code, 200)
        self.assertIn('QA Portal Contract A', response.text)

    def test_other_partners_contract_page_is_not_served(self):
        self.authenticate('qa_portal_a@example.com', 'qa_portal_a@example.com')
        response = self.url_open(f'/my/contracts/{self.contract_b.id}', allow_redirects=False)
        self.assertNotEqual(response.status_code, 200)
        self.assertNotIn('QA Portal Contract B', response.text)

    def test_other_partners_contract_not_listed(self):
        self.authenticate('qa_portal_a@example.com', 'qa_portal_a@example.com')
        response = self.url_open('/my/contracts')
        self.assertEqual(response.status_code, 200)
        self.assertIn('QA Portal Contract A', response.text)
        self.assertNotIn('QA Portal Contract B', response.text)

    def test_cannot_download_another_partners_version(self):
        self.authenticate('qa_portal_a@example.com', 'qa_portal_a@example.com')
        response = self.url_open(
            f'/my/contracts/download/{self.version_b.id}', allow_redirects=False)
        self.assertNotEqual(response.status_code, 200)
        self.assertNotIn(b'%PDF', response.content[:8])

    def test_cannot_download_a_version_that_was_not_shared(self):
        """Own contract, but no active round and the version is not in shared_version_ids."""
        self.authenticate('qa_portal_a@example.com', 'qa_portal_a@example.com')
        response = self.url_open(
            f'/my/contracts/download/{self.version_a.id}', allow_redirects=False)
        self.assertNotEqual(response.status_code, 200)

    def test_shared_version_can_be_downloaded(self):
        access = self.contract_a.portal_access_ids
        access.shared_version_ids = [(4, self.version_a.id)]
        self.authenticate('qa_portal_a@example.com', 'qa_portal_a@example.com')
        response = self.url_open(f'/my/contracts/download/{self.version_a.id}')
        self.assertEqual(response.status_code, 200)

    def test_expired_access_blocks_the_page(self):
        access = self.contract_a.portal_access_ids
        access.expiry_date = '2000-01-01 00:00:00'
        self.authenticate('qa_portal_a@example.com', 'qa_portal_a@example.com')
        response = self.url_open(f'/my/contracts/{self.contract_a.id}', allow_redirects=False)
        self.assertNotEqual(response.status_code, 200)

    def test_upload_without_csrf_token_is_refused(self):
        self.authenticate('qa_portal_a@example.com', 'qa_portal_a@example.com')
        response = self.url_open(
            f'/my/contracts/{self.contract_a.id}/upload',
            data={'notes': 'x'},
            files={'upload_file': ('r.docx', b'x', 'application/octet-stream')},
            allow_redirects=False)
        self.assertIn(response.status_code, (400, 403))

    def test_portal_user_invited_after_access_record_is_linked(self):
        """BUG-20: access.user_id is a stored compute depending only on partner_id."""
        env = self.env
        partner = env['res.partner'].create({'name': 'Late Invitee', 'email': 'late@example.com'})
        access = env['clm.contract.portal.access'].create({
            'contract_id': self.contract_a.id, 'partner_id': partner.id})
        self.assertFalse(access.user_id)
        env['res.users'].create({
            'name': 'Late Invitee', 'login': 'late@example.com', 'partner_id': partner.id,
            'group_ids': [(6, 0, [env.ref('base.group_portal').id])],
        })
        access.invalidate_recordset()
        self.assertTrue(
            access.user_id,
            'The portal user created after the access record is not linked to it, '
            'so the counterparty can never open the contract.')


@tagged('post_install', '-at_install', 'clm_qa')
class TestEditorAndSignEndpoints(HttpCase):

    def test_editor_document_requires_login(self):
        template = self.env['clm.template'].create({
            'name': 'QA Template',
            'contract_type_id': self.env.ref('clm.contract_type_nda').id,
        })
        response = self.url_open(
            f'/clm_docx_editor/template/{template.id}/document', allow_redirects=False)
        self.assertNotEqual(response.status_code, 200)

    def test_editor_document_denied_to_portal_user(self):
        env = self.env
        env['res.users'].create({
            'name': 'QA P', 'login': 'qa_editor_portal@example.com',
            'password': 'qa_editor_portal@example.com', 'email': 'qa_editor_portal@example.com',
            'group_ids': [(6, 0, [env.ref('base.group_portal').id])]})
        template = env['clm.template'].create({
            'name': 'QA Template 2',
            'contract_type_id': env.ref('clm.contract_type_nda').id})
        self.authenticate('qa_editor_portal@example.com', 'qa_editor_portal@example.com')
        response = self.url_open(
            f'/clm_docx_editor/template/{template.id}/document', allow_redirects=False)
        self.assertNotEqual(response.status_code, 200)

    def test_signature_pages_of_other_signers_are_not_served(self):
        env = self.env
        stranger = env['res.users'].create({
            'name': 'Stranger', 'login': 'stranger@example.com',
            'password': 'stranger@example.com', 'email': 'stranger@example.com',
            'group_ids': [(6, 0, [env.ref('base.group_portal').id])]})
        signer = env['res.partner'].create({'name': 'Real Signer', 'email': 'real@example.com'})
        attachment = env['ir.attachment'].create({
            'name': 's.pdf', 'type': 'binary', 'datas': base64.b64encode(_valid_pdf())})
        template_id, _name = env['sign.template'].create_sign_template_from_ir_attachment_data(
            attachment_id=attachment.id, res_id=0, res_model='ir.attachment')
        template = env['sign.template'].browse(template_id)
        role = env.ref('sign.sign_item_role_default')
        request = env['sign.request'].with_context(no_sign_mail=True).create({
            'template_id': template.id, 'reference': 'QA',
            'request_item_ids': [(0, 0, {'role_id': role.id, 'partner_id': signer.id})],
        })
        item = request.request_item_ids
        self.authenticate('stranger@example.com', 'stranger@example.com')
        response = self.url_open(f'/my/signature/{item.id}', allow_redirects=False)
        self.assertIn(response.status_code, (301, 302, 303), 'must redirect away')
        self.assertNotIn(item.access_token, response.text)
        final = self.url_open(f'/my/clm/signature/{item.id}/final.pdf', allow_redirects=False)
        self.assertNotEqual(final.status_code, 200)
