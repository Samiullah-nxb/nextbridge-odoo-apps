/** @odoo-module **/

/**
 * Hosts the docunext Word editor (bundled in static/editor/) in an iframe for
 * one Library template, speaking its ude-host/0.1 postMessage protocol. Odoo
 * loads the template's active Document Template DOCX into the editor; the
 * edited bytes come back and are stored by this module's own routes, either
 * onto the active Template Version (Save) or as a new one (Save as New
 * Version).
 *
 * The protocol has a single export message, so each export is tagged on the
 * host side with what asked for it (Save, Save as New Version, Check tags).
 * An export nobody asked for is the editor's own Save / Ctrl+S, which saves
 * in place.
 *
 * The editor reports "clean" once it has handed the bytes over, which is not
 * the same as stored. `state.unsaved` only clears after the server confirms.
 */

import {
    Component, onMounted, onWillUnmount, onWillStart,
    useExternalListener, useRef, useState, useSubEnv,
} from "@odoo/owl";
import { ConfirmationDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { user } from "@web/core/user";
import { useService } from "@web/core/utils/hooks";
import { useSetupAction } from "@web/search/action_hook";
import { Layout } from "@web/search/layout";
import { getDefaultConfig } from "@web/views/view";
import { standardActionServiceProps } from "@web/webclient/actions/action_service";

// The protocol of the editor build in static/editor/. Messages in any other
// protocol are ignored, so a rebuilt editor must be checked against this.
const SUPPORTED_PROTOCOL = "ude-host/0.1";
const DOCX_MIMETYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document";
const EXPORT_TIMEOUT_MS = 30000;

function _fullscreenElement() {
    return (
        document.fullscreenElement ||
        document.webkitFullscreenElement ||
        document.msFullscreenElement ||
        null
    );
}

async function _copyText(text) {
    try {
        await navigator.clipboard.writeText(text);
        return true;
    } catch {
        // Clipboard API needs a secure context; fall back for plain http.
        const area = document.createElement("textarea");
        area.value = text;
        area.style.position = "fixed";
        area.style.opacity = "0";
        document.body.appendChild(area);
        area.select();
        const copied = document.execCommand("copy");
        area.remove();
        return copied;
    }
}

export class ClmDocxEditor extends Component {
    static template = "clm.DocxEditor";
    static components = { Layout };
    static props = { ...standardActionServiceProps };

    setup() {
        useSubEnv({ config: { ...getDefaultConfig(), ...this.env.config } });
        this.orm = useService("orm");
        this.actionService = useService("action");
        this.dialog = useService("dialog");
        this.notification = useService("notification");
        this.frame = useRef("frame");
        this.shell = useRef("shell");
        this.state = useState({
            info: null,
            error: "",
            conflict: "",
            loaded: false,
            unsaved: false,
            busy: null, // "inplace" | "new" | "validate" while an export is handled
            changeSummary: "",
            savedLabel: "",
            fullscreen: false,
            panelOpen: true,
            search: "",
            issues: null, // {errors, warnings} from the last check or save
        });
        const { action } = this.props;
        this.templateId = action.params?.template_id || action.context?.active_id;
        this.editorDirty = false;
        this.initialized = false;
        // What asked for each export still on its way back from the editor.
        this.pendingIntents = [];
        this.exportTimer = null;
        // Last bytes the editor handed over, for "Download my copy".
        this.lastExport = null;
        this._onFullscreenChange = () => this.syncFullscreenState();

        useExternalListener(window, "message", this.onMessage);
        useSetupAction({
            beforeLeave: (options) => this.confirmLeave(options),
            beforeUnload: (ev) => {
                if (this.state.unsaved) {
                    ev.preventDefault();
                    ev.returnValue = "";
                }
            },
        });
        onWillStart(async () => {
            if (!this.templateId) {
                this.state.error = _t("Open the Word editor from a template.");
                return;
            }
            try {
                await this.loadInfo();
            } catch (error) {
                this.state.error = error.data?.message || error.message;
            }
        });
        onMounted(() => {
            document.addEventListener("fullscreenchange", this._onFullscreenChange);
            document.addEventListener("webkitfullscreenchange", this._onFullscreenChange);
            document.addEventListener("MSFullscreenChange", this._onFullscreenChange);
        });
        onWillUnmount(() => {
            this.clearExportTimer();
            document.removeEventListener("fullscreenchange", this._onFullscreenChange);
            document.removeEventListener("webkitfullscreenchange", this._onFullscreenChange);
            document.removeEventListener("MSFullscreenChange", this._onFullscreenChange);
            if (_fullscreenElement() === this.shell.el) {
                this.exitFullscreen();
            }
        });
    }

    async loadInfo() {
        this.state.info = await this.orm.call(
            "clm.template", "get_docx_editor_info", [[this.templateId]]
        );
    }

    // -------------------------------------------------------------------------
    // Fullscreen
    // -------------------------------------------------------------------------

    syncFullscreenState() {
        this.state.fullscreen = _fullscreenElement() === this.shell.el;
    }

    async toggleFullscreen() {
        if (this.state.fullscreen) {
            await this.exitFullscreen();
        } else {
            await this.enterFullscreen();
        }
    }

    async enterFullscreen() {
        const el = this.shell.el;
        if (!el) {
            return;
        }
        try {
            if (el.requestFullscreen) {
                await el.requestFullscreen();
            } else if (el.webkitRequestFullscreen) {
                await el.webkitRequestFullscreen();
            } else if (el.msRequestFullscreen) {
                await el.msRequestFullscreen();
            }
        } catch (_err) {
            this.notification.add(_t("Fullscreen is not available in this browser."), {
                type: "warning",
            });
        }
        this.syncFullscreenState();
    }

    async exitFullscreen() {
        try {
            if (document.exitFullscreen) {
                await document.exitFullscreen();
            } else if (document.webkitExitFullscreen) {
                await document.webkitExitFullscreen();
            } else if (document.msExitFullscreen) {
                await document.msExitFullscreen();
            }
        } catch (_err) {
            // Already exited or unsupported.
        }
        this.syncFullscreenState();
    }

    // -------------------------------------------------------------------------
    // Editor bridge
    // -------------------------------------------------------------------------

    get editorSrc() {
        const url = new URL(this.state.info.editor_url, window.location.origin);
        url.searchParams.set("hostOrigin", window.location.origin);
        // This component's own bar replaces the editor's title bar, File
        // workspace and quick-tools rail.
        url.searchParams.set("chrome", "compact");
        if (!this.state.info.can_edit) {
            url.searchParams.set("readOnly", "1");
        }
        return url.pathname + url.search;
    }

    send(type, payload, transfer = []) {
        const message = { protocol: SUPPORTED_PROTOCOL, type };
        if (payload !== undefined) {
            message.payload = payload;
        }
        this.frame.el?.contentWindow?.postMessage(message, window.location.origin, transfer);
    }

    async onMessage(ev) {
        const editorWindow = this.frame.el?.contentWindow;
        if (!editorWindow || ev.source !== editorWindow || ev.origin !== window.location.origin) {
            return;
        }
        const message = ev.data;
        if (message?.protocol !== SUPPORTED_PROTOCOL) {
            return;
        }
        switch (message.type) {
            case "ude:ready":
                await this.onEditorReady();
                break;
            case "ude:document-opened":
                this.state.loaded = true;
                break;
            case "ude:dirty":
                this.editorDirty = message.payload.dirty;
                if (this.editorDirty) {
                    this.state.unsaved = true;
                }
                break;
            case "ude:save-result":
                await this.onExport(message.payload.document);
                break;
            case "ude:error":
                if (this.state.busy) {
                    this.pendingIntents = [];
                }
                this.endExport();
                this.notification.add(message.payload.message, { type: "danger" });
                break;
        }
    }

    /** A second ready means the iframe restarted and lost what it held. */
    async onEditorReady() {
        if (this.initialized && this.state.unsaved) {
            this.notification.add(
                _t("The Word editor restarted and its unsaved edits were lost. The last saved version was reopened."),
                { type: "warning", sticky: true }
            );
        }
        this.initialized = true;
        this.state.unsaved = false;
        this.pendingIntents = [];
        this.endExport();
        await this.sendDocument("ude:init");
    }

    async fetchDocument() {
        const response = await fetch(`/clm_docx_editor/template/${this.templateId}/document`, {
            cache: "no-store",
        });
        if (!response.ok) {
            const result = await response.json().catch(() => ({}));
            throw new Error(result.error || _t("The Word document could not be loaded."));
        }
        return response.arrayBuffer();
    }

    async sendDocument(type) {
        const { info } = this.state;
        try {
            const buffer = await this.fetchDocument();
            const payload = { filename: info.filename, document: buffer };
            if (type === "ude:init") {
                payload.author = user.name;
                payload.mode = info.can_edit ? "editing" : "viewing";
            }
            this.state.loaded = false;
            this.send(type, payload, [buffer]);
        } catch (error) {
            this.state.error = error.message;
        }
    }

    // -------------------------------------------------------------------------
    // Exports: Save, Save as New Version, Check tags
    // -------------------------------------------------------------------------

    requestExport(intent) {
        if (this.state.busy || !this.state.loaded) {
            return;
        }
        if (intent !== "validate" && !this.state.unsaved) {
            this.notifyNothingChanged();
            return;
        }
        this.state.busy = intent;
        this.pendingIntents.push(intent);
        this.exportTimer = setTimeout(() => {
            // Exports carry no request id: one that arrives after this must
            // not be taken for a later Save or for the editor's own Ctrl+S.
            this.pendingIntents = this.pendingIntents.map(() => "discard");
            this.endExport();
            this.notification.add(
                _t("The editor did not hand over the document in time. Your edits are still open; try again."),
                { type: "danger" }
            );
        }, EXPORT_TIMEOUT_MS);
        this.send("ude:save-request");
    }

    save() {
        this.requestExport("inplace");
    }

    saveAsNewVersion() {
        this.requestExport("new");
    }

    checkTags() {
        this.requestExport("validate");
    }

    clearExportTimer() {
        if (this.exportTimer !== null) {
            clearTimeout(this.exportTimer);
            this.exportTimer = null;
        }
    }

    endExport() {
        this.clearExportTimer();
        this.state.busy = null;
    }

    async onExport(buffer) {
        this.clearExportTimer();
        // The editor's own Save / Ctrl+S was not asked for by the host: save as is.
        const intent = this.pendingIntents.shift() || "inplace";
        this.lastExport = buffer.slice(0);
        try {
            if (intent === "discard") {
                this.notification.add(
                    _t("A late export from the editor was discarded. Save again."), { type: "warning" }
                );
            } else if (intent === "validate") {
                await this.validate(buffer);
            } else if (!this.state.info.can_edit) {
                return;
            } else if (this.state.conflict) {
                this.notification.add(this.state.conflict, { title: _t("Not saved"), type: "danger" });
            } else if (this.state.unsaved) {
                this.state.busy = intent;
                await this.persist(buffer, intent);
            } else {
                this.notifyNothingChanged();
            }
        } finally {
            this.endExport();
        }
    }

    notifyNothingChanged() {
        this.notification.add(
            _t("Nothing has changed since %s, so nothing was saved.", this.state.info.version_label),
            { type: "info" }
        );
    }

    documentForm(buffer) {
        const body = new FormData();
        body.append("document", new Blob([buffer], { type: DOCX_MIMETYPE }), this.state.info.filename);
        body.append("csrf_token", odoo.csrf_token);
        return body;
    }

    async postDocument(route, body) {
        const response = await fetch(`/clm_docx_editor/template/${this.templateId}/${route}`, {
            method: "POST",
            body,
        });
        const result = await response.json().catch(() => ({}));
        return { response, result };
    }

    async validate(buffer) {
        const { response, result } = await this.postDocument("validate", this.documentForm(buffer));
        if (!response.ok && !result.errors) {
            this.notification.add(result.error || _t("The tags could not be checked."), { type: "danger" });
            return;
        }
        this.state.issues = { errors: result.errors || [], warnings: result.warnings || [] };
        this.state.panelOpen = true;
        if (this.state.issues.errors.length) {
            this.notification.add(
                _t("%s tag problem(s) found. They must be fixed before saving.", this.state.issues.errors.length),
                { type: "danger" }
            );
        } else {
            this.notification.add(_t("All tags are valid."), { type: "success" });
        }
    }

    /** Every save path ends here: the host buttons and the editor's own Save / Ctrl+S. */
    async persist(buffer, mode) {
        const { info } = this.state;
        const body = this.documentForm(buffer);
        body.append("mode", mode);
        body.append("version_token", info.version_token);
        body.append("change_summary", this.state.changeSummary);
        let response, result;
        try {
            ({ response, result } = await this.postDocument("save", body));
        } catch {
            this.notification.add(_t("Connection lost. Your edits are still open; try again."), {
                title: _t("Not saved"),
                type: "danger",
                sticky: true,
            });
            return;
        }
        if (response.status === 409) {
            this.state.conflict = result.error;
            return;
        }
        if (!response.ok) {
            if (result.errors?.length) {
                this.state.issues = { errors: result.errors, warnings: result.warnings || [] };
                this.state.panelOpen = true;
            }
            this.notification.add(result.error || _t("The document could not be saved."), {
                title: _t("Not saved"),
                type: "danger",
                sticky: true,
            });
            return;
        }
        // The template stays the same record; only its active version may change.
        this.state.info = {
            ...info,
            version_label: result.version_label,
            version_token: result.version_token,
            active_is_immutable: result.active_is_immutable,
        };
        this.state.savedLabel = result.version_label;
        this.state.changeSummary = "";
        this.state.unsaved = this.editorDirty;
        this.state.issues = { errors: [], warnings: result.warnings || [] };
        if (mode === "inplace" && result.created_new_version) {
            this.notification.add(
                _t("The version you edited is the original or is used by contracts, so your changes were saved as a new Template Version %s.", result.version_label),
                { type: "warning" }
            );
        } else if (result.created_new_version) {
            this.notification.add(_t("Saved as Template Version %s.", result.version_label), { type: "success" });
        } else {
            this.notification.add(_t("Saved to %s.", result.version_label), { type: "success" });
        }
    }

    // -------------------------------------------------------------------------
    // Conflict recovery
    // -------------------------------------------------------------------------

    downloadMyCopy() {
        if (!this.lastExport) {
            return;
        }
        const url = URL.createObjectURL(new Blob([this.lastExport], { type: DOCX_MIMETYPE }));
        const link = document.createElement("a");
        link.href = url;
        link.download = this.state.info.filename;
        link.click();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
    }

    reloadLatest() {
        this.dialog.add(ConfirmationDialog, {
            title: _t("Reload the latest version"),
            body: _t("Your edits in the editor will be replaced by the latest saved Template Version. Download your copy first if you want to keep them."),
            confirmLabel: _t("Reload latest"),
            confirmClass: "btn-danger",
            cancelLabel: _t("Cancel"),
            confirm: async () => {
                await this.loadInfo();
                this.state.conflict = "";
                this.state.unsaved = false;
                this.state.savedLabel = "";
                this.pendingIntents = [];
                this.endExport();
                await this.sendDocument("ude:open");
            },
            cancel: () => {},
        });
    }

    // -------------------------------------------------------------------------
    // Tags panel
    // -------------------------------------------------------------------------

    get tagGroups() {
        const { info, search } = this.state;
        if (!info) {
            return [];
        }
        const needle = search.trim().toLowerCase();
        const matches = (item) =>
            !needle ||
            item.label.toLowerCase().includes(needle) ||
            item.insert.toLowerCase().includes(needle);
        const groups = new Map();
        for (const tag of info.tags || []) {
            const item = { id: tag.key, label: tag.label, insert: tag.insert, snippet: !!tag.is_snippet };
            if (!groups.has(tag.group)) {
                groups.set(tag.group, []);
            }
            groups.get(tag.group).push(item);
        }
        const clauseLines = (info.clause_set_compact_lines || []).filter((line) => line.trim());
        if (clauseLines.length) {
            groups.set(_t("Clause set"), [
                {
                    id: "clause_set_all",
                    label: _t("All clauses (title, category, content)"),
                    insert: info.clause_set_compact_lines.join("\n"),
                    snippet: true,
                },
                ...clauseLines.map((line) => ({ id: line, label: line, insert: line, snippet: false })),
            ]);
        }
        return [...groups.entries()]
            .map(([name, items]) => ({ name, items: items.filter(matches) }))
            .filter((group) => group.items.length);
    }

    togglePanel() {
        this.state.panelOpen = !this.state.panelOpen;
    }

    async copyTag(item) {
        if (await _copyText(item.insert)) {
            this.notification.add(_t("Copied. Paste it into the document with Ctrl+V."), { type: "info" });
        } else {
            this.notification.add(_t("Copy failed. Select the tag and copy it manually."), { type: "warning" });
        }
    }

    // -------------------------------------------------------------------------
    // Navigation
    // -------------------------------------------------------------------------

    openTemplate() {
        this.actionService.doAction({
            type: "ir.actions.act_window",
            res_model: "clm.template",
            res_id: this.templateId,
            views: [[false, "form"]],
        });
    }

    confirmLeave({ forceLeave } = {}) {
        if (!this.state.unsaved || forceLeave) {
            return true;
        }
        return new Promise((resolve) => {
            this.dialog.add(ConfirmationDialog, {
                title: _t("Unsaved changes"),
                body: _t("Your edits have not been saved. Leave the Word editor and discard them?"),
                confirmLabel: _t("Discard and leave"),
                confirmClass: "btn-danger",
                cancelLabel: _t("Keep editing"),
                confirm: () => resolve(true),
                cancel: () => resolve(false),
            }, { onClose: () => resolve(false) });
        });
    }
}

registry.category("actions").add("clm_docx_editor", ClmDocxEditor);
