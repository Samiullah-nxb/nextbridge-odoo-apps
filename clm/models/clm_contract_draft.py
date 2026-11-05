# Part of NextContract. See LICENSE file for full copyright and licensing details.

import hashlib
import json

from odoo import api, models, fields
from odoo.exceptions import UserError

from ..services import DraftAssemblyEngine, PlainTextRenderer, DocxRenderer, PdfRenderer

_RENDERER_MAP = {
    'plain_text': PlainTextRenderer,
    'docx': DocxRenderer,
    'pdf': PdfRenderer,
}

_MIME_MAP = {
    'plain_text': ('text/plain', 'txt'),
    'docx': ('application/vnd.openxmlformats-officedocument.wordprocessingml.document', 'docx'),
    'pdf': ('application/pdf', 'pdf'),
}


class ClmContract(models.Model):
    """
    Extends clm.contract with draft assembly capabilities (Phase 3 + Phase 4).

    _generate_draft(renderer='plain_text' | 'docx' | 'pdf') orchestrates:
      1. clm.contract.clause  → the template's clauses become the contract's
                                own wording on first generation
      2. DraftAssemblyEngine  → assembly context (variables, conditional sections)
      3. clm.contract.version → a new version freezing that content
      4. Renderer             → bytes (text / DOCX / PDF) of that version
      5. ir.attachment        → binary storage
      6. documents.document   → Odoo Documents registration
      7. clm.assembly.request → audit record (PRD §34) with SHA-256 hash
    """
    _inherit = 'clm.contract'

    template_id = fields.Many2one(
        'clm.template', string='Contract Template',
        domain=[('state', '=', 'published')]
    )
    base_docx_version_id = fields.Many2one(
        'clm.template.base.docx.version',
        string='Locked Base DOCX Version',
        readonly=True,
        copy=False,
        ondelete='restrict',
        index=True,
        help='Document Template File version captured when this contract was '
             'created (or when a template with a Base DOCX was first assigned). '
             'Subsequent changes to the template Active version never update '
             'this lock. All preview / DOCX / PDF for this contract use it.',
    )

    @api.model_create_multi
    def create(self, vals_list):
        contracts = super().create(vals_list)
        contracts._clm_capture_base_docx_version()
        return contracts

    def write(self, vals):
        if (
            'base_docx_version_id' in vals
            and not self.env.context.get('clm_capturing_base_docx')
        ):
            new_id = vals.get('base_docx_version_id') or False
            for contract in self:
                if (
                    contract.base_docx_version_id
                    and new_id
                    and contract.base_docx_version_id.id != new_id
                ):
                    raise UserError(
                        "This contract's Document Template version is locked "
                        "and cannot be changed. Create a new contract to use a "
                        "different Active Template Version."
                    )
        res = super().write(vals)
        if 'template_id' in vals and not self.env.context.get('clm_capturing_base_docx'):
            self.filtered(
                lambda c: c.template_id and not c.base_docx_version_id
            )._clm_capture_base_docx_version()
        return res

    def _clm_capture_base_docx_version(self):
        """Lock the template's Active Base DOCX onto contracts that have none yet.

        The Template Active version only feeds *new* locks. Once set, the
        contract never re-reads the template Active version for generation.
        """
        for contract in self:
            if contract.base_docx_version_id:
                continue
            template = contract.template_id
            if not template or not hasattr(template, '_active_base_docx_version'):
                continue
            active = template._active_base_docx_version()
            if not active:
                continue
            contract.with_context(clm_capturing_base_docx=True).write({
                'base_docx_version_id': active.id,
            })

    def _clm_document_generation_format(self):
        """Return which of the four document formats this contract uses.

        ``default_no_template`` — Format 1
        ``default_template``    — Format 2 (template, no body, no Base DOCX)
        ``template_body``       — Format 3
        ``base_docx``           — Format 4 (locked / active Base DOCX)
        """
        self.ensure_one()
        template = self.template_id
        if not template:
            return 'default_no_template'
        if self.base_docx_version_id or (
            hasattr(template, 'has_base_document') and template.has_base_document
        ):
            return 'base_docx'
        if template.content and str(template.content).strip():
            return 'template_body'
        return 'default_template'

    def _clm_require_base_docx_bytes(self, base_docx_version=None):
        """Return Base DOCX bytes when Format 4 applies; raise if the file is missing.

        Never falls back to the template Active version when a lock exists but
        its file cannot be read.
        """
        self.ensure_one()
        template = self.template_id
        base_docx_version = base_docx_version or self._clm_resolve_base_docx_version()
        expects_base = bool(base_docx_version) or (
            template
            and hasattr(template, 'has_base_document')
            and template.has_base_document
        )
        if not expects_base:
            return b''
        data = DocxRenderer._base_docx_bytes(
            template, base_docx_version=base_docx_version,
        )
        if data:
            return data
        if base_docx_version:
            raise UserError(
                "This contract is locked to Document Template version '%s', "
                "but that file can no longer be read. NextContract will not "
                "switch to another Template Version. Restore the file or "
                "contact Legal Admin."
                % (base_docx_version.display_name,)
            )
        raise UserError(
            "Template '%s' is configured with a Document Template File, but "
            "no Active Template Version is available. Activate a version "
            "before generating documents."
            % (template.display_name if template else '?',)
        )

    def action_open_generate_draft_wizard(self):
        self.ensure_one()
        return {
            'name': 'Generate Draft',
            'type': 'ir.actions.act_window',
            'res_model': 'clm.generate.draft.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_contract_id': self.id},
        }

    def _generate_draft(self, renderer='plain_text'):
        """
        Generate a contract draft using the specified renderer.

        Phase 3 renderer: 'plain_text' — safe preview, no external deps.
        Phase 4 renderers:
          'docx' — docxtpl + a .docx base template on clm.template, otherwise
                   python-docx.
          'pdf'  — WeasyPrint when installed, otherwise Odoo's QWeb report.
        """
        self.ensure_one()

        if not self.template_id:
            raise UserError(
                "Please select a template before generating a draft."
            )
        if self.template_id.state != 'published':
            raise UserError(
                f"Template '{self.template_id.name}' is not published. "
                "Only published templates can be used to generate drafts."
            )
        renderer_cls = _RENDERER_MAP.get(renderer)
        if not renderer_cls:
            raise UserError(f"Unknown renderer: '{renderer}'. Choose from: {list(_RENDERER_MAP)}")

        # 1. On first generation the template's clauses become this contract's
        #    own wording; from then on that wording is what gets drafted.
        if not self.clause_ids:
            self._clm_load_template_clauses()

        # Ensure a lock exists for Format 4 before resolving (covers legacy
        # contracts created before the contract-level field existed).
        if (
            not self.base_docx_version_id
            and hasattr(self.template_id, 'has_base_document')
            and self.template_id.has_base_document
        ):
            self._clm_capture_base_docx_version()

        # Always resolve via the contract lock / prior pins — never re-read
        # the template Active version when a lock already exists.
        base_docx_version = self._clm_resolve_base_docx_version()
        if (
            hasattr(self.template_id, 'has_base_document')
            and self.template_id.has_base_document
            and not base_docx_version
        ):
            raise UserError(
                "Template '%s' is configured with a Document Template File, "
                "but no Active Template Version is available. Activate a "
                "version before generating drafts."
                % self.template_id.display_name
            )
        if base_docx_version and renderer in ('docx', 'pdf'):
            # Fail early with a clear message instead of producing an empty /
            # fallback document when the locked file is unreadable.
            self._clm_require_base_docx_bytes(base_docx_version)

        # 2. Assemble context (audit snapshot + Formats 1–3 renderers).
        engine = DraftAssemblyEngine(self.template_id, self)
        context = engine.assemble()
        if base_docx_version:
            context['base_docx_version'] = base_docx_version

        # 3. Freeze the content into a new version. The file below is a
        #    rendering of that version, never of the live contract.
        version = self._clm_create_content_version(
            version_type='internal_draft',
            source=f'Generated from template: {self.template_id.name}',
            change_summary=f'Auto-generated draft ({renderer} renderer)',
            base_docx_version=base_docx_version,
        )
        context['version'] = version
        context['base_docx_version'] = version.base_docx_version_id or base_docx_version

        # 4. Render — Format 4 DOCX/PDF use the same locked Base DOCX pipeline
        #    as Preview / Generate DOCX / Generate PDF / Final Contract.
        output = None
        if base_docx_version and renderer in ('docx', 'pdf'):
            output_bytes, mime, filename = self._render_contract_document(
                renderer, version=version, kind='draft',
            )
        else:
            output = renderer_cls().render(context)
            if isinstance(output, str):
                output_bytes = output.encode('utf-8')
            else:
                output_bytes = output
            mime, ext = _MIME_MAP[renderer]
            filename = self._clm_document_filename(ext, kind='draft', version=version)

        # 5. Compute SHA-256 hash for audit (PRD §34)
        output_hash = hashlib.sha256(output_bytes).hexdigest()

        # 6. Store as ir.attachment (use raw — datas can leave empty filestore rows)
        attachment = self.env['ir.attachment'].create({
            'name': filename,
            'raw': (
                output_bytes if isinstance(output_bytes, (bytes, bytearray))
                else bytes(output_bytes)
            ),
            'mimetype': mime,
            'res_model': 'clm.contract',
            'res_id': self.id,
        })

        # 7. Create documents.document entry (Odoo Documents layer) and link it
        if 'documents.document' in self.env:
            document = self.env['documents.document'].create({
                'name': filename,
                'attachment_id': attachment.id,
            })
            version.with_context(clm_skip_version_preview_compute=True).write(
                {'document_id': document.id},
            )
        if renderer in ('docx', 'pdf'):
            version.sudo().with_context(clm_skip_version_preview_compute=True).write(
                {'generated_%s_id' % renderer: attachment.id},
            )

        # 8. Build audit snapshots (PRD §34)
        input_snapshot = json.dumps(context['variables'], default=str)
        clause_versions_snapshot = json.dumps(
            {str(k): v for k, v in context.get('clause_versions', {}).items()}
        )

        # 9. Create assembly request (= generation audit record)
        request = self.env['clm.assembly.request'].create({
            'contract_id': self.id,
            'template_id': self.template_id.id,
            'renderer': renderer,
            'state': 'done',
            'result_version_id': version.id,
            'result_preview': output if isinstance(output, str) else None,
            'template_version_snapshot': self.template_id.version_number,
            'base_docx_version_id': (
                (version.base_docx_version_id or base_docx_version).id
                if (version.base_docx_version_id or base_docx_version) else False
            ),
            'clause_versions_snapshot': clause_versions_snapshot,
            'input_snapshot': input_snapshot,
            'output_hash': output_hash,
        })
        if request.base_docx_version_id:
            request.base_docx_version_id.invalidate_recordset(['is_used', 'status_label'])

        return request
