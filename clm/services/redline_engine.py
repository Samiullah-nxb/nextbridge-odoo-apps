# Part of NextContract. See LICENSE file for full copyright and licensing details.
"""
Redline Comparison Engine Interface and Implementation

This provides the abstraction layer for document redlining as requested in PRD §39.
Phase 5 implements a PlainText HtmlRedlineEngine.
Future phases can swap this for a NativeDocxRedlineEngine or external APIs.

What is compared:
  * a version with frozen contract content (clm_draft `content_snapshot`) is
    compared on that content — parties, terms and clause wording — so two
    versions edited in Odoo can be compared without any file;
  * any other version is compared on the text of its DOCX document.

Output is a GitHub-style contract redline: change navigator, unified (inline)
view, and side-by-side view, with addition/deletion counts.
"""

import base64
import io
import difflib
import re

from markupsafe import Markup, escape

from odoo.exceptions import UserError

try:
    import docx
    PYTHON_DOCX_AVAILABLE = True
except ImportError:
    PYTHON_DOCX_AVAILABLE = False

_HEADING_RE = re.compile(
    r'^(?:section\s+\d|article\s+\d|clause\s+\d|\d+(?:\.\d+)*\s+\S|'
    r'parties|recitals|definitions|payment|term|termination|notice|'
    r'confidential|governing|signatures?\b)',
    re.IGNORECASE,
)


class RedlineEngine:
    """Base interface for all Redline engines."""

    def compare(self, base_version, target_version):
        """
        Compare two clm.contract.version records.
        Returns the comparison output (HTML string for Phase 5).
        """
        raise NotImplementedError("Subclasses must implement compare()")


class HtmlRedlineEngine(RedlineEngine):
    """
    Phase 5 Implementation.

    Line-level comparison with word-level marks inside changed lines.
    Renders a contract-friendly GitHub-style redline (unified + side-by-side).
    """

    def compare(self, base_version, target_version, diff_view='unified'):
        result = self.build_result(base_version, target_version)
        return self.render_html(result, diff_view=diff_view)

    def build_result(self, base_version, target_version):
        """Structured diff used for UI stats, HTML views, and PDF export."""
        base_lines, target_lines, source = self._resolve_compare_lines(
            base_version, target_version,
        )

        hunks = self._build_hunks(base_lines, target_lines)
        changes = self.summarize_changes(base_lines, target_lines)
        additions = sum(1 for h in hunks for row in h['rows'] if row['kind'] == 'add')
        deletions = sum(1 for h in hunks for row in h['rows'] if row['kind'] == 'del')

        identical_reason = False
        if not hunks:
            identical_reason = self._identical_reason(base_version, target_version, source)

        return {
            'base_version_number': base_version.version_number,
            'target_version_number': target_version.version_number,
            'base_label': self._version_short_label(base_version),
            'target_label': self._version_short_label(target_version),
            'addition_count': additions,
            'deletion_count': deletions,
            'change_count': len(hunks),
            'changes': changes,
            'hunks': hunks,
            'base_lines': base_lines,
            'target_lines': target_lines,
            'identical': not hunks,
            'identical_reason': identical_reason,
            'compare_source': source,
        }

    def render_changes_panels(self, result):
        """Draftable-style document compare: Original | gutter | Revised."""
        return self.render_document_compare(result)

    def render_document_compare(self, result):
        """Side-by-side documents with red deletions and green additions on the text."""
        if result.get('identical'):
            return self._render_identical(result)

        base_lines = result.get('base_lines') or []
        target_lines = result.get('target_lines') or []
        left_rows, right_rows, gutter_rows, change_ids = self._document_compare_rows(
            base_lines, target_lines,
        )
        toolbar = Markup(
            '<div class="o_clm_doc_toolbar" role="toolbar">'
            '<span class="o_clm_doc_mode is-active">Side by side</span>'
            '<span class="o_clm_doc_stat o_clm_doc_stat_del">−%s removed</span>'
            '<span class="o_clm_doc_stat o_clm_doc_stat_add">+%s added</span>'
            '<span class="o_clm_doc_stat">%s changes</span>'
            '<span class="o_clm_doc_nav">'
            '<a class="o_clm_doc_nav_btn" href="#clm-chg-%s">Previous</a>'
            '<a class="o_clm_doc_nav_btn" href="#clm-chg-%s">Next</a>'
            '</span></div>'
        ) % (
            result['deletion_count'],
            result['addition_count'],
            result['change_count'],
            change_ids[-1] if change_ids else 1,
            change_ids[0] if change_ids else 1,
        )
        headers = Markup(
            '<div class="o_clm_doc_headers">'
            '<div class="o_clm_doc_header o_clm_doc_header_left">Original — %s</div>'
            '<div class="o_clm_doc_gutter_head"></div>'
            '<div class="o_clm_doc_header o_clm_doc_header_right">Revised — %s</div>'
            '</div>'
        ) % (escape(result['base_label']), escape(result['target_label']))

        return Markup(
            '<div class="o_clm_doc_compare" data-clm-doc-compare="1">'
            '%s%s%s%s'
            '<div class="o_clm_doc_body">'
            '<div class="o_clm_doc_pane o_clm_doc_pane_left" data-clm-scroll-pane="left">%s</div>'
            '<div class="o_clm_doc_gutter" aria-hidden="true">%s</div>'
            '<div class="o_clm_doc_pane o_clm_doc_pane_right" data-clm-scroll-pane="right">%s</div>'
            '</div></div>'
        ) % (
            self._embedded_css(),
            self._document_compare_css(),
            toolbar,
            headers,
            Markup('').join(left_rows),
            Markup('').join(gutter_rows),
            Markup('').join(right_rows),
        )

    def _document_compare_rows(self, base_lines, target_lines):
        left_rows, right_rows, gutter_rows, change_ids = [], [], [], []
        change_id = 0
        matcher = difflib.SequenceMatcher(None, base_lines, target_lines, autojunk=False)
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == 'equal':
                for offset, line in enumerate(base_lines[i1:i2]):
                    left_rows.append(self._doc_line('ctx', escape(line)))
                    right_rows.append(self._doc_line('ctx', escape(line)))
                    gutter_rows.append(Markup('<div class="o_clm_doc_tick o_clm_doc_tick_ctx"></div>'))
                continue

            change_id += 1
            change_ids.append(change_id)
            before = base_lines[i1:i2]
            after = target_lines[j1:j2]

            if tag == 'delete':
                for index, line in enumerate(before):
                    anchor = change_id if index == 0 else None
                    left_rows.append(self._doc_line('del', escape(line), change_id, anchor=anchor))
                    right_rows.append(self._doc_line('blank', ''))
                    gutter_rows.append(self._doc_tick('del', change_id))
            elif tag == 'insert':
                for index, line in enumerate(after):
                    anchor = change_id if index == 0 else None
                    left_rows.append(self._doc_line('blank', ''))
                    right_rows.append(self._doc_line('add', escape(line), change_id, anchor=anchor))
                    gutter_rows.append(self._doc_tick('add', change_id))
            else:  # replace
                paired = max(len(before), len(after))
                for index in range(paired):
                    old_line = before[index] if index < len(before) else ''
                    new_line = after[index] if index < len(after) else ''
                    anchor = change_id if index == 0 else None
                    if old_line and new_line:
                        word = self._word_change(old_line, new_line)
                        left_rows.append(self._doc_line(
                            'del', word['marked_old'], change_id, anchor=anchor,
                        ))
                        right_rows.append(self._doc_line('add', word['marked_new'], change_id))
                    elif old_line:
                        left_rows.append(self._doc_line(
                            'del', escape(old_line), change_id, anchor=anchor,
                        ))
                        right_rows.append(self._doc_line('blank', ''))
                    else:
                        left_rows.append(self._doc_line('blank', ''))
                        right_rows.append(self._doc_line(
                            'add', escape(new_line), change_id, anchor=anchor,
                        ))
                    gutter_rows.append(self._doc_tick('chg', change_id))
        return left_rows, right_rows, gutter_rows, change_ids

    @staticmethod
    def _doc_line(kind, text_html, change_id=None, anchor=None):
        id_attr = Markup(' id="clm-chg-%s"') % anchor if anchor else Markup('')
        return Markup(
            '<div class="o_clm_doc_line o_clm_doc_line_%s"%s data-change="%s">'
            '<div class="o_clm_doc_line_inner">%s</div></div>'
        ) % (kind, id_attr, change_id or '', text_html or Markup('&nbsp;'))

    @staticmethod
    def _doc_tick(kind, change_id):
        return Markup(
            '<div class="o_clm_doc_tick o_clm_doc_tick_%s" data-change="%s" title="Change %s"></div>'
        ) % (kind, change_id, change_id)

    @staticmethod
    def _document_compare_css():
        return Markup("""<style>
.o_clm_doc_compare {
  --doc-del-bg: rgba(220, 38, 38, 0.22);
  --doc-add-bg: rgba(34, 197, 94, 0.22);
  --doc-del-mark: rgba(220, 38, 38, 0.45);
  --doc-add-mark: rgba(34, 197, 94, 0.45);
  --doc-border: color-mix(in srgb, currentColor 14%, transparent);
  --doc-page: color-mix(in srgb, currentColor 3%, transparent);
  color: inherit;
  border: 1px solid var(--doc-border);
  border-radius: 10px;
  overflow: hidden;
  background: var(--doc-page);
}
.o_clm_doc_toolbar {
  display: flex; flex-wrap: wrap; gap: 0.65rem 1rem; align-items: center;
  padding: 0.55rem 0.85rem; border-bottom: 1px solid var(--doc-border);
  background: color-mix(in srgb, currentColor 5%, transparent);
  font-size: 12.5px;
}
.o_clm_doc_mode.is-active {
  font-weight: 700; padding: 0.2rem 0.55rem; border-radius: 999px;
  background: color-mix(in srgb, #0d9488 28%, transparent);
}
.o_clm_doc_stat_del { color: #f87171; font-weight: 600; }
.o_clm_doc_stat_add { color: #4ade80; font-weight: 600; }
.o_clm_doc_nav { margin-left: auto; display: flex; gap: 0.5rem; }
.o_clm_doc_nav_btn {
  color: inherit; text-decoration: none; padding: 0.2rem 0.5rem;
  border: 1px solid var(--doc-border); border-radius: 6px;
}
.o_clm_doc_headers {
  display: grid; grid-template-columns: 1fr 18px 1fr;
  border-bottom: 1px solid var(--doc-border); font-size: 12px; font-weight: 700;
  text-transform: uppercase; letter-spacing: 0.03em;
}
.o_clm_doc_header { padding: 0.45rem 0.85rem; }
.o_clm_doc_header_left { border-right: 1px solid var(--doc-border); }
.o_clm_doc_header_right { border-left: 1px solid var(--doc-border); }
.o_clm_doc_body {
  display: grid; grid-template-columns: 1fr 18px 1fr;
  max-height: 72vh; overflow: hidden;
}
.o_clm_doc_pane {
  overflow: auto; max-height: 72vh; padding: 0.75rem 0.85rem 1.25rem;
  font-family: "Liberation Serif", "Times New Roman", Times, serif;
  font-size: 14px; line-height: 1.55;
  scroll-behavior: auto;
}
.o_clm_doc_pane_left { border-right: 1px solid var(--doc-border); }
.o_clm_doc_pane_right { border-left: 1px solid var(--doc-border); }
.o_clm_doc_gutter {
  overflow: hidden; background: color-mix(in srgb, currentColor 4%, transparent);
  border-left: 1px solid var(--doc-border); border-right: 1px solid var(--doc-border);
}
.o_clm_doc_line { min-height: 1.55em; margin: 0; }
.o_clm_doc_line_inner { padding: 0.05rem 0.25rem; border-radius: 3px; overflow-wrap: anywhere; }
.o_clm_doc_line_blank .o_clm_doc_line_inner { min-height: 1.55em; opacity: 0.25; }
.o_clm_doc_line_del .o_clm_doc_line_inner { background: var(--doc-del-bg); }
.o_clm_doc_line_add .o_clm_doc_line_inner { background: var(--doc-add-bg); }
.o_clm_doc_line_del del {
  color: inherit; background: var(--doc-del-mark); text-decoration: line-through;
}
.o_clm_doc_line_add ins {
  color: inherit; background: var(--doc-add-mark); text-decoration: none; font-weight: 600;
}
.o_clm_doc_tick { min-height: 1.55em; position: relative; }
.o_clm_doc_tick_del::after, .o_clm_doc_tick_add::after, .o_clm_doc_tick_chg::after {
  content: ""; position: absolute; left: 4px; right: 4px; top: 0.35em; height: 0.85em;
  border-radius: 2px;
}
.o_clm_doc_tick_del::after { background: #ef4444; }
.o_clm_doc_tick_add::after { background: #22c55e; }
.o_clm_doc_tick_chg::after {
  background: linear-gradient(180deg, #ef4444 0 50%, #22c55e 50% 100%);
}
</style>""")

    def _resolve_compare_lines(self, base_version, target_version):
        """Prefer frozen wording when it differs; otherwise compare DOCX text.

        Lifecycle steps (negotiated → signature draft → executed) often copy the
        same ``content_snapshot`` even when the attached Word file changed
        (portal upload, DOCX editor). Snapshot-only compare then falsely reports
        "No wording changes".
        """
        both_snap = self._both_have_content_snapshot(base_version, target_version)
        if both_snap:
            base_snap = self._extract_lines(base_version, use_snapshot=True)
            target_snap = self._extract_lines(target_version, use_snapshot=True)
            if base_snap != target_snap:
                return base_snap, target_snap, 'snapshot'

            doc_lines = self._try_document_lines(base_version, target_version)
            if doc_lines is not None:
                base_doc, target_doc = doc_lines
                if base_doc != target_doc:
                    return base_doc, target_doc, 'document'
                return base_doc, target_doc, 'document'
            return base_snap, target_snap, 'snapshot'

        base_lines = self._extract_lines(base_version, use_snapshot=False)
        target_lines = self._extract_lines(target_version, use_snapshot=False)
        return base_lines, target_lines, 'document'

    def _try_document_lines(self, base_version, target_version):
        """Return (base_lines, target_lines) from DOCX, or None if unavailable."""
        try:
            base_doc = self._extract_text(base_version).splitlines()
            target_doc = self._extract_text(target_version).splitlines()
        except UserError:
            return None
        return base_doc, target_doc

    @staticmethod
    def _identical_reason(base_version, target_version, source):
        base_type = base_version.version_type
        target_type = target_version.version_type
        if {base_type, target_type} == {'signature_draft', 'executed'} or (
            base_type == 'signature_draft' and target_type == 'executed'
        ):
            return 'execution'
        if base_type in ('negotiated_draft', 'legal_draft', 'internal_draft') and (
            target_type == 'signature_draft'
        ):
            return 'prepare_signature'
        if source == 'document':
            return 'same_document'
        return 'same_wording'

    def render_html(self, result, diff_view='unified'):
        """Full redline HTML for the Odoo form (and PDF when view=unified)."""
        if result.get('identical'):
            return self._render_identical(result)

        # Embed both panels; CSS + data-view shows the active one.
        unified = self._render_unified(result)
        split = self._render_side_by_side(result)
        active = 'side_by_side' if diff_view == 'side_by_side' else 'unified'
        return Markup(
            '<div class="o_clm_redline" data-view="%s">'
            '%s'
            '%s'
            '<div class="o_clm_redline_layout">'
            '%s'
            '<div class="o_clm_redline_main">'
            '<div class="o_clm_redline_panel o_clm_redline_panel_unified">%s</div>'
            '<div class="o_clm_redline_panel o_clm_redline_panel_split">%s</div>'
            '</div></div></div>'
        ) % (
            active,
            self._embedded_css(),
            self._render_stats_bar(result),
            self._render_navigator(result),
            unified,
            split,
        )

    # -------------------------------------------------------------------------
    # Extraction
    # -------------------------------------------------------------------------

    @staticmethod
    def _both_have_content_snapshot(base_version, target_version):
        if 'content_snapshot' not in base_version._fields:
            return False
        return bool(base_version.sudo().content_snapshot) and bool(
            target_version.sudo().content_snapshot
        )

    def _extract_lines(self, version, use_snapshot=None):
        """The lines to compare for one version."""
        if use_snapshot is None:
            use_snapshot = (
                'content_snapshot' in version._fields
                and bool(version.sudo().content_snapshot)
            )
        if use_snapshot:
            return version._clm_content_lines()
        return self._extract_text(version).splitlines()

    def _extract_text(self, version):
        """
        Extracts plain text from the version's underlying document.
        Assumes it's a DOCX for Phase 5.
        """
        if not PYTHON_DOCX_AVAILABLE:
            raise UserError(
                "Redlining requires the 'python-docx' library. "
                "Please install it on the Odoo server: pip install python-docx"
            )

        attachment = False
        if version.document_id and version.document_id.attachment_id:
            attachment = version.document_id.attachment_id
        elif version.generated_docx_id:
            attachment = version.generated_docx_id
        elif hasattr(version, '_clm_generate_document'):
            attachment = version._clm_generate_document('docx')

        if not attachment:
            raise UserError(
                f"Version {version.version_number} has no underlying document attachment."
            )

        mime = (attachment.mimetype or '')
        name = (attachment.name or '').lower()
        if (
            'wordprocessingml.document' not in mime
            and not name.endswith('.docx')
        ):
            raise UserError(
                f"Version {version.version_number} is not a DOCX file. "
                "Phase 5 redlining only supports DOCX format."
            )

        if hasattr(version, '_clm_attachment_bytes'):
            docx_bytes = version._clm_attachment_bytes(attachment)
        else:
            # Prefer attachment.raw — datas can be b'24.57 Kb' under bin_size.
            docx_bytes = attachment.with_context(bin_size=False).raw
            if not docx_bytes:
                datas = attachment.with_context(bin_size=False).datas
                if isinstance(datas, str):
                    datas = datas.encode('ascii')
                docx_bytes = base64.b64decode(datas, validate=False)
        docx_file = io.BytesIO(docx_bytes)

        try:
            doc = docx.Document(docx_file)
            lines = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
            # Include table rows (e.g. Notice Period on the cover sheet).
            for table in doc.tables:
                for row in table.rows:
                    cells = [cell.text.strip() for cell in row.cells if cell.text.strip()]
                    if cells:
                        lines.append(' | '.join(cells))
            return "\n".join(lines)
        except Exception as e:
            raise UserError(f"Failed to read DOCX file for version {version.version_number}: {e}")

    @staticmethod
    def _version_short_label(version):
        type_labels = dict(
            version._fields['version_type']._description_selection(version.env)
        )
        parts = ['v%s' % version.version_number]
        label = type_labels.get(version.version_type)
        if label:
            parts.append(label)
        return ' · '.join(parts)

    # -------------------------------------------------------------------------
    # Hunk / change building
    # -------------------------------------------------------------------------

    def _build_hunks(self, base_lines, target_lines):
        hunks = []
        matcher = difflib.SequenceMatcher(None, base_lines, target_lines, autojunk=False)
        change_id = 0
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == 'equal':
                continue
            change_id += 1
            rows = []
            if tag in ('replace', 'delete'):
                for offset, line in enumerate(base_lines[i1:i2]):
                    rows.append({
                        'kind': 'del',
                        'base_no': i1 + offset + 1,
                        'target_no': None,
                        'text': line,
                        'marked': None,
                    })
            if tag in ('replace', 'insert'):
                for offset, line in enumerate(target_lines[j1:j2]):
                    rows.append({
                        'kind': 'add',
                        'base_no': None,
                        'target_no': j1 + offset + 1,
                        'text': line,
                        'marked': None,
                    })
            # Word-level marks for paired replacements (old vs new line).
            if tag == 'replace':
                before = base_lines[i1:i2]
                after = target_lines[j1:j2]
                del_rows = [r for r in rows if r['kind'] == 'del']
                add_rows = [r for r in rows if r['kind'] == 'add']
                for index in range(min(len(before), len(after))):
                    word = self._word_change(before[index], after[index])
                    if index < len(del_rows):
                        del_rows[index]['marked'] = word['marked_old']
                    if index < len(add_rows):
                        add_rows[index]['marked'] = word['marked_new']
            elif tag == 'delete':
                for row in rows:
                    word = self._word_change(row['text'], '')
                    row['marked'] = word['marked_old']
            elif tag == 'insert':
                for row in rows:
                    word = self._word_change('', row['text'])
                    row['marked'] = word['marked_new']

            title = self._hunk_title(rows, change_id)
            hunks.append({
                'id': change_id,
                'title': title,
                'tag': tag,
                'rows': rows,
                'context_before': base_lines[max(0, i1 - 2):i1],
                'context_after': base_lines[i2:i2 + 2],
                'base_start': i1 + 1 if i2 > i1 else (i1 if i1 else 1),
                'target_start': j1 + 1 if j2 > j1 else (j1 if j1 else 1),
            })
        return hunks

    def _hunk_title(self, rows, change_id):
        for row in rows:
            text = (row.get('text') or '').strip()
            if text and _HEADING_RE.match(text):
                return text[:80]
        for row in rows:
            text = (row.get('text') or '').strip()
            if text:
                return (text[:72] + '…') if len(text) > 72 else text
        return 'Change %s' % change_id

    # -------------------------------------------------------------------------
    # Change summary (word-level) — kept for tests and PDF detail
    # -------------------------------------------------------------------------

    @classmethod
    def summarize_changes(cls, base_lines, target_lines):
        """Every changed line, reduced to the words removed and the words added.

        Returns a list of {'before', 'after', 'removed', 'added', 'marked'} where
        `marked` is the line with <del>/<ins> around the changed words.
        """
        changes = []
        matcher = difflib.SequenceMatcher(None, base_lines, target_lines, autojunk=False)
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == 'equal':
                continue
            before, after = base_lines[i1:i2], target_lines[j1:j2]
            for index in range(max(len(before), len(after))):
                changes.append(cls._word_change(
                    before[index] if index < len(before) else '',
                    after[index] if index < len(after) else '',
                ))
        return changes

    @staticmethod
    def _word_change(before, after):
        old, new = before.split(), after.split()
        removed, added, marked, marked_old, marked_new = [], [], [], [], []
        matcher = difflib.SequenceMatcher(None, old, new, autojunk=False)
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == 'equal':
                chunk = escape(' '.join(old[i1:i2]))
                marked.append(chunk)
                marked_old.append(chunk)
                marked_new.append(chunk)
                continue
            if i2 > i1:
                chunk = ' '.join(old[i1:i2])
                removed.append(chunk)
                marked.append(Markup('<del>%s</del>') % chunk)
                marked_old.append(Markup('<del>%s</del>') % chunk)
            if j2 > j1:
                chunk = ' '.join(new[j1:j2])
                added.append(chunk)
                marked.append(Markup('<ins>%s</ins>') % chunk)
                marked_new.append(Markup('<ins>%s</ins>') % chunk)
        return {
            'before': before,
            'after': after,
            'removed': removed,
            'added': added,
            'marked': Markup(' ').join(marked),
            'marked_old': Markup(' ').join(marked_old),
            'marked_new': Markup(' ').join(marked_new),
        }

    # -------------------------------------------------------------------------
    # HTML rendering
    # -------------------------------------------------------------------------

    def _render_identical(self, result):
        reason = result.get('identical_reason') or 'same_wording'
        title, detail = self._identical_copy(result, reason)
        return Markup(
            '<div class="o_clm_redline o_clm_redline_identical">'
            '%s'
            '<div class="o_clm_redline_empty">'
            '<div class="o_clm_redline_empty_title">%s</div>'
            '<p>%s</p>'
            '<p>No differences found between %s and %s.</p>'
            '</div></div>'
        ) % (
            self._embedded_css(),
            escape(title),
            escape(detail),
            escape(result['base_label']),
            escape(result['target_label']),
        )

    @staticmethod
    def _identical_copy(result, reason):
        if reason == 'execution':
            return (
                'Same wording as Signature Draft',
                'Executed versions keep the Signature Draft wording and attach '
                'the signed file. Pick an earlier draft (for example the version '
                'before Signature Draft) to see clause changes.',
            )
        if reason == 'prepare_signature':
            return (
                'Same wording as previous draft',
                'Preparing a Signature Draft does not change contract wording. '
                'Compare with an earlier version that edited clauses or the Word file.',
            )
        if reason == 'same_document':
            return (
                'No wording changes',
                'The attached documents have the same extractable text.',
            )
        return (
            'No wording changes',
            'The frozen contract wording matches between these versions.',
        )

    def _render_stats_bar(self, result):
        source = result.get('compare_source')
        source_note = ''
        if source == 'document':
            source_note = Markup(
                '<span class="o_clm_redline_stat_src">'
                'Compared from Word documents (frozen wording matched)'
                '</span>'
            )
        elif source == 'snapshot':
            source_note = Markup(
                '<span class="o_clm_redline_stat_src">Compared from frozen contract wording</span>'
            )
        return Markup(
            '<div class="o_clm_redline_stats" role="status">'
            '<span class="o_clm_redline_stat_add">+%s additions</span>'
            '<span class="o_clm_redline_stat_del">−%s deletions</span>'
            '<span class="o_clm_redline_stat_chg">%s changes</span>'
            '<span class="o_clm_redline_stat_pair">%s → %s</span>'
            '%s'
            '</div>'
        ) % (
            result['addition_count'],
            result['deletion_count'],
            result['change_count'],
            escape(result['base_label']),
            escape(result['target_label']),
            source_note,
        )

    def _render_navigator(self, result):
        items = Markup('').join(
            Markup(
                '<a class="o_clm_redline_nav_item" href="#clm-change-%s">'
                '<span class="o_clm_redline_nav_index">%s</span>'
                '<span class="o_clm_redline_nav_title">%s</span>'
                '</a>'
            ) % (hunk['id'], hunk['id'], escape(hunk['title']))
            for hunk in result['hunks']
        )
        return Markup(
            '<nav class="o_clm_redline_nav" aria-label="Summary of Changes">'
            '<div class="o_clm_redline_nav_heading">Summary of Changes</div>'
            '%s</nav>'
        ) % items

    def _render_unified(self, result):
        blocks = []
        for hunk in result['hunks']:
            lines = []
            for ctx in hunk['context_before']:
                lines.append(self._unified_line('ctx', None, None, escape(ctx)))
            for row in hunk['rows']:
                text = row.get('marked') or escape(row['text'])
                lines.append(self._unified_line(
                    row['kind'], row['base_no'], row['target_no'], text,
                ))
            for ctx in hunk['context_after']:
                lines.append(self._unified_line('ctx', None, None, escape(ctx)))
            blocks.append(Markup(
                '<div class="o_clm_redline_hunk" id="clm-change-%s">'
                '<div class="o_clm_redline_hunk_head">'
                '<span class="o_clm_redline_hunk_id">#%s</span>'
                '<span class="o_clm_redline_hunk_title">%s</span>'
                '</div>'
                '<div class="o_clm_redline_code">%s</div></div>'
            ) % (
                hunk['id'], hunk['id'], escape(hunk['title']),
                Markup('').join(lines),
            ))
        return Markup(
            '<div class="o_clm_redline_unified">'
            '<div class="o_clm_redline_view_label">Unified redline</div>'
            '%s</div>'
        ) % Markup('').join(blocks)

    @staticmethod
    def _unified_line(kind, base_no, target_no, text):
        sign = {'add': '+', 'del': '−', 'ctx': ' '}.get(kind, ' ')
        return Markup(
            '<div class="o_clm_redline_line o_clm_redline_line_%s">'
            '<span class="o_clm_redline_ln o_clm_redline_ln_base">%s</span>'
            '<span class="o_clm_redline_ln o_clm_redline_ln_target">%s</span>'
            '<span class="o_clm_redline_sign">%s</span>'
            '<span class="o_clm_redline_text">%s</span>'
            '</div>'
        ) % (
            kind,
            base_no or '',
            target_no or '',
            sign,
            text,
        )

    def _render_side_by_side(self, result):
        blocks = []
        for hunk in result['hunks']:
            left_rows = [r for r in hunk['rows'] if r['kind'] == 'del']
            right_rows = [r for r in hunk['rows'] if r['kind'] == 'add']
            # Pad context onto both sides
            left = [
                self._split_cell('ctx', None, escape(t))
                for t in hunk['context_before']
            ] + [
                self._split_cell(
                    'del', r['base_no'],
                    r.get('marked') or escape(r['text']),
                )
                for r in left_rows
            ] + [
                self._split_cell('ctx', None, escape(t))
                for t in hunk['context_after']
            ]
            right = [
                self._split_cell('ctx', None, escape(t))
                for t in hunk['context_before']
            ] + [
                self._split_cell(
                    'add', r['target_no'],
                    r.get('marked') or escape(r['text']),
                )
                for r in right_rows
            ] + [
                self._split_cell('ctx', None, escape(t))
                for t in hunk['context_after']
            ]
            # Equalize column heights for alignment
            while len(left) < len(right):
                left.append(self._split_cell('blank', None, ''))
            while len(right) < len(left):
                right.append(self._split_cell('blank', None, ''))

            pairs = Markup('').join(
                Markup('<div class="o_clm_redline_split_row">%s%s</div>') % (l, r)
                for l, r in zip(left, right)
            )
            blocks.append(Markup(
                '<div class="o_clm_redline_hunk" id="clm-change-%s-split">'
                '<div class="o_clm_redline_hunk_head">'
                '<span class="o_clm_redline_hunk_id">#%s</span>'
                '<span class="o_clm_redline_hunk_title">%s</span>'
                '</div>'
                '<div class="o_clm_redline_split_cols">'
                '<div class="o_clm_redline_split_head_left">%s (Original)</div>'
                '<div class="o_clm_redline_split_head_right">%s (Revised)</div>'
                '</div>'
                '%s</div>'
            ) % (
                hunk['id'], hunk['id'], escape(hunk['title']),
                escape(result['base_label']), escape(result['target_label']),
                pairs,
            ))
        return Markup(
            '<div class="o_clm_redline_split">'
            '<div class="o_clm_redline_view_label">Side-by-side</div>'
            '%s</div>'
        ) % Markup('').join(blocks)

    @staticmethod
    def _split_cell(kind, number, text):
        return Markup(
            '<div class="o_clm_redline_split_cell o_clm_redline_line_%s">'
            '<span class="o_clm_redline_ln">%s</span>'
            '<span class="o_clm_redline_text">%s</span>'
            '</div>'
        ) % (kind, number or '', text)

    @staticmethod
    def _embedded_css():
        # Self-contained so the Html field and PDF export both look correct,
        # including Odoo dark theme (color-mix / currentColor aware where possible).
        return Markup("""<style>
.o_clm_redline {
  --clm-rl-add-bg: color-mix(in srgb, #16a34a 22%, transparent);
  --clm-rl-del-bg: color-mix(in srgb, #dc2626 22%, transparent);
  /* Inherit page text colour so dark mode stays readable; tint only the bg. */
  --clm-rl-add-fg: inherit;
  --clm-rl-del-fg: inherit;
  --clm-rl-add-mark-bg: color-mix(in srgb, #16a34a 38%, transparent);
  --clm-rl-del-mark-bg: color-mix(in srgb, #dc2626 38%, transparent);
  --clm-rl-border: color-mix(in srgb, currentColor 16%, transparent);
  --clm-rl-muted: color-mix(in srgb, currentColor 55%, transparent);
  --clm-rl-surface: color-mix(in srgb, currentColor 4%, transparent);
                font-size: 13px;
  line-height: 1.45;
  color: inherit;
}
.o_clm_redline_stats {
  display: flex; flex-wrap: wrap; gap: 0.75rem 1.25rem;
  margin: 0 0 1rem; padding: 0.65rem 0.85rem;
  border: 1px solid var(--clm-rl-border); border-radius: 8px;
  background: var(--clm-rl-surface);
}
.o_clm_redline_stat_add { color: #16a34a; font-weight: 600; }
.o_clm_redline_stat_del { color: #dc2626; font-weight: 600; }
.o_dark .o_clm_redline_stat_add,
html[data-color-scheme="dark"] .o_clm_redline_stat_add { color: #86efac; }
.o_dark .o_clm_redline_stat_del,
html[data-color-scheme="dark"] .o_clm_redline_stat_del { color: #fca5a5; }
.o_clm_redline_stat_chg, .o_clm_redline_stat_pair { color: var(--clm-rl-muted); }
.o_clm_redline_layout {
  display: grid; grid-template-columns: minmax(11rem, 16rem) 1fr; gap: 1rem;
  align-items: start;
}
@media (max-width: 900px) {
  .o_clm_redline_layout { grid-template-columns: 1fr; }
}
.o_clm_redline_nav {
  position: sticky; top: 0.5rem; max-height: 70vh; overflow: auto;
  border: 1px solid var(--clm-rl-border); border-radius: 8px;
  background: var(--clm-rl-surface); padding: 0.5rem 0;
}
.o_clm_redline_nav_heading {
  padding: 0.35rem 0.75rem 0.55rem; font-weight: 600; font-size: 12px;
  text-transform: uppercase; letter-spacing: 0.04em; color: var(--clm-rl-muted);
}
.o_clm_redline_nav_item {
  display: flex; gap: 0.5rem; align-items: flex-start;
  padding: 0.4rem 0.75rem; color: inherit; text-decoration: none;
  border-left: 3px solid transparent;
}
.o_clm_redline_nav_item:hover { background: var(--clm-rl-surface); border-left-color: #0d9488; }
.o_clm_redline_nav_index {
  flex: 0 0 auto; min-width: 1.25rem; color: var(--clm-rl-muted); font-variant-numeric: tabular-nums;
}
.o_clm_redline_nav_title { overflow-wrap: anywhere; }
.o_clm_redline_view_label {
  margin-bottom: 0.5rem; font-size: 12px; font-weight: 600;
  text-transform: uppercase; letter-spacing: 0.04em; color: var(--clm-rl-muted);
}
.o_clm_redline_hunk {
  margin-bottom: 1rem; border: 1px solid var(--clm-rl-border); border-radius: 8px;
  overflow: hidden; scroll-margin-top: 1rem;
}
.o_clm_redline_hunk_head {
  display: flex; gap: 0.5rem; align-items: baseline;
  padding: 0.45rem 0.75rem; border-bottom: 1px solid var(--clm-rl-border);
  background: var(--clm-rl-surface);
}
.o_clm_redline_hunk_id { color: var(--clm-rl-muted); font-weight: 600; }
.o_clm_redline_code { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 12.5px; }
.o_clm_redline_line {
  display: grid; grid-template-columns: 2.5rem 2.5rem 1.1rem 1fr;
  gap: 0.25rem; padding: 0.15rem 0.5rem; border-top: 1px solid transparent;
}
.o_clm_redline_ln { color: var(--clm-rl-muted); text-align: right; font-variant-numeric: tabular-nums; user-select: none; }
.o_clm_redline_sign { text-align: center; user-select: none; }
.o_clm_redline_text { white-space: pre-wrap; overflow-wrap: anywhere; }
.o_clm_redline_line_add { background: var(--clm-rl-add-bg); color: inherit; }
.o_clm_redline_line_del { background: var(--clm-rl-del-bg); color: inherit; }
.o_clm_redline_line_add .o_clm_redline_text,
.o_clm_redline_line_del .o_clm_redline_text { color: inherit; }
.o_clm_redline_line_add .o_clm_redline_sign { color: #16a34a; font-weight: 700; }
.o_clm_redline_line_del .o_clm_redline_sign { color: #dc2626; font-weight: 700; }
.o_dark .o_clm_redline_line_add .o_clm_redline_sign,
html[data-color-scheme="dark"] .o_clm_redline_line_add .o_clm_redline_sign { color: #86efac; }
.o_dark .o_clm_redline_line_del .o_clm_redline_sign,
html[data-color-scheme="dark"] .o_clm_redline_line_del .o_clm_redline_sign { color: #fca5a5; }
.o_clm_redline del {
  text-decoration: line-through;
  background: var(--clm-rl-del-mark-bg);
  color: inherit;
  border-radius: 2px;
  padding: 0 0.12em;
}
.o_clm_redline ins {
  text-decoration: none;
  background: var(--clm-rl-add-mark-bg);
  color: inherit;
  font-weight: 600;
  border-radius: 2px;
  padding: 0 0.12em;
}
.o_clm_redline_split_cols {
  display: grid; grid-template-columns: 1fr 1fr; gap: 0;
  border-bottom: 1px solid var(--clm-rl-border); background: var(--clm-rl-surface);
  font-weight: 600; font-size: 12px;
}
.o_clm_redline_split_head_left, .o_clm_redline_split_head_right { padding: 0.4rem 0.65rem; }
.o_clm_redline_split_head_right { border-left: 1px solid var(--clm-rl-border); }
.o_clm_redline_split_row { display: grid; grid-template-columns: 1fr 1fr; }
.o_clm_redline_split_cell {
  display: grid; grid-template-columns: 2.5rem 1fr; gap: 0.35rem;
  padding: 0.15rem 0.5rem; border-top: 1px solid color-mix(in srgb, currentColor 6%, transparent);
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 12.5px;
}
.o_clm_redline_split_row .o_clm_redline_split_cell:last-child { border-left: 1px solid var(--clm-rl-border); }
.o_clm_redline_line_blank { min-height: 1.35em; background: transparent; }
.o_clm_redline_empty { text-align: center; padding: 2rem 1rem; color: var(--clm-rl-muted); }
.o_clm_redline_empty_title { font-size: 1rem; font-weight: 600; color: inherit; margin-bottom: 0.35rem; }
/* View switching driven by data-view on the root (set by the form). */
.o_clm_redline[data-view="unified"] .o_clm_redline_panel_split { display: none !important; }
.o_clm_redline[data-view="side_by_side"] .o_clm_redline_panel_unified,
.o_clm_redline[data-view="split"] .o_clm_redline_panel_unified { display: none !important; }
.o_clm_redline[data-view="side_by_side"] .o_clm_redline_panel_split,
.o_clm_redline[data-view="split"] .o_clm_redline_panel_split { display: block !important; }
.o_clm_redline[data-view="unified"] .o_clm_redline_panel_unified { display: block !important; }
.o_clm_pdf_changes .o_clm_changes_grid {
  display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; margin-top: 0.75rem;
}
.o_clm_pdf_changes .o_clm_changes_col {
  border: 1px solid var(--clm-rl-border); border-radius: 8px; overflow: hidden;
}
.o_clm_pdf_changes .o_clm_changes_head {
  display: flex; justify-content: space-between; align-items: center;
  padding: 0.55rem 0.85rem; font-weight: 700; font-size: 13px;
}
.o_clm_pdf_changes .o_clm_changes_head_del {
  background: rgba(220, 38, 38, 0.18); color: #fecaca;
}
.o_clm_pdf_changes .o_clm_changes_head_add {
  background: rgba(22, 163, 74, 0.18); color: #bbf7d0;
}
.o_clm_pdf_changes .o_clm_changes_list { max-height: 28rem; overflow: auto; padding: 0.35rem 0; }
.o_clm_pdf_changes .o_clm_change_item {
  display: grid; grid-template-columns: 1.5rem 1fr; gap: 0.5rem;
  padding: 0.45rem 0.75rem; font-size: 13px; line-height: 1.45;
  border-top: 1px solid color-mix(in srgb, currentColor 6%, transparent);
}
.o_clm_pdf_changes .o_clm_change_badge { font-weight: 700; text-align: center; }
.o_clm_pdf_changes .o_clm_change_del { background: rgba(220, 38, 38, 0.14); }
.o_clm_pdf_changes .o_clm_change_add { background: rgba(22, 163, 74, 0.14); }
.o_clm_pdf_changes .o_clm_change_del .o_clm_change_badge { color: #f87171; }
.o_clm_pdf_changes .o_clm_change_add .o_clm_change_badge { color: #4ade80; }
.o_clm_pdf_changes .o_clm_change_body { color: inherit; white-space: pre-wrap; overflow-wrap: anywhere; }
.o_clm_pdf_changes .o_clm_change_empty {
  padding: 1.25rem; text-align: center; color: var(--clm-rl-muted);
}
</style>""")
