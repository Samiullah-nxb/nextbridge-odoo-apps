/** @odoo-module **/

/**
 * Adds "Word Editor" to the Document Template tab's own toolbar, next to
 * Reload, so both editors are reached from the same place. Edits made here
 * live only in this widget until saved, and opening the Word editor leaves
 * the form, so unsaved work is confirmed away first.
 */

import { ConfirmationDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { _t } from "@web/core/l10n/translation";
import { useService } from "@web/core/utils/hooks";
import { patch } from "@web/core/utils/patch";
import { DocxTagEditorField } from "@clm/docx_tag_editor/docx_tag_editor";

patch(DocxTagEditorField.prototype, {
    setup() {
        super.setup();
        this.actionService = useService("action");
        this.dialogService = useService("dialog");
    },

    async onOpenWordEditor() {
        if (!this.templateId || this.state.busy) {
            return;
        }
        if (this.state.dirty && !(await this.confirmDiscardForWordEditor())) {
            return;
        }
        const action = await this.orm.call("clm.template", "action_open_docx_editor", [
            [this.templateId],
        ]);
        await this.actionService.doAction(action);
    },

    confirmDiscardForWordEditor() {
        return new Promise((resolve) => {
            this.dialogService.add(
                ConfirmationDialog,
                {
                    title: _t("Unsaved changes"),
                    body: _t(
                        "Your changes here have not been saved. Open the Word editor and discard them?"
                    ),
                    confirmLabel: _t("Discard and open"),
                    confirmClass: "btn-danger",
                    cancelLabel: _t("Keep editing"),
                    confirm: () => resolve(true),
                    cancel: () => resolve(false),
                },
                { onClose: () => resolve(false) }
            );
        });
    },
});
