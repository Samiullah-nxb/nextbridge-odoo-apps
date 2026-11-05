# Part of NextContract. See LICENSE file for full copyright and licensing details.

from odoo import models, fields


class ClmAssemblyRequest(models.Model):
    """
    Represents a request to assemble a draft document from a template + contract.

    Phase 3: Produces a plain-text preview via PlainTextRenderer.
    Phase 4: Produces DOCX via DocxRenderer and PDF via PdfRenderer.

    Also serves as the generation audit record (PRD §34), capturing:
    - template version at generation time
    - clause version snapshot
    - input variable snapshot
    - output document hash (SHA-256)
    """
    _name = 'clm.assembly.request'
    _description = 'Draft Assembly Request'

    contract_id = fields.Many2one(
        'clm.contract', string='Contract', required=True, ondelete='cascade'
    )
    template_id = fields.Many2one(
        'clm.template', string='Template', required=True, ondelete='restrict'
    )
    state = fields.Selection([
        ('pending', 'Pending'),
        ('in_progress', 'In Progress'),
        ('done', 'Done'),
        ('failed', 'Failed'),
    ], string='Status', default='pending')
    renderer = fields.Selection([
        ('plain_text', 'Plain Text (Phase 3)'),
        ('docx', 'DOCX'),
        ('pdf', 'PDF'),
    ], string='Renderer', default='plain_text')
    result_version_id = fields.Many2one(
        'clm.contract.version', string='Resulting Version', readonly=True
    )
    result_preview = fields.Text(string='Plain-text Preview', readonly=True)
    error_message = fields.Text(string='Error', readonly=True)
    requested_by_id = fields.Many2one(
        'res.users', string='Requested By', default=lambda self: self.env.uid
    )
    request_date = fields.Datetime(string='Requested At', default=fields.Datetime.now)

    # -------------------------------------------------------------------------
    # PRD §34 — Generation audit fields
    # -------------------------------------------------------------------------
    template_version_snapshot = fields.Integer(
        string='Template Version (at generation)', readonly=True,
        help='Version number of the template at the time of generation.'
    )
    base_docx_version_id = fields.Many2one(
        'clm.template.base.docx.version',
        string='Base DOCX Version (at generation)',
        readonly=True,
        ondelete='restrict',
        index=True,
        help='Exact Document Template File version used to render this draft. '
             'Changing the template Active version does not alter this snapshot.',
    )
    clause_versions_snapshot = fields.Text(
        string='Clause Versions Snapshot', readonly=True,
        help='JSON dict of {clause_id: version} for all clauses used in this generation.'
    )
    input_snapshot = fields.Text(
        string='Variable Input Snapshot', readonly=True,
        help='JSON dict of all resolved variable values at generation time.'
    )
    output_hash = fields.Char(
        string='Output Document Hash (SHA-256)', readonly=True,
        help='SHA-256 hash of the generated binary. Allows verification of document integrity.'
    )
