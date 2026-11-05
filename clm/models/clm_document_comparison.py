# Part of NextContract. See LICENSE file for full copyright and licensing details.

import base64
import logging

from markupsafe import Markup

from odoo import api, models, fields
from odoo.exceptions import UserError, ValidationError
from odoo.tools.misc import format_date

_logger = logging.getLogger(__name__)


class ClmDocumentComparison(models.Model):
    _name = 'clm.document.comparison'
    _description = 'Document Comparison (Redline)'
    _order = 'create_date desc'

    base_version_id = fields.Many2one(
        'clm.contract.version', string='Original',
        required=True, ondelete='cascade',
        help='The earlier version (what you started from).'
    )
    target_version_id = fields.Many2one(
        'clm.contract.version', string='Revised',
        required=True, ondelete='cascade',
        help='The later version (what changed).'
    )
    contract_id = fields.Many2one(
        'clm.contract', related='base_version_id.contract_id',
        store=True, index=True
    )

    @api.constrains('base_version_id', 'target_version_id')
    def _check_same_contract(self):
        for comparison in self.sudo():
            if comparison.base_version_id.contract_id != comparison.target_version_id.contract_id:
                raise ValidationError(
                    "Only two versions of the same contract can be compared."
                )

    title = fields.Char(string='Title', compute='_compute_header')
    base_caption = fields.Char(string='Original caption', compute='_compute_header')
    target_caption = fields.Char(string='Revised caption', compute='_compute_header')
    stats_label = fields.Char(string='Stats', compute='_compute_header')

    addition_count = fields.Integer(string='Additions', readonly=True, default=0)
    deletion_count = fields.Integer(string='Deletions', readonly=True, default=0)
    change_count = fields.Integer(string='Changes', readonly=True, default=0)
    has_differences = fields.Boolean(string='Has Differences', readonly=True, default=False)

    diff_view = fields.Selection([
        ('unified', 'Unified'),
        ('side_by_side', 'Side-by-side'),
    ], string='Text view', default='unified', required=True)

    # PDF document panes (Original | Revised)
    base_pdf_attachment_id = fields.Many2one(
        'ir.attachment', string='Original PDF', readonly=True, copy=False,
        ondelete='set null',
    )
    target_pdf_attachment_id = fields.Many2one(
        'ir.attachment', string='Revised PDF', readonly=True, copy=False,
        ondelete='set null',
    )
    base_pdf_preview_html = fields.Html(
        string='Original PDF preview', compute='_compute_pdf_preview_html',
        sanitize=False,
    )
    target_pdf_preview_html = fields.Html(
        string='Revised PDF preview', compute='_compute_pdf_preview_html',
        sanitize=False,
    )
    # Draftable-style middle rail: [{kind, top, height, page}, ...]
    pdf_gutter_markers = fields.Json(string='PDF gutter markers', copy=False)
    pdf_gutter_html = fields.Html(
        string='PDF gutter', compute='_compute_pdf_gutter_html', sanitize=False,
    )

    # Red / green change panels under the PDFs
    html_changes = fields.Html(
        string='Changes', readonly=True, sanitize=False,
        help='Removed (red) and Added (green) sections.',
    )
    # Full text redline (optional detail / Export PDF)
    html_redline = fields.Html(
        string='HTML Redline', readonly=True, sanitize=False,
        help='The generated unified / side-by-side text redline HTML.'
    )
    status = fields.Selection([
        ('pending', 'Pending Generation'),
        ('in_progress', 'In Progress'),
        ('done', 'Completed'),
        ('failed', 'Failed')
    ], string='Status', default='pending')
    error_message = fields.Text(string='Error Message', readonly=True)

    @api.depends(
        'base_version_id', 'base_version_id.version_number',
        'base_version_id.version_type', 'base_version_id.create_date',
        'target_version_id', 'target_version_id.version_number',
        'target_version_id.version_type', 'target_version_id.create_date',
        'addition_count', 'deletion_count', 'change_count', 'status',
        'has_differences',
    )
    def _compute_header(self):
        for comp in self:
            base = comp.base_version_id
            target = comp.target_version_id
            if base and target:
                comp.title = 'Compare v%s → v%s' % (
                    base.version_number, target.version_number,
                )
                comp.base_caption = comp._version_caption(base)
                comp.target_caption = comp._version_caption(target)
            else:
                comp.title = 'Compare versions'
                comp.base_caption = False
                comp.target_caption = False

            if comp.status == 'done' and not comp.has_differences:
                comp.stats_label = 'No wording changes'
            elif comp.status == 'done':
                comp.stats_label = '+%s additions · −%s deletions · %s changes' % (
                    comp.addition_count, comp.deletion_count, comp.change_count,
                )
            else:
                comp.stats_label = False

    @api.depends(
        'base_pdf_attachment_id', 'target_pdf_attachment_id',
        'base_pdf_attachment_id.checksum', 'target_pdf_attachment_id.checksum',
        'base_pdf_attachment_id.write_date', 'target_pdf_attachment_id.write_date',
        'base_caption', 'target_caption', 'write_date',
    )
    def _compute_pdf_preview_html(self):
        from odoo.addons.clm.services.pdf_preview import pdf_attachment_preview_html
        for comp in self:
            comp.base_pdf_preview_html = comp._clm_pdf_pane_html(
                comp.base_pdf_attachment_id, comp.base_caption or 'Original',
                pdf_attachment_preview_html,
            )
            comp.target_pdf_preview_html = comp._clm_pdf_pane_html(
                comp.target_pdf_attachment_id, comp.target_caption or 'Revised',
                pdf_attachment_preview_html,
            )

    @api.depends(
        'pdf_gutter_markers', 'has_differences', 'status',
        'addition_count', 'deletion_count',
    )
    def _compute_pdf_gutter_html(self):
        """Thin Draftable-style tick lines — rail + viewport live in the form XML."""
        for comp in self:
            ticks = []
            for marker in comp.pdf_gutter_markers or []:
                kind = marker.get('kind') or 'del'
                # Never show green ticks when there are no additions (and vice versa).
                if kind == 'add' and not comp.addition_count:
                    continue
                if kind == 'del' and not comp.deletion_count:
                    continue
                top = max(0.0, min(1.0, float(marker.get('top') or 0)))
                height = max(0.004, min(1.0 - top, float(marker.get('height') or 0.008)))
                css = 'o_clm_pdf_gutter_del' if kind == 'del' else 'o_clm_pdf_gutter_add'
                if kind == 'chg':
                    css = 'o_clm_pdf_gutter_chg'
                title = (
                    'Removed' if kind == 'del' else ('Added' if kind == 'add' else 'Changed')
                )
                # Tall changes → stacked thin lines (Draftable minimap look).
                line_step = 0.0055
                count = max(1, min(10, int(round(height / line_step)) or 1))
                for index in range(count):
                    if count == 1:
                        line_top = top
                    else:
                        line_top = top + (height * index / (count - 1))
                    line_top = max(0.0, min(0.997, line_top))
                    ticks.append(Markup(
                        '<div class="o_clm_pdf_gutter_tick %s" style="top:%s%%;" '
                        'title="%s"></div>'
                    ) % (css, '%.3f' % (line_top * 100.0), title))
            comp.pdf_gutter_html = Markup('').join(ticks) if ticks else Markup('')

    def _clm_pdf_pane_html(self, attachment, label, preview_fn):
        if not attachment:
            return Markup(
                '<div class="o_clm_pdf_pane_empty">'
                '<div class="o_clm_empty_title">PDF not available</div>'
                '<div class="o_clm_empty_text">Could not build a PDF for %s.</div>'
                '</div>'
            ) % label
        attachment = attachment.sudo()
        if not attachment.access_token:
            attachment.generate_access_token()
        # Bust PDF.js / browser cache whenever highlight bytes change.
        cache_key = '%s-%s-%s' % (
            attachment.id,
            attachment.checksum or '0',
            int((attachment.write_date or self.write_date).timestamp())
            if (attachment.write_date or self.write_date) else 0,
        )
        # Fit page width to each pane — zoom tracks screen / fullscreen size.
        viewer = preview_fn(
            attachment.id,
            filename=attachment.name or 'document.pdf',
            cache_key=cache_key,
            access_token=attachment.access_token,
            zoom='page-width',
        )
        return Markup(
            '<div class="o_clm_compare_pdf_pane">'
            '<div class="o_clm_compare_pdf_label">%s</div>%s</div>'
        ) % (label, viewer)

    def _version_caption(self, version):
        type_labels = dict(
            version._fields['version_type']._description_selection(self.env)
        )
        parts = ['v%s' % version.version_number]
        label = type_labels.get(version.version_type)
        if label:
            parts.append(label)
        if version.create_date:
            parts.append(format_date(self.env, version.create_date))
        return ' · '.join(parts)

    def write(self, vals):
        # A redline describes one pair of versions; pointing the comparison at
        # another pair makes the stored result stale.
        if {'base_version_id', 'target_version_id'} & set(vals) and 'html_redline' not in vals:
            vals = dict(
                vals,
                status='pending',
                html_redline=False,
                html_changes=False,
                error_message=False,
                addition_count=0,
                deletion_count=0,
                change_count=0,
                has_differences=False,
                base_pdf_attachment_id=False,
                target_pdf_attachment_id=False,
            )
        return super().write(vals)

    def _clm_resolve_version_pdf(self, version):
        """Best PDF attachment for a version (generated or converted from Word)."""
        version.ensure_one()
        contract = version.contract_id
        try:
            if hasattr(version, '_clm_generate_document'):
                return version._clm_generate_document('pdf')
        except Exception as error:  # noqa: BLE001
            _logger.info(
                'NextContract: generate PDF for v%s failed (%s); trying convert',
                version.version_number, error,
            )
        # Convert an attached Word/PDF document when generation is unavailable.
        attachment = False
        if version.document_id and version.document_id.attachment_id:
            attachment = version.document_id.attachment_id
        elif getattr(version, 'generated_docx_id', False):
            attachment = version.generated_docx_id
        elif getattr(version, 'generated_pdf_id', False):
            attachment = version.generated_pdf_id
        if not attachment:
            raise UserError(
                'Version v%s has no document that can be previewed as PDF.'
                % version.version_number
            )
        return contract._clm_prepare_attachment_for_pdf_preview(attachment)

    def _clm_attachment_raw_bytes(self, attachment):
        attachment = attachment.sudo()
        raw = attachment.with_context(bin_size=False).raw or b''
        if not raw and attachment.datas:
            datas = attachment.datas
            if isinstance(datas, str):
                datas = datas.encode('ascii')
            try:
                raw = base64.b64decode(datas, validate=False)
            except Exception:  # noqa: BLE001
                raw = b''
        return raw

    def _clm_store_compare_pdf(self, filename, pdf_bytes, existing=False):
        """Store a comparison-owned PDF (highlighted Original / Revised)."""
        self.ensure_one()
        data = bytes(pdf_bytes or b'')
        if not data.startswith(b'%PDF'):
            raise UserError('Could not store a valid comparison PDF.')
        vals = {
            'name': filename,
            'raw': data,
            'mimetype': 'application/pdf',
            'res_model': self._name,
            'res_id': self.id,
            'description': 'Highlighted comparison PDF',
        }
        Attachment = self.env['ir.attachment'].sudo()
        if existing:
            existing.sudo().write(vals)
            attachment = existing
        else:
            attachment = Attachment.create(vals)
        if not attachment.access_token:
            attachment.generate_access_token()
        return attachment

    def _clm_build_highlighted_pdfs(self, result, base_source, target_source):
        """Copy version PDFs and paint red/green highlight annotations on them.

        Returns ``(base_pdf, target_pdf, gutter_markers)``.
        """
        self.ensure_one()
        from ..services.pdf_compare_highlight import build_pdf_highlights

        base_raw = self._clm_attachment_raw_bytes(base_source)
        target_raw = self._clm_attachment_raw_bytes(target_source)
        markers = []
        if not result.get('identical'):
            # Contiguous deleted/added blocks (full clauses) + unique word replaces.
            base_raw, target_raw, markers = build_pdf_highlights(
                base_raw, target_raw, result=result,
            )

        ref = self.contract_id.contract_ref or self.contract_id.id
        base_name = 'CLM_Compare_Original_%s_v%s.pdf' % (
            ref, self.base_version_id.version_number,
        )
        target_name = 'CLM_Compare_Revised_%s_v%s.pdf' % (
            ref, self.target_version_id.version_number,
        )
        # Drop previous highlighted copies for this comparison.
        old = (self.base_pdf_attachment_id | self.target_pdf_attachment_id).filtered(
            lambda a: a.res_model == self._name and a.res_id == self.id
        )
        base_pdf = self._clm_store_compare_pdf(base_name, base_raw)
        target_pdf = self._clm_store_compare_pdf(target_name, target_raw)
        stale = old - base_pdf - target_pdf
        if stale:
            stale.unlink()
        return base_pdf, target_pdf, markers

    def action_generate_redline(self):
        """Compare wording and open highlighted Original / Revised PDFs."""
        for comp in self:
            if not comp.base_version_id or not comp.target_version_id:
                raise UserError('Pick both an Original and a Revised version to compare.')
            comp.status = 'in_progress'
            comp.error_message = False
            try:
                from ..services.redline_engine import HtmlRedlineEngine
                engine = HtmlRedlineEngine()
                result = engine.build_result(comp.base_version_id, comp.target_version_id)
                html_output = engine.render_html(result, diff_view=comp.diff_view or 'unified')
                html_changes = engine.render_changes_panels(result)

                base_pdf = target_pdf = False
                gutter_markers = []
                pdf_errors = []
                try:
                    base_source = comp._clm_resolve_version_pdf(comp.base_version_id)
                    target_source = comp._clm_resolve_version_pdf(comp.target_version_id)
                    base_pdf, target_pdf, gutter_markers = comp._clm_build_highlighted_pdfs(
                        result, base_source, target_source,
                    )
                except Exception as error:  # noqa: BLE001
                    pdf_errors.append(str(error))
                    _logger.exception('NextContract: highlighted PDF compare failed')

                # Drop orphaned gutter ticks that don't match the wording counts
                # (e.g. stale green marks when addition_count is 0).
                adds = int(result['addition_count'] or 0)
                dels = int(result['deletion_count'] or 0)
                clean_markers = []
                for marker in gutter_markers or []:
                    kind = marker.get('kind')
                    if kind == 'add' and not adds:
                        continue
                    if kind == 'del' and not dels:
                        continue
                    clean_markers.append(marker)

                comp.write({
                    'html_redline': html_output,
                    'html_changes': html_changes,
                    'status': 'done',
                    'addition_count': adds,
                    'deletion_count': dels,
                    'change_count': result['change_count'],
                    'has_differences': not result['identical'],
                    'error_message': '\n'.join(pdf_errors) if pdf_errors else False,
                    'base_pdf_attachment_id': base_pdf.id if base_pdf else False,
                    'target_pdf_attachment_id': target_pdf.id if target_pdf else False,
                    'pdf_gutter_markers': clean_markers,
                })
            except Exception as e:
                comp.write({
                    'status': 'failed',
                    'error_message': str(e),
                    'html_redline': False,
                    'html_changes': False,
                    'addition_count': 0,
                    'deletion_count': 0,
                    'change_count': 0,
                    'has_differences': False,
                    'base_pdf_attachment_id': False,
                    'target_pdf_attachment_id': False,
                    'pdf_gutter_markers': [],
                })
        # Reload the form so PDF.js picks up new attachment URLs (not a cached iframe).
        if len(self) == 1:
            return self._clm_open_action()
        return True

    def action_set_diff_view_unified(self):
        return self._action_set_diff_view('unified')

    def action_set_diff_view_side_by_side(self):
        return self._action_set_diff_view('side_by_side')

    def _action_set_diff_view(self, diff_view):
        for comp in self:
            comp.diff_view = diff_view
            if comp.status == 'done' and comp.base_version_id and comp.target_version_id:
                from ..services.redline_engine import HtmlRedlineEngine
                engine = HtmlRedlineEngine()
                result = engine.build_result(comp.base_version_id, comp.target_version_id)
                comp.html_redline = engine.render_html(result, diff_view=diff_view)
                comp.html_changes = engine.render_changes_panels(result)
        return True

    @api.model
    def _clm_compare(self, base_version, target_version):
        """Compare two versions, reusing the latest comparison of that pair if it exists."""
        comparisons = self.search([
            ('base_version_id', '=', base_version.id),
            ('target_version_id', '=', target_version.id),
        ], order='id desc')
        comparison = comparisons[:1]
        extras = comparisons[1:]
        if not comparison:
            comparison = self.create({
                'base_version_id': base_version.id,
                'target_version_id': target_version.id,
            })
        elif extras:
            # Keep a single record per pair so the UI never opens a stale copy.
            extras.unlink()
        comparison.action_generate_redline()
        return comparison

    def _clm_open_action(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': self.title or 'Compare v%s ↔ v%s' % (
                self.base_version_id.version_number, self.target_version_id.version_number,
            ),
            'res_model': 'clm.document.comparison',
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'current',
        }
