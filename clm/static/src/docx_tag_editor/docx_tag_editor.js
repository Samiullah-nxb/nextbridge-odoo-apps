/** @odoo-module **/

import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { standardFieldProps } from "@web/views/fields/standard_field_props";
import { useService } from "@web/core/utils/hooks";
import { useRecordObserver } from "@web/model/relational_model/utils";
import { Plugin } from "@html_editor/plugin";
import { MAIN_PLUGINS } from "@html_editor/plugin_sets";
import { Wysiwyg } from "@html_editor/wysiwyg";
import {
    Component,
    markup,
    onMounted,
    onPatched,
    onWillDestroy,
    onWillUpdateProps,
    useExternalListener,
    useRef,
    useState,
} from "@odoo/owl";

/**
 * Consumes Tab in the html_editor hotkey path so we can close incomplete
 * Jinja placeholders before TabulationPlugin inserts an indent tab.
 */
function makeClmPlaceholderTabPlugin(component) {
    return class ClmPlaceholderTabPlugin extends Plugin {
        static id = "clmPlaceholderTabFix";
        resources = {
            tab_overrides: () => component._onEditorTabOverride(),
        };
    };
}

export class DocxTagEditorField extends Component {
    static template = "clm.DocxTagEditor";
    static components = { Wysiwyg };
    static props = { ...standardFieldProps };

    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.rootRef = useRef("root");
        this.headerRootRef = useRef("headerRoot");
        this.readonlyHtmlRef = useRef("readonlyHtml");
        this.tagComboRef = useRef("tagCombo");
        this.tagInputRef = useRef("tagInput");
        this.tagListRef = useRef("tagList");
        this.clauseComboRef = useRef("clauseCombo");
        this.clauseInputRef = useRef("clauseInput");
        this.clauseListRef = useRef("clauseList");
        this.liveRegionRef = useRef("liveRegion");
        this._blobUrls = [];
        this._blobToDataUri = new Map();
        this._preservedImgHtml = [];
        this._preservedImgSrcs = new Set();
        this.editor = null;
        this._savedRange = null;
        this._loadSeq = 0;
        this._observedKey = null;
        this._uid = `clm_docx_${Math.random().toString(36).slice(2, 9)}`;
        this.state = useState({
            loading: true,
            busy: false,
            error: null,
            documentName: "",
            versionName: "",
            versionNumber: false,
            versionCount: 0,
            isOriginalActive: false,
            canSaveInplace: false,
            headers: [],
            paragraphs: [],
            documentHtml: "",
            tags: [],
            errors: [],
            warnings: [],
            issueSpans: [],
            readonly: false,
            dirty: false,
            loadToken: 0,
            editorReady: false,
            tagQuery: "",
            tagMenuOpen: false,
            tagHighlight: 0,
            clauseQuery: "",
            clauseMenuOpen: false,
            clauseHighlight: 0,
            clauseSetCompactLines: [],
            liveMessage: "",
            errorsExpanded: false,
            warningsExpanded: false,
        });

        // Reloads when Base DOCX is set/changed or the record gets an id after save.
        useRecordObserver((record) => {
            const key = `${record.resId || 0}:${this._docId(record.data.base_document_id) || 0}:${
                record.data.docx_editor_anchor || ""
            }`;
            if (key === this._observedKey) {
                return;
            }
            this._observedKey = key;
            return this.loadData({ record });
        });
        onWillUpdateProps(async (nextProps) => {
            const nextId = nextProps.record.resId;
            const nextDoc = this._docId(nextProps.record.data.base_document_id);
            const curDoc = this._docId(this.props.record.data.base_document_id);
            if (nextId !== this.props.record.resId || nextDoc !== curDoc) {
                await this.loadData(nextProps);
            }
        });
        onMounted(() => this._syncHeaderDom());
        onPatched(() => {
            this._syncHeaderDom();
            this._scrollHighlightedOptionIntoView();
        });
        onWillDestroy(() => {
            this._unbindEditableTabFix();
            this._revokeBlobUrls();
            this.editor = null;
        });
        useExternalListener(window, "pointerdown", this.onWindowPointerDown.bind(this));
        // Keep last caret while the tag combobox steals focus.
        useExternalListener(document, "selectionchange", () => {
            this._saveSelectionFromDocument();
        });
    }

    get tagQueryNormalized() {
        return (this.state.tagQuery || "").trim().toLowerCase();
    }

    get clauseQueryNormalized() {
        return (this.state.clauseQuery || "").trim().toLowerCase();
    }

    _matchesQuery(tag, query) {
        if (!query) {
            return true;
        }
        const haystack = [tag.key, tag.label, tag.insert, tag.group]
            .filter(Boolean)
            .join(" ")
            .toLowerCase();
        return query.split(/\s+/).every((token) => haystack.includes(token));
    }

    get allFieldTags() {
        return (this.state.tags || []).filter(
            (t) => !t.is_snippet && t.group !== "Clause fields" && t.group !== "Snippets"
        );
    }

    get allClauseFieldTags() {
        return (this.state.tags || []).filter((t) => t.group === "Clause fields");
    }

    get fieldTags() {
        return this.allFieldTags.filter((t) => this._matchesQuery(t, this.tagQueryNormalized));
    }

    get clauseFieldTags() {
        return this.allClauseFieldTags.filter((t) =>
            this._matchesQuery(t, this.clauseQueryNormalized)
        );
    }

    get flatTagOptions() {
        return this.fieldTags;
    }

    get flatClauseOptions() {
        return this.clauseFieldTags;
    }

    get totalTagCount() {
        return this.allFieldTags.length;
    }

    get totalClauseTagCount() {
        return this.allClauseFieldTags.length;
    }

    get highlightedTag() {
        const options = this.flatTagOptions;
        if (!options.length) {
            return null;
        }
        const idx = Math.min(Math.max(this.state.tagHighlight, 0), options.length - 1);
        return options[idx];
    }

    get highlightedClauseTag() {
        const options = this.flatClauseOptions;
        if (!options.length) {
            return null;
        }
        const idx = Math.min(Math.max(this.state.clauseHighlight, 0), options.length - 1);
        return options[idx];
    }

    isTagHighlighted(tag) {
        return this.highlightedTag && this.highlightedTag.key === tag.key;
    }

    isClauseTagHighlighted(tag) {
        return this.highlightedClauseTag && this.highlightedClauseTag.key === tag.key;
    }

    optionId(prefix, key) {
        return `${this._uid}_${prefix}_${String(key || "").replace(/\W+/g, "_")}`;
    }

    optionAriaLabel(tag) {
        if (!tag) {
            return "";
        }
        return `${tag.label} — {{ ${tag.key} }}`;
    }

    get tagActiveDescendantId() {
        if (!this.state.tagMenuOpen || !this.highlightedTag) {
            return false;
        }
        return this.optionId("tag", this.highlightedTag.key);
    }

    get clauseActiveDescendantId() {
        if (!this.state.clauseMenuOpen || !this.highlightedClauseTag) {
            return false;
        }
        return this.optionId("clause", this.highlightedClauseTag.key);
    }

    get tagListboxId() {
        return `${this._uid}_tag_listbox`;
    }

    get clauseListboxId() {
        return `${this._uid}_clause_listbox`;
    }

    get tagInputId() {
        return `${this._uid}_tag_input`;
    }

    get clauseInputId() {
        return `${this._uid}_clause_input`;
    }

    get clauseHintId() {
        return `${this._uid}_clause_hint`;
    }

    announce(message) {
        // Retrigger polite live region even when the same message repeats.
        this.state.liveMessage = "";
        requestAnimationFrame(() => {
            this.state.liveMessage = message || "";
        });
    }

    openTagMenu() {
        if (this.state.readonly || this.state.busy) {
            return;
        }
        this._saveSelectionFromDocument();
        this.closeClauseMenu();
        this.state.tagMenuOpen = true;
        if (this.state.tagHighlight < 0) {
            this.state.tagHighlight = 0;
        }
        this.announce(`${this.flatTagOptions.length} contract fields available.`);
    }

    closeTagMenu({ clearQuery = false } = {}) {
        this.state.tagMenuOpen = false;
        this.state.tagHighlight = 0;
        if (clearQuery) {
            this.state.tagQuery = "";
        }
    }

    openClauseMenu() {
        if (this.state.readonly || this.state.busy) {
            return;
        }
        this._saveSelectionFromDocument();
        this.closeTagMenu();
        this.state.clauseMenuOpen = true;
        if (this.state.clauseHighlight < 0) {
            this.state.clauseHighlight = 0;
        }
        this.announce(`${this.flatClauseOptions.length} clause fields available.`);
    }

    closeClauseMenu({ clearQuery = false } = {}) {
        this.state.clauseMenuOpen = false;
        this.state.clauseHighlight = 0;
        if (clearQuery) {
            this.state.clauseQuery = "";
        }
    }

    onTagComboToggle(ev) {
        ev.preventDefault();
        if (this.state.readonly || this.state.busy) {
            return;
        }
        if (this.state.tagMenuOpen) {
            this.closeTagMenu();
        } else {
            this.openTagMenu();
            this.tagInputRef.el?.focus();
        }
    }

    onClauseComboToggle(ev) {
        ev.preventDefault();
        if (this.state.readonly || this.state.busy) {
            return;
        }
        if (this.state.clauseMenuOpen) {
            this.closeClauseMenu();
        } else {
            this.openClauseMenu();
            this.clauseInputRef.el?.focus();
        }
    }

    onTagSearchInput(ev) {
        this.state.tagQuery = ev.target.value || "";
        this.state.tagHighlight = 0;
        this.openTagMenu();
    }

    onClauseSearchInput(ev) {
        this.state.clauseQuery = ev.target.value || "";
        this.state.clauseHighlight = 0;
        this.openClauseMenu();
    }

    onTagSearchFocus() {
        this.openTagMenu();
    }

    onClauseSearchFocus() {
        this.openClauseMenu();
    }

    onClearTagSearch(ev) {
        ev?.preventDefault?.();
        ev?.stopPropagation?.();
        this.state.tagQuery = "";
        this.state.tagHighlight = 0;
        this.openTagMenu();
        this.tagInputRef.el?.focus();
    }

    onClearClauseSearch(ev) {
        ev?.preventDefault?.();
        ev?.stopPropagation?.();
        this.state.clauseQuery = "";
        this.state.clauseHighlight = 0;
        this.openClauseMenu();
        this.clauseInputRef.el?.focus();
    }

    onTagSearchKeydown(ev) {
        this._onComboKeydown(ev, {
            open: () => this.openTagMenu(),
            close: (opts) => this.closeTagMenu(opts),
            options: () => this.flatTagOptions,
            getHighlight: () => this.state.tagHighlight,
            setHighlight: (v) => {
                this.state.tagHighlight = v;
            },
            isOpen: () => this.state.tagMenuOpen,
            highlighted: () => this.highlightedTag,
            inputEl: () => this.tagInputRef.el,
        });
    }

    onClauseSearchKeydown(ev) {
        this._onComboKeydown(ev, {
            open: () => this.openClauseMenu(),
            close: (opts) => this.closeClauseMenu(opts),
            options: () => this.flatClauseOptions,
            getHighlight: () => this.state.clauseHighlight,
            setHighlight: (v) => {
                this.state.clauseHighlight = v;
            },
            isOpen: () => this.state.clauseMenuOpen,
            highlighted: () => this.highlightedClauseTag,
            inputEl: () => this.clauseInputRef.el,
        });
    }

    _onComboKeydown(ev, api) {
        if (this.state.readonly || this.state.busy) {
            return;
        }
        const options = api.options();
        switch (ev.key) {
            case "ArrowDown":
                ev.preventDefault();
                api.open();
                if (!options.length) {
                    return;
                }
                api.setHighlight(Math.min(api.getHighlight() + 1, options.length - 1));
                break;
            case "ArrowUp":
                ev.preventDefault();
                api.open();
                if (!options.length) {
                    return;
                }
                api.setHighlight(Math.max(api.getHighlight() - 1, 0));
                break;
            case "Enter":
                if (!api.isOpen()) {
                    api.open();
                    return;
                }
                if (api.highlighted()) {
                    ev.preventDefault();
                    this.onSelectTag(api.highlighted(), api.close);
                }
                break;
            case "Escape":
                ev.preventDefault();
                api.close({ clearQuery: true });
                api.inputEl()?.blur();
                break;
            case "Tab":
                api.close();
                break;
            default:
                break;
        }
    }

    onSelectTag(tag, closeFn = null) {
        if (!tag || this.state.readonly || this.state.busy) {
            return;
        }
        const inserted = this.onInsertTag(tag);
        if (inserted) {
            this.announce(`Inserted ${tag.label || tag.key}`);
            if (closeFn) {
                closeFn({ clearQuery: true });
            } else {
                this.closeTagMenu({ clearQuery: true });
                this.closeClauseMenu({ clearQuery: true });
            }
        }
    }

    onSelectClauseTag(tag) {
        this.onSelectTag(tag, (opts) => this.closeClauseMenu(opts));
    }

    onWindowPointerDown(ev) {
        if (this.state.tagMenuOpen) {
            const root = this.tagComboRef.el;
            if (root && !root.contains(ev.target)) {
                this.closeTagMenu();
            }
        }
        if (this.state.clauseMenuOpen) {
            const root = this.clauseComboRef.el;
            if (root && !root.contains(ev.target)) {
                this.closeClauseMenu();
            }
        }
    }

    _scrollHighlightedOptionIntoView() {
        if (this.state.tagMenuOpen) {
            const list = this.tagListRef.el;
            const active = list?.querySelector(".o_clm_docx_tag_option_active");
            active?.scrollIntoView?.({ block: "nearest" });
        }
        if (this.state.clauseMenuOpen) {
            const list = this.clauseListRef.el;
            const active = list?.querySelector(".o_clm_docx_tag_option_active");
            active?.scrollIntoView?.({ block: "nearest" });
        }
    }

    get wysiwygConfig() {
        const self = this;
        const html = this.state.documentHtml || "<p><br/></p>";
        const ClmPlaceholderTabPlugin = makeClmPlaceholderTabPlugin(self);
        return {
            content: markup(html),
            Plugins: [...MAIN_PLUGINS, ClmPlaceholderTabPlugin],
            baseContainers: ["P", "DIV"],
            onChange: () => {
                self.state.dirty = true;
            },
            onEditorReady: () => {
                // Plugins (including shared.dom.insert) are available only after attachTo.
                if (self.editor && !self.editor.isDestroyed) {
                    self._stripNearWhiteTextColors(self.editor.editable);
                    self._materializeDataImages(self.editor.editable, { trackForSave: true });
                }
            },
        };
    }

    _docId(value) {
        if (!value) {
            return false;
        }
        if (Array.isArray(value)) {
            return value[0] || false;
        }
        if (typeof value === "object") {
            return value.id || false;
        }
        return value;
    }

    get templateId() {
        return this.props.record.resId;
    }

    onEditorLoad(editor) {
        this.editor = editor;
        requestAnimationFrame(() => {
            if (editor?.editable) {
                this._stripNearWhiteTextColors(editor.editable);
                this._materializeDataImages(editor.editable, { trackForSave: true });
                this._applyIssueHighlights(this.state.issueSpans);
                this._bindEditableTabFix(editor.editable);
            }
        });
    }

    _bindEditableTabFix(editable) {
        if (!editable || editable._clmTabFixBound) {
            return;
        }
        this._onEditableTabKeydown = this._onEditableTabKeydown.bind(this);
        editable.addEventListener("keydown", this._onEditableTabKeydown, true);
        editable._clmTabFixBound = true;
        this._tabFixEditable = editable;
    }

    _unbindEditableTabFix() {
        if (this._tabFixEditable && this._onEditableTabKeydown) {
            this._tabFixEditable.removeEventListener(
                "keydown",
                this._onEditableTabKeydown,
                true
            );
            delete this._tabFixEditable._clmTabFixBound;
        }
        this._tabFixEditable = null;
    }

    _onEditableTabKeydown(ev) {
        if (ev.key !== "Tab" || ev.shiftKey || ev.altKey || ev.ctrlKey || ev.metaKey) {
            return;
        }
        if (ev.target?.closest?.("input, textarea, select, .o_clm_docx_tag_combo")) {
            return;
        }
        if (!this._onEditorTabOverride()) {
            return;
        }
        ev.preventDefault();
        ev.stopPropagation();
        ev.stopImmediatePropagation();
    }

    /**
     * Tab override for html_editor: close incomplete {{ }}/{% %} tags.
     * @returns {boolean} true when Tab was consumed (do not indent).
     */
    _onEditorTabOverride() {
        if (this.state.readonly || this.state.busy) {
            return false;
        }
        // Issue highlights wrap orphan "{{" alone, splitting the incomplete
        // tag across text nodes. Unwrap first so autofix can see the full tag.
        this._clearValidationHighlights();
        const fixed =
            this._autoFixPlaceholderAtCursor() || this._autoFixUnbalancedPlaceholders();
        if (!fixed) {
            this._applyIssueHighlights(this.state.issueSpans);
            return false;
        }
        this.state.dirty = true;
        this.announce(_t("Placeholder tag fixed."));
        this._syncValidationFromEditor().catch(() => {});
        return true;
    }

    _matchTagByPartial(partial) {
        const q = (partial || "").trim().toLowerCase();
        if (!q) {
            return null;
        }
        const tags = (this.state.tags || []).filter((t) => t.key && !t.is_snippet);
        const exact = tags.find((t) => (t.key || "").toLowerCase() === q);
        if (exact) {
            return exact;
        }
        const starts = tags.filter((t) => (t.key || "").toLowerCase().startsWith(q));
        if (starts.length === 1) {
            return starts[0];
        }
        if (starts.length > 1) {
            starts.sort((a, b) => (a.key || "").length - (b.key || "").length);
            return starts[0];
        }
        const includes = tags.filter(
            (t) =>
                (t.key || "").toLowerCase().includes(q) ||
                (t.label || "").toLowerCase().includes(q)
        );
        if (includes.length === 1) {
            return includes[0];
        }
        return null;
    }

    _formatPlaceholderInsert(tag, partial) {
        if (tag?.insert) {
            return tag.insert;
        }
        if (tag?.key) {
            return `{{ ${tag.key} }}`;
        }
        const expr = (partial || "").trim();
        if (!expr) {
            return "{{ }}";
        }
        // Preserve spacing style the user started: {{foo vs {{ foo
        return `{{ ${expr} }}`;
    }

    _autoFixPlaceholderAtCursor() {
        const editable = this._getEditableEl();
        if (!editable) {
            return false;
        }
        const selection = editable.ownerDocument.getSelection();
        if (!selection?.rangeCount) {
            return false;
        }
        const range = selection.getRangeAt(0);
        let node = range.startContainer;
        let offset = range.startOffset;
        if (node.nodeType === Node.ELEMENT_NODE) {
            if (node.childNodes[offset]?.nodeType === Node.TEXT_NODE) {
                node = node.childNodes[offset];
                offset = 0;
            } else if (offset > 0) {
                const prev = node.childNodes[offset - 1];
                if (prev?.nodeType === Node.TEXT_NODE) {
                    node = prev;
                    offset = node.textContent.length;
                }
            }
        }
        if (node.nodeType !== Node.TEXT_NODE) {
            return false;
        }

        // Build a contiguous string around the caret within the same block so
        // we still match when "{{" and "partner_name" live in sibling nodes.
        const block =
            node.parentElement?.closest?.("p, h1, h2, h3, h4, h5, h6, li, div") ||
            editable;
        const texts = this._collectTextNodes(block);
        let abs = 0;
        let caretAbs = 0;
        let plain = "";
        for (const textNode of texts) {
            const value = textNode.textContent || "";
            if (textNode === node) {
                caretAbs = abs + offset;
            }
            plain += value;
            abs += value.length;
        }

        const before = plain.slice(0, caretAbs);
        const after = plain.slice(caretAbs);

        const varMatch = before.match(/\{\{(\s*)([a-zA-Z0-9_.]*)$/);
        if (varMatch && !/^\s*\}\}/.test(after)) {
            const cont = (after.match(/^[a-zA-Z0-9_.]*/) || [""])[0];
            const partial = varMatch[2] + cont;
            const tag = this._matchTagByPartial(partial);
            const insert = this._formatPlaceholderInsert(tag, partial);
            const start = caretAbs - varMatch[0].length;
            const end = caretAbs + cont.length;
            return this._replaceBlockPlainRange(block, texts, start, end, insert);
        }

        const blockMatch = before.match(/\{%(\s*)([a-zA-Z0-9_.\s]*)$/);
        if (blockMatch && !/^\s*%\}/.test(after)) {
            const body = blockMatch[0];
            const insert = body.trimEnd().endsWith("%") ? `${body}}` : `${body} %}`;
            const start = caretAbs - blockMatch[0].length;
            return this._replaceBlockPlainRange(block, texts, start, caretAbs, insert);
        }
        return false;
    }

    /**
     * Replace a [start, end) range in a block's concatenated text nodes.
     */
    _replaceBlockPlainRange(block, textNodes, start, end, insert) {
        const editable = this._getEditableEl();
        if (!editable || !textNodes?.length) {
            return false;
        }
        let plain = "";
        for (const n of textNodes) {
            plain += n.textContent || "";
        }
        if (start < 0 || end > plain.length || start > end) {
            return false;
        }
        const nextPlain = plain.slice(0, start) + insert + plain.slice(end);

        // Write back into the first text node and clear the rest in this block.
        const first = textNodes[0];
        first.textContent = nextPlain;
        for (let i = 1; i < textNodes.length; i++) {
            textNodes[i].textContent = "";
        }
        block.normalize();

        const nodes = this._collectTextNodes(block);
        const target = nodes[0] || first;
        const caret = Math.min(start + insert.length, (target.textContent || "").length);
        const selection = editable.ownerDocument.getSelection();
        const next = editable.ownerDocument.createRange();
        next.setStart(target, caret);
        next.collapse(true);
        selection.removeAllRanges();
        selection.addRange(next);
        this._savedRange = next.cloneRange();
        editable.dispatchEvent(new Event("input", { bubbles: true }));
        return true;
    }

    _fixMalformedPlaceholdersInText(text) {
        let next = text || "";

        // { partner_name }}  →  {{ partner_name }}
        next = next.replace(
            /(^|[^/{])\{(?!%)(\s*[a-zA-Z_][\w.]*\s*)\}\}/g,
            (full, prefix, body) => `${prefix}{{${body}}}`
        );

        // {{ contract_name }  →  {{ contract_name }}
        next = next.replace(
            /\{\{(\s*[a-zA-Z_][\w.]*\s*)\}(?!\})/g,
            (full, body) => `{{${body}}}`
        );

        // partner_address }}  →  {{ partner_address }}
        next = next.replace(
            /([a-zA-Z_][\w.]*)(\s*)\}\}/g,
            (full, key, _sp, index, src) => {
                const before = src.slice(0, index);
                // Belongs to "{ … }}" / "{{ … }}" opener.
                if (/\{\{?\s*$/.test(before)) {
                    return full;
                }
                const opens = (before.match(/\{\{/g) || []).length;
                const closes = (before.match(/\}\}/g) || []).length;
                if (opens > closes) {
                    return full;
                }
                const tag = this._matchTagByPartial(key);
                return this._formatPlaceholderInsert(tag, key);
            }
        );

        // {{ partner_address   (missing closing braces)
        // Do not use (?!\s*\}\}) in the regex — greedy backtracking corrupts
        // complete tags like "{{ contract_name }}".
        next = next.replace(
            /\{\{(\s*)([a-zA-Z0-9_.]+)/g,
            (full, sp, key, index, src) => {
                const rest = src.slice(index + full.length);
                if (/^\s*\}\}/.test(rest)) {
                    return full;
                }
                // Lone "}" case is normalized above; leave if still present.
                if (/^\s*\}(?!\})/.test(rest)) {
                    return full;
                }
                const tag = this._matchTagByPartial(key);
                if (tag) {
                    return this._formatPlaceholderInsert(tag, key);
                }
                return `{{${sp}${key} }}`;
            }
        );

        // {% ... without %}
        next = next.replace(
            /\{%(\s*)([a-zA-Z0-9_.\s]+)/g,
            (full, sp, body, index, src) => {
                const rest = src.slice(index + full.length);
                if (/^\s*%\}/.test(rest)) {
                    return full;
                }
                return `{%${sp}${body.trim()} %}`;
            }
        );

        return next;
    }

    _autoFixUnbalancedPlaceholders() {
        const editable = this._getEditableEl();
        if (!editable) {
            return false;
        }

        let changed = false;
        const blocks = [
            ...editable.querySelectorAll("p, h1, h2, h3, h4, h5, h6, li"),
        ];
        const targets = blocks.length ? blocks : [editable];

        for (const block of targets) {
            const texts = this._collectTextNodes(block);
            if (!texts.length) {
                continue;
            }
            let original = "";
            for (const n of texts) {
                original += n.textContent || "";
            }
            const next = this._fixMalformedPlaceholdersInText(original);
            if (next !== original) {
                texts[0].textContent = next;
                for (let i = 1; i < texts.length; i++) {
                    texts[i].textContent = "";
                }
                block.normalize();
                changed = true;
            }
        }
        if (changed) {
            editable.dispatchEvent(new Event("input", { bubbles: true }));
        }
        return changed;
    }

    /**
     * Drop near-white inline text colours that vanish on the white paper.
     * Dark-mode heading tokens / mistaken DOCX whites leave style="color:#fff"
     * (or equivalent) which beats our paper CSS.
     */
    _stripNearWhiteTextColors(root) {
        if (!root?.querySelectorAll) {
            return;
        }
        const parseRgb = (value) => {
            if (!value) {
                return null;
            }
            const hex = String(value).trim();
            const hexMatch = hex.match(/^#([0-9a-f]{3}|[0-9a-f]{6})$/i);
            if (hexMatch) {
                let h = hexMatch[1];
                if (h.length === 3) {
                    h = h.split("").map((c) => c + c).join("");
                }
                return [
                    parseInt(h.slice(0, 2), 16),
                    parseInt(h.slice(2, 4), 16),
                    parseInt(h.slice(4, 6), 16),
                ];
            }
            const rgbMatch = hex.match(
                /^rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)/i
            );
            if (rgbMatch) {
                return rgbMatch.slice(1, 4).map((n) => parseInt(n, 10));
            }
            return null;
        };
        const isNearWhite = (rgb) =>
            rgb && 0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2] >= 230;

        root.querySelectorAll("[style*='color']").forEach((el) => {
            const rgb = parseRgb(el.style.color);
            if (!isNearWhite(rgb)) {
                return;
            }
            el.style.removeProperty("color");
            el.style.removeProperty("-webkit-text-fill-color");
            if (el.tagName === "FONT" && el.hasAttribute("color")) {
                el.removeAttribute("color");
            }
            if (!el.getAttribute("style")?.trim()) {
                el.removeAttribute("style");
            }
        });
    }

    _clearValidationHighlights() {
        const editable = this._getEditableEl();
        if (!editable) {
            return;
        }
        editable.querySelectorAll(".o_clm_docx_tag_issue").forEach((span) => {
            const parent = span.parentNode;
            if (!parent) {
                return;
            }
            while (span.firstChild) {
                parent.insertBefore(span.firstChild, span);
            }
            parent.removeChild(span);
            parent.normalize();
        });
    }

    /**
     * Highlight validation problem tags in the live editor.
     * Prefer precise start/end offsets; fall back to text match.
     */
    _applyIssueHighlights(issueSpans) {
        this._clearValidationHighlights();
        const editable = this._getEditableEl();
        const spans = issueSpans || [];
        if (!editable || !spans.length) {
            return;
        }

        const offsetSpans = spans.filter(
            (s) => Number.isInteger(s.start) && Number.isInteger(s.end) && s.end > s.start
        );
        if (offsetSpans.length) {
            this._highlightByOffsets(editable, offsetSpans);
        }

        const textSpans = spans.filter((s) => s.text && !Number.isInteger(s.start));
        // Longer matches first so full tags win over bare "{{".
        textSpans
            .slice()
            .sort((a, b) => (b.text || "").length - (a.text || "").length)
            .forEach((span) => {
                this._highlightByText(editable, span.text, span.severity || "error");
            });
    }

    _highlightClass(severity) {
        return severity === "warning"
            ? "o_clm_docx_tag_issue o_clm_docx_tag_issue_warn"
            : "o_clm_docx_tag_issue";
    }

    _collectTextNodes(root) {
        const nodes = [];
        const walker = root.ownerDocument.createTreeWalker(root, NodeFilter.SHOW_TEXT);
        let node = walker.nextNode();
        while (node) {
            nodes.push(node);
            node = walker.nextNode();
        }
        return nodes;
    }

    _highlightByOffsets(root, spans) {
        const blocks = [...root.querySelectorAll("p, h1, h2, h3, h4, li")];
        const blockEls = blocks.length ? blocks : [root];
        const segments = [];
        let cursor = 0;
        blockEls.forEach((block, blockIndex) => {
            const texts = this._collectTextNodes(block);
            texts.forEach((node) => {
                const len = (node.nodeValue || "").length;
                segments.push({ node, start: cursor, end: cursor + len });
                cursor += len;
            });
            if (blockIndex < blockEls.length - 1) {
                cursor += 1; // matches server '\n'.join(paragraphs)
            }
        });

        const ranges = [];
        for (const span of spans) {
            for (const seg of segments) {
                const a = Math.max(span.start, seg.start);
                const b = Math.min(span.end, seg.end);
                if (a < b) {
                    ranges.push({
                        node: seg.node,
                        localStart: a - seg.start,
                        localEnd: b - seg.start,
                        severity: span.severity || "error",
                    });
                }
            }
        }
        const byNode = new Map();
        for (const range of ranges) {
            if (!byNode.has(range.node)) {
                byNode.set(range.node, []);
            }
            byNode.get(range.node).push(range);
        }
        byNode.forEach((nodeRanges) => {
            nodeRanges
                .sort((a, b) => b.localStart - a.localStart)
                .forEach((range) => this._wrapTextRange(range));
        });
    }

    _highlightByText(root, needle, severity) {
        if (!needle) {
            return;
        }
        const nodes = this._collectTextNodes(root);
        for (const node of nodes) {
            if (node.parentElement?.closest(".o_clm_docx_tag_issue")) {
                continue;
            }
            let text = node.nodeValue || "";
            let from = 0;
            const pieces = [];
            while (from < text.length) {
                const idx = text.indexOf(needle, from);
                if (idx === -1) {
                    pieces.push({ type: "text", value: text.slice(from) });
                    break;
                }
                if (idx > from) {
                    pieces.push({ type: "text", value: text.slice(from, idx) });
                }
                pieces.push({ type: "mark", value: needle, severity });
                from = idx + needle.length;
            }
            if (!pieces.some((p) => p.type === "mark")) {
                continue;
            }
            const frag = root.ownerDocument.createDocumentFragment();
            for (const piece of pieces) {
                if (piece.type === "text") {
                    if (piece.value) {
                        frag.appendChild(root.ownerDocument.createTextNode(piece.value));
                    }
                } else {
                    const mark = root.ownerDocument.createElement("span");
                    mark.className = this._highlightClass(piece.severity);
                    mark.setAttribute("data-clm-issue", "1");
                    mark.textContent = piece.value;
                    frag.appendChild(mark);
                }
            }
            node.parentNode?.replaceChild(frag, node);
        }
    }

    _wrapTextRange({ node, localStart, localEnd, severity }) {
        if (!node?.parentNode || localStart >= localEnd) {
            return;
        }
        if (node.parentElement?.closest(".o_clm_docx_tag_issue")) {
            return;
        }
        const text = node.nodeValue || "";
        const before = text.slice(0, localStart);
        const middle = text.slice(localStart, localEnd);
        const after = text.slice(localEnd);
        if (!middle) {
            return;
        }
        const doc = node.ownerDocument;
        const frag = doc.createDocumentFragment();
        if (before) {
            frag.appendChild(doc.createTextNode(before));
        }
        const mark = doc.createElement("span");
        mark.className = this._highlightClass(severity);
        mark.setAttribute("data-clm-issue", "1");
        mark.textContent = middle;
        frag.appendChild(mark);
        if (after) {
            frag.appendChild(doc.createTextNode(after));
        }
        node.parentNode.replaceChild(frag, node);
    }

    _getLiveEditor() {
        const editor = this.editor;
        if (!editor || editor.isDestroyed) {
            return null;
        }
        if (!editor.isReady || !editor.shared?.dom?.insert) {
            return null;
        }
        return editor;
    }

    _getEditableEl() {
        return (
            this.editor?.editable ||
            this.rootRef.el?.querySelector(".o_clm_docx_editable.odoo-editor-editable") ||
            this.rootRef.el?.querySelector(".o_clm_docx_editable") ||
            null
        );
    }

    _saveSelectionFromDocument() {
        const editable = this._getEditableEl();
        if (!editable) {
            return;
        }
        const selection = editable.ownerDocument.getSelection();
        if (!selection || !selection.rangeCount) {
            return;
        }
        const range = selection.getRangeAt(0);
        if (!editable.contains(range.commonAncestorContainer)) {
            return;
        }
        this._savedRange = range.cloneRange();
    }

    _restoreSavedSelection() {
        const editable = this._getEditableEl();
        if (!editable) {
            return false;
        }
        editable.focus();
        const selection = editable.ownerDocument.getSelection();
        if (this._savedRange && editable.contains(this._savedRange.commonAncestorContainer)) {
            selection.removeAllRanges();
            selection.addRange(this._savedRange);
            return true;
        }
        // Fall back to end of editable content.
        const range = editable.ownerDocument.createRange();
        range.selectNodeContents(editable);
        range.collapse(false);
        selection.removeAllRanges();
        selection.addRange(range);
        this._savedRange = range.cloneRange();
        return true;
    }

    _revokeBlobUrls() {
        for (const url of this._blobUrls || []) {
            try {
                URL.revokeObjectURL(url);
            } catch {
                // ignore
            }
        }
        this._blobUrls = [];
        this._blobToDataUri = new Map();
        this._preservedImgHtml = [];
        this._preservedImgSrcs = new Set();
    }

    _getEditorHtmlForSave() {
        this._clearValidationHighlights();
        let html = this.editor?.getContent?.() || this.state.documentHtml || "";

        // Prefer recovering from the live editable DOM — blob map alone can
        // miss clones the Wysiwyg creates without our mapping.
        const editable = this.editor?.editable;
        if (editable) {
            editable.querySelectorAll("img").forEach((img) => {
                const src = img.getAttribute("src") || "";
                const original =
                    img.getAttribute("data-clm-original-src") ||
                    this._blobToDataUri?.get(src) ||
                    "";
                if (original.startsWith("data:")) {
                    img.setAttribute("src", original);
                    img.setAttribute("data-clm-original-src", original);
                    img.classList.add("o_clm_docx_img");
                }
            });
            html = this.editor?.getContent?.() || html;
        }

        // Blob URLs are display-only; restore data: URIs so logos survive save.
        if (this._blobToDataUri?.size) {
            for (const [blobUrl, dataUri] of this._blobToDataUri.entries()) {
                if (blobUrl && dataUri && html.includes(blobUrl)) {
                    html = html.split(blobUrl).join(dataUri);
                }
            }
        }

        // Restore data-clm-original-src onto src when the editor left blob:/empty.
        html = html.replace(
            /<img\b([^>]*?)>/gi,
            (full, attrs) => {
                const srcMatch = /\bsrc=(["'])(.*?)\1/i.exec(attrs);
                const src = srcMatch ? srcMatch[2] : "";
                if (src.startsWith("data:")) {
                    return full;
                }
                const origMatch = /\bdata-clm-original-src=(["'])(.*?)\1/i.exec(attrs);
                const original = origMatch ? origMatch[2] : "";
                if (!original.startsWith("data:")) {
                    return full;
                }
                let nextAttrs = attrs;
                if (srcMatch) {
                    nextAttrs = nextAttrs.replace(
                        /\bsrc=(["']).*?\1/i,
                        `src=$1${original}$1`
                    );
                } else {
                    nextAttrs += ` src="${original}"`;
                }
                if (!/\bclass=/i.test(nextAttrs)) {
                    nextAttrs += ' class="o_clm_docx_img"';
                } else if (!/o_clm_docx_img/.test(nextAttrs)) {
                    nextAttrs = nextAttrs.replace(
                        /\bclass=(["'])(.*?)\1/i,
                        (_, q, cls) => `class=${q}${cls} o_clm_docx_img${q}`
                    );
                }
                return `<img${nextAttrs}>`;
            }
        );

        // Only treat real data: images as present — blob: leftovers still need reinject.
        const hasDataImage = /data:image\//i.test(html);
        if (!hasDataImage && this._preservedImgHtml?.length) {
            const imgs = this._preservedImgHtml
                .map((fragment) => `<p>${fragment}</p>`)
                .join("");
            html = imgs + html;
        }
        return html;
    }

    _materializeDataImages(rootEl, { trackForSave = false } = {}) {
        if (!rootEl) {
            return;
        }
        if (!this._blobToDataUri) {
            this._blobToDataUri = new Map();
        }
        if (!this._preservedImgHtml) {
            this._preservedImgHtml = [];
        }
        if (!this._preservedImgSrcs) {
            this._preservedImgSrcs = new Set();
        }
        rootEl.querySelectorAll("img.o_clm_docx_img[src^='data:'], img[src^='data:']").forEach((img) => {
            const src = img.getAttribute("src") || "";
            const match = /^data:([^;,]+)?(;base64)?,(.*)$/s.exec(src);
            if (!match || !match[2]) {
                return;
            }
            try {
                img.setAttribute("data-clm-original-src", src);
                if (trackForSave && !this._preservedImgSrcs.has(src)) {
                    const clone = img.cloneNode(true);
                    clone.setAttribute("src", src);
                    clone.setAttribute("data-clm-original-src", src);
                    clone.classList.add("o_clm_docx_img");
                    this._preservedImgSrcs.add(src);
                    this._preservedImgHtml.push(clone.outerHTML);
                }
                const mime = match[1] || "image/png";
                const binary = atob(match[3] || "");
                const bytes = new Uint8Array(binary.length);
                for (let i = 0; i < binary.length; i++) {
                    bytes[i] = binary.charCodeAt(i);
                }
                const url = URL.createObjectURL(new Blob([bytes], { type: mime }));
                this._blobUrls.push(url);
                this._blobToDataUri.set(url, src);
                img.setAttribute("src", url);
                img.setAttribute("data-clm-data-uri", "1");
                img.classList.add("o_clm_docx_img");
            } catch (error) {
                console.warn("DOCX image materialize failed", error);
            }
        });
    }

    _syncHeaderDom() {
        const headerRoot = this.headerRootRef.el;
        if (headerRoot) {
            this.state.headers.forEach((hdr, index) => {
                const el = headerRoot.querySelector(
                    `.o_clm_docx_header_line[data-header-index="${index}"]`
                );
                if (!el) {
                    return;
                }
                const html = hdr.html || "<br/>";
                if (el.dataset.syncedHtml === html) {
                    return;
                }
                el.innerHTML = html;
                this._stripNearWhiteTextColors(el);
                this._materializeDataImages(el);
                el.dataset.syncedHtml = html;
            });
        }
        const readonlyEl = this.readonlyHtmlRef.el;
        if (readonlyEl && !this.state.editorReady) {
            const html = this.state.documentHtml || "<p><br/></p>";
            if (readonlyEl.dataset.syncedHtml !== html) {
                readonlyEl.innerHTML = html;
                this._stripNearWhiteTextColors(readonlyEl);
                this._materializeDataImages(readonlyEl);
                readonlyEl.dataset.syncedHtml = html;
            }
        }
    }

    async loadData(props = this.props) {
        const record = props.record;
        const docId = this._docId(record.data.base_document_id);
        if (!docId) {
            this.state.loading = false;
            this.state.error = _t("Set a Document Template to edit placeholders.");
            this.state.paragraphs = [];
            this.state.documentHtml = "";
            this.state.editorReady = false;
            return;
        }

        // New / dirty records: persist first so the server can read base_document_id.
        let resId = record.resId;
        const dirty = typeof record.isDirty === "function" ? await record.isDirty() : record.dirty;
        if (!resId || dirty) {
            this.state.loading = true;
            this.state.error = null;
            try {
                const saved = await record.save({ reload: true });
                if (saved === false) {
                    this.state.loading = false;
                    this.state.error = _t(
                        "Save the template (required fields) to open the Document Template editor."
                    );
                    this.state.editorReady = false;
                    return;
                }
                resId = record.resId;
            } catch (error) {
                this.state.loading = false;
                this.state.error =
                    error?.data?.message ||
                    error.message ||
                    _t("Save the template to open the Document Template editor.");
                this.state.editorReady = false;
                return;
            }
        }

        if (!resId) {
            this.state.loading = false;
            this.state.error = _t("Save the template to open the Document Template editor.");
            this.state.editorReady = false;
            return;
        }

        const seq = ++this._loadSeq;
        this.state.loading = true;
        this.state.editorReady = false;
        this.state.error = null;
        try {
            const data = await this.orm.call("clm.template", "get_docx_editor_data", [resId]);
            if (seq !== this._loadSeq) {
                return;
            }
            this._applyPayload(data, { resetDirty: true });
        } catch (error) {
            if (seq !== this._loadSeq) {
                return;
            }
            this.state.error = error?.data?.message || error.message || String(error);
            this.state.paragraphs = [];
            this.state.documentHtml = "";
            this.state.editorReady = false;
        } finally {
            if (seq === this._loadSeq) {
                this.state.loading = false;
            }
        }
    }

    _applyPayload(data, { resetDirty = false } = {}) {
        this._revokeBlobUrls();
        this._savedRange = null;
        // Drop stale editor handle; Wysiwyg remount (loadToken) will call onEditorLoad again.
        this.editor = null;
        this.state.documentName = data.document_name || "";
        this.state.versionName = data.version_name || "";
        this.state.versionNumber = data.version_number || false;
        this.state.versionCount = data.version_count || 0;
        this.state.isOriginalActive = Boolean(data.is_original_active);
        this.state.canSaveInplace = Boolean(data.can_save_inplace);
        this.state.headers = (data.headers || []).map((row) => ({
            html: row.html || "<br/>",
            styleCss: row.style_css || "",
            text: row.text || "",
        }));
        this.state.paragraphs = (data.paragraphs || []).map((row) => ({
            index: row.index,
            text: row.text || "",
            html: row.html || "<br/>",
            styleCss: row.style_css || "",
            hasImage: Boolean(row.has_image),
        }));
        this.state.documentHtml = data.document_html || "<p><br/></p>";
        this.state.tags = data.tags || [];
        this.state.clauseSetCompactLines = data.clause_set_compact_lines || [];
        this.state.errors = data.errors || [];
        this.state.warnings = data.warnings || [];
        this.state.issueSpans = data.issue_spans || [];
        // Keep banners collapsed so long error lists don't crush the editor.
        this.state.errorsExpanded = false;
        this.state.warningsExpanded = false;
        this.state.readonly = Boolean(data.readonly);
        this.state.loadToken = (this.state.loadToken || 0) + 1;
        this.state.editorReady = !this.state.readonly;
        if (resetDirty) {
            this.state.dirty = false;
        }
        requestAnimationFrame(() => this._applyIssueHighlights(this.state.issueSpans));
    }

    _escapeHtml(text) {
        return String(text || "")
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;");
    }

    onInsertTag(tag) {
        if (this.state.readonly) {
            return false;
        }
        const text = tag.insert || `{{ ${tag.key} }}`;
        return this._insertIntoEditor(text);
    }

    /**
     * Insert plain text or a DOM node at the last editor caret.
     * Odoo 19 helpers live under editor.shared.<pluginId>.<method>.
     */
    _insertIntoEditor(content) {
        const editor = this._getLiveEditor();
        if (editor) {
            try {
                this._restoreSavedSelection();
                editor.shared.selection?.focusEditable?.();
                editor.shared.dom.insert(content);
                editor.shared.history?.addStep?.();
                this.state.dirty = true;
                this._saveSelectionFromDocument();
                return true;
            } catch (error) {
                console.warn("DOCX tag insert via editor failed, trying DOM fallback", error);
            }
        }
        if (this._insertViaDomFallback(content)) {
            this.state.dirty = true;
            return true;
        }
        this.notification.add(_t("Click in the document first."), { type: "warning" });
        return false;
    }

    _insertMultilineSnippet(lines) {
        const fragment = document.createDocumentFragment();
        const p = document.createElement("p");
        lines.forEach((line, index) => {
            if (index) {
                p.appendChild(document.createElement("br"));
            }
            p.appendChild(document.createTextNode(line));
        });
        fragment.appendChild(p);
        return this._insertIntoEditor(fragment);
    }

    /**
     * Insert the dual-party signature block as real paragraphs (not soft
     * breaks). Soft breaks in one <p> leave Title / Signature / Date cramped
     * so Odoo Sign has no room.
     *
     * Spacing rule: exactly one blank line above each Signature: field.
     * No blank between IN WITNESS and the first For (that looked oversized).
     * Never insert a blank line between Signature and Date.
     */
    _insertSignatureBlockSnippet(lines) {
        const cleaned = [];
        const raw = lines || [];
        for (let i = 0; i < raw.length; i++) {
            const trimmed = (raw[i] || "").trim();
            if (!trimmed) {
                const next = (raw[i + 1] || "").trim();
                const prev = cleaned.length ? cleaned[cleaned.length - 1] : "";
                // Keep blank only above Signature, or between parties (after Date).
                if (next.startsWith("Signature:")) {
                    cleaned.push("");
                } else if (next.startsWith("For ") && prev.startsWith("Date:")) {
                    cleaned.push("");
                }
                // Skip blank after IN WITNESS and blank above Date.
                continue;
            }
            // Guarantee one blank line immediately above Signature.
            if (
                trimmed.startsWith("Signature:") &&
                (cleaned.length === 0 || cleaned[cleaned.length - 1] !== "")
            ) {
                cleaned.push("");
            }
            cleaned.push(trimmed);
        }

        const fragment = document.createDocumentFragment();
        cleaned.forEach((line) => {
            const p = document.createElement("p");
            p.setAttribute("style", "margin-top:0;margin-bottom:0");
            if (!line) {
                // One-line spacer above Signature (or between parties).
                p.style.marginTop = "0";
                p.style.marginBottom = "12pt";
                p.style.minHeight = "1em";
                p.appendChild(document.createElement("br"));
            } else if (line.startsWith("For ")) {
                // Modest gap only for the second party (after Date spacer).
                p.appendChild(document.createTextNode(line));
            } else {
                // IN WITNESS / Name / Title / Signature / Date — no extra margins.
                p.appendChild(document.createTextNode(line));
            }
            fragment.appendChild(p);
        });
        return this._insertIntoEditor(fragment);
    }

    _insertClauseCollectionSnippet(lines) {
        const fragment = document.createDocumentFragment();
        (lines || []).forEach((line) => {
            const p = document.createElement("p");
            p.setAttribute("style", "margin-top:0;margin-bottom:0");
            const trimmed = (line || "").trim();
            if (trimmed === "{{ clause.title }}") {
                p.style.marginTop = "8pt";
                const strong = document.createElement("strong");
                strong.textContent = line;
                p.appendChild(strong);
            } else if (trimmed === "{{ clause.category }}") {
                // One line of space under the title.
                p.style.marginTop = "12pt";
                const em = document.createElement("em");
                em.textContent = line;
                p.appendChild(em);
            } else if (trimmed === "{{ clause.content }}") {
                // One line of space above standard wording.
                p.style.marginTop = "12pt";
                p.style.marginBottom = "10pt";
                p.appendChild(document.createTextNode(line));
            } else {
                p.appendChild(document.createTextNode(line));
            }
            fragment.appendChild(p);
        });
        return this._insertIntoEditor(fragment);
    }

    onInsertClauseLoop() {
        if (this.state.readonly) {
            return false;
        }
        const editor = this._getLiveEditor();
        const current = editor?.getContent?.() || this.state.documentHtml || "";
        const marker = "{% for section in sections %}";
        if (current.includes(marker)) {
            this.notification.add(_t("Clause Collection is already present."), { type: "info" });
            this.announce(_t("Clause Collection is already present."));
            return false;
        }
        const tag = (this.state.tags || []).find((t) => t.key === "clause_loop");
        const lines = tag?.insert
            ? tag.insert.split("\n")
            : [
                "{% for section in sections %}",
                "{% for clause in section.clauses %}",
                "{{ clause.title }}",
                "{{ clause.category }}",
                "{{ clause.content }}",
                "{% endfor %}",
                "{% endfor %}",
            ];
        const ok = this._insertClauseCollectionSnippet(lines);
        if (ok) {
            this.announce(_t("Clause Collection inserted."));
        }
        return ok;
    }

    onInsertAllClauses() {
        if (this.state.readonly) {
            return false;
        }
        const lines = this.state.clauseSetCompactLines || [];
        if (!lines.length) {
            const msg = _t("No clauses on this template. Add Clause Lines first.");
            this.notification.add(msg, { type: "warning" });
            this.announce(msg);
            return false;
        }
        const editor = this._getLiveEditor();
        const current = editor?.getContent?.() || this.state.documentHtml || "";
        if (current.includes("{% for section in sections %}")) {
            this.notification.add(
                _t("A Clause Collection is already present. Customizing clauses may duplicate wording."),
                { type: "warning" }
            );
        }
        const firstLine = lines.find((line) => line.includes("clause_set.clauses."));
        if (firstLine && current.includes(firstLine)) {
            const msg = _t("Customizable clauses are already present.");
            this.notification.add(msg, { type: "info" });
            this.announce(msg);
            return false;
        }
        const ok = this._insertMultilineSnippet(lines);
        if (ok) {
            const msg = _t(
                "Clauses inserted for customization. Delete any title, category, or content line you do not need."
            );
            this.notification.add(msg, { type: "success" });
            this.announce(msg);
        }
        return ok;
    }

    onInsertSignatureBlock() {
        if (this.state.readonly) {
            return false;
        }
        const editor = this._getLiveEditor();
        const current = editor?.getContent?.() || this.state.documentHtml || "";
        // Only treat our inserted snippet as present — not generic legal wording
        // like "IN WITNESS WHEREOF" that many contracts already contain.
        const hasSnippet =
            current.includes("{{ company_signature_label }}") &&
            current.includes("{{ partner_signature_label }}");
        if (hasSnippet) {
            const msg = _t("Signature block is already present.");
            this.notification.add(msg, { type: "info" });
            this.announce(msg);
            return false;
        }
        const tag = (this.state.tags || []).find((t) => t.key === "signature_block");
        const lines = tag?.insert
            ? tag.insert.split("\n")
            : [
                "IN WITNESS WHEREOF, the parties have executed this Agreement.",
                "For {{ company_signature_label }}:",
                "Name: {{ company_signatory }}",
                "Title: {{ company_signatory_title }}",
                "",
                "Signature: ___________________________",
                "Date: {{ signing_date }}",
                "",
                "For {{ partner_signature_label }}:",
                "Name: {{ partner_signatory }}",
                "Title: {{ partner_signatory_title }}",
                "",
                "Signature: ___________________________",
                "Date: {{ signing_date }}",
            ];
        const ok = this._insertSignatureBlockSnippet(lines);
        if (ok) {
            this.announce(_t("Signature block inserted."));
        }
        return ok;
    }

    _insertViaDomFallback(content) {
        const editable = this._getEditableEl();
        if (!editable) {
            return false;
        }
        this._restoreSavedSelection();
        const selection = editable.ownerDocument.getSelection();
        if (!selection || !selection.rangeCount) {
            return false;
        }
        const range = selection.getRangeAt(0);
        range.deleteContents();
        let node;
        if (typeof content === "string") {
            node = editable.ownerDocument.createTextNode(content);
            range.insertNode(node);
        } else if (content instanceof DocumentFragment || content instanceof Element) {
            const last = content.lastChild;
            range.insertNode(content);
            node = last;
        } else {
            return false;
        }
        if (node) {
            range.setStartAfter(node);
            range.setEndAfter(node);
            selection.removeAllRanges();
            selection.addRange(range);
            this._savedRange = range.cloneRange();
        }
        editable.dispatchEvent(new Event("input", { bubbles: true }));
        return true;
    }

    async onReload() {
        await this.loadData();
    }

    _editorParagraphTexts() {
        const html = this.editor?.getContent?.() || this.state.documentHtml || "";
        const tmp = document.createElement("div");
        tmp.innerHTML = html;
        const texts = [...tmp.querySelectorAll("p, h1, h2, h3, h4, li")].map(
            (el) => (el.innerText || "").replace(/\u00a0/g, " ")
        );
        if (!texts.length) {
            texts.push((tmp.innerText || "").replace(/\u00a0/g, " "));
        }
        return texts;
    }

    /**
     * Run server validation and sync the in-editor error/warning banners.
     * @returns {Promise<{errors: string[], warnings: string[], issue_spans: object[]}>}
     */
    async _syncValidationFromEditor() {
        this._clearValidationHighlights();
        const texts = this._editorParagraphTexts();
        const validateResult = await this.orm.call(
            "clm.template",
            "validate_docx_paragraphs",
            [this.templateId, texts.map((t, i) => ({ index: i, text: t }))]
        );
        this.state.errors = validateResult.errors || [];
        this.state.warnings = validateResult.warnings || [];
        this.state.issueSpans = validateResult.issue_spans || [];
        this._applyIssueHighlights(this.state.issueSpans);
        return validateResult;
    }

    async onValidate() {
        if (!this.templateId) {
            return;
        }
        this.state.busy = true;
        try {
            const validateResult = await this._syncValidationFromEditor();
            if (!validateResult.errors?.length && !validateResult.warnings?.length) {
                this.notification.add(_t("Tags look valid."), { type: "success" });
                this.announce(_t("Validation passed. Tags look valid."));
            } else if (validateResult.errors?.length) {
                this.notification.add(_t("Validation found errors."), { type: "danger" });
                this.announce(_t("Validation found errors."));
            } else {
                this.notification.add(_t("Validation completed with warnings."), {
                    type: "warning",
                });
                this.announce(_t("Validation completed with warnings."));
            }
        } catch (error) {
            this.notification.add(error?.data?.message || error.message || String(error), {
                type: "danger",
            });
        } finally {
            this.state.busy = false;
        }
    }

    async onSaveInplace() {
        return this._saveDocumentHtml("inplace");
    }

    async onSaveAsNewVersion() {
        return this._saveDocumentHtml("new");
    }

    async _saveDocumentHtml(mode) {
        if (!this.templateId || this.state.readonly) {
            return;
        }
        if (mode === "inplace" && !this.state.canSaveInplace) {
            this.notification.add(
                _t("The original Document Template cannot be overwritten. Use Save as New Version."),
                { type: "warning" }
            );
            return;
        }
        const html = this._getEditorHtmlForSave();
        this.state.busy = true;
        try {
            // Show the same in-editor validation banner as Validate before saving.
            const validateResult = await this._syncValidationFromEditor();
            if (validateResult.errors?.length) {
                this.notification.add(_t("Fix validation errors before saving."), {
                    type: "danger",
                });
                this.announce(_t("Save blocked. Fix validation errors."));
                return;
            }

            const data = await this.orm.call("clm.template", "save_docx_document_html", [
                this.templateId,
                html,
                mode,
            ]);
            this._applyPayload(data, { resetDirty: true });
            if (mode === "inplace") {
                this.notification.add(_t("Changes saved to this Template Version."), {
                    type: "success",
                });
                this.announce(_t("Changes saved to this version."));
            } else {
                this.notification.add(
                    _t("Saved as a New Template Version. Previous versions were left unchanged."),
                    { type: "success" }
                );
                this.announce(_t("Saved as a New Template Version."));
            }
            // Refresh form so the version list / base_document_id update.
            try {
                if (typeof this.props.record.model?.root?.load === "function") {
                    await this.props.record.model.root.load();
                } else if (typeof this.props.record.load === "function") {
                    await this.props.record.load();
                }
            } catch {
                // Editor payload already applied.
            }
        } catch (error) {
            const message = error?.data?.message || error.message || String(error);
            const lines = String(message)
                .split("\n")
                .map((line) => line.trim())
                .filter(Boolean);
            if (lines.length) {
                this.state.errors = lines;
                this.state.warnings = [];
            }
            this.notification.add(message, { type: "danger" });
        } finally {
            this.state.busy = false;
        }
    }
}

export const docxTagEditorField = {
    component: DocxTagEditorField,
    supportedTypes: ["char"],
    additionalClasses: ["o_clm_docx_field", "w-100"],
};

registry.category("fields").add("clm_docx_tag_editor", docxTagEditorField);
