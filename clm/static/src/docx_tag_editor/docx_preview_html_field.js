/** @odoo-module **/

import { registry } from "@web/core/registry";
import { standardFieldProps } from "@web/views/fields/standard_field_props";
import {
    Component,
    onMounted,
    onPatched,
    onWillDestroy,
    useRef,
} from "@odoo/owl";

/**
 * Read-only HTML preview that keeps DOCX header logos (data: URIs).
 * Odoo's default Html field can strip / fail to show large data-URI images.
 */
export class DocxPreviewHtmlField extends Component {
    static template = "clm.DocxPreviewHtmlField";
    static props = { ...standardFieldProps };

    setup() {
        this.rootRef = useRef("root");
        this._blobUrls = [];
        this._lastHtml = null;
        onMounted(() => this._syncDom());
        onPatched(() => this._syncDom());
        onWillDestroy(() => this._revokeBlobUrls());
    }

    get htmlValue() {
        const value = this.props.record.data[this.props.name];
        return value && typeof value === "object" && "toString" in value
            ? value.toString()
            : value || "";
    }

    _revokeBlobUrls() {
        this._blobUrls.forEach((url) => {
            try {
                URL.revokeObjectURL(url);
            } catch {
                // ignore
            }
        });
        this._blobUrls = [];
    }

    _materializeDataImages(rootEl) {
        if (!rootEl) {
            return;
        }
        rootEl.querySelectorAll("img[src^='data:']").forEach((img) => {
            const src = img.getAttribute("src") || "";
            const match = /^data:([^;,]+)?(;base64)?,(.*)$/s.exec(src);
            if (!match || !match[2]) {
                return;
            }
            try {
                const mime = match[1] || "image/png";
                const binary = atob(match[3] || "");
                const bytes = new Uint8Array(binary.length);
                for (let i = 0; i < binary.length; i++) {
                    bytes[i] = binary.charCodeAt(i);
                }
                const url = URL.createObjectURL(new Blob([bytes], { type: mime }));
                this._blobUrls.push(url);
                img.setAttribute("src", url);
                img.classList.add("o_clm_docx_img");
            } catch (error) {
                console.warn("DOCX preview image materialize failed", error);
            }
        });
    }

    _syncDom() {
        const el = this.rootRef.el;
        if (!el) {
            return;
        }
        const html = this.htmlValue;
        if (html === this._lastHtml && el.childNodes.length) {
            return;
        }
        this._revokeBlobUrls();
        el.innerHTML = html || "<p class='text-muted'>No preview.</p>";
        this._materializeDataImages(el);
        this._lastHtml = html;
    }
}

export const docxPreviewHtmlField = {
    component: DocxPreviewHtmlField,
    supportedTypes: ["html"],
};

registry.category("fields").add("clm_docx_preview_html", docxPreviewHtmlField);
