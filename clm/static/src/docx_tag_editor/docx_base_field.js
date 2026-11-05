/** @odoo-module **/

import { Component, useRef } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { standardFieldProps } from "@web/views/fields/standard_field_props";
import { useService } from "@web/core/utils/hooks";

/**
 * Standalone Upload button for Document Template (beside the Documents many2one).
 */
export class BaseDocxUploadField extends Component {
    static template = "clm.BaseDocxUploadField";
    static props = { ...standardFieldProps };

    setup() {
        this.notification = useService("notification");
        this.fileInputRef = useRef("fileInput");
        this._uploading = false;
    }

    onUploadClick() {
        if (this.props.readonly || this._uploading) {
            return;
        }
        this.fileInputRef.el?.click();
    }

    async onFileSelected(ev) {
        const input = ev.target;
        const file = input.files && input.files[0];
        input.value = "";
        if (!file || this.props.readonly) {
            return;
        }
        const name = file.name || "Document Template.docx";
        if (!name.toLowerCase().endsWith(".docx")) {
            this.notification.add(_t("Please upload a .docx file."), { type: "danger" });
            return;
        }
        this._uploading = true;
        try {
            const b64 = await this._readFileAsBase64(file);
            await this.props.record.update({
                base_docx_upload: b64,
                base_docx_upload_filename: name,
            });
            const saved = await this.props.record.save({ reload: true });
            if (saved === false) {
                this.notification.add(
                    _t("Save the template (required fields) to attach the Document Template."),
                    { type: "warning" }
                );
                return;
            }
            this.notification.add(_t("Document Template uploaded."), { type: "success" });
        } catch (error) {
            this.notification.add(error?.data?.message || error.message || String(error), {
                type: "danger",
            });
        } finally {
            this._uploading = false;
        }
    }

    _readFileAsBase64(file) {
        return new Promise((resolve, reject) => {
            const reader = new FileReader();
            reader.onload = () => {
                const result = reader.result || "";
                const comma = String(result).indexOf(",");
                resolve(comma >= 0 ? String(result).slice(comma + 1) : String(result));
            };
            reader.onerror = () => reject(reader.error || new Error("Failed to read file"));
            reader.readAsDataURL(file);
        });
    }
}

registry.category("fields").add("clm_base_docx_upload", {
    component: BaseDocxUploadField,
    displayName: _t("Document Template Upload"),
    supportedTypes: ["char"],
    additionalClasses: ["o_clm_base_docx_upload_field"],
});
