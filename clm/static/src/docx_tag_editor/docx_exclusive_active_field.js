/** @odoo-module **/

import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { standardFieldProps } from "@web/views/fields/standard_field_props";
import { useService } from "@web/core/utils/hooks";
import { Component } from "@odoo/owl";

/**
 * Exclusive Active checkbox for Template Versions.
 * Checking a row sets that file as the active Document Template (no modal).
 */
export class ExclusiveActiveBooleanField extends Component {
    static template = "clm.ExclusiveActiveBooleanField";
    static props = { ...standardFieldProps };

    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
    }

    get checked() {
        return Boolean(this.props.record.data[this.props.name]);
    }

    async _reloadParentForm() {
        const record = this.props.record;
        const root = record.model?.root;
        if (root && typeof root.load === "function") {
            await root.load();
            return;
        }
        // x2many datapoint: climb to the parent form record when available.
        let parent = record._parentRecord || record.parentRecord;
        if (parent && typeof parent.load === "function") {
            await parent.load();
            return;
        }
        if (typeof record.load === "function") {
            await record.load();
        }
    }

    async onChange(ev) {
        const wantActive = Boolean(ev.target.checked);
        // Always keep one active: unchecking the current active is not allowed.
        if (!wantActive && this.checked) {
            ev.target.checked = true;
            this.notification.add(
                _t("Tick another version’s Active checkbox to switch. One must stay active."),
                { type: "warning" }
            );
            return;
        }
        if (wantActive && this.checked) {
            return;
        }
        const resId = this.props.record.resId;
        if (!resId || typeof resId !== "number") {
            this.notification.add(_t("Save the template before switching Active version."), {
                type: "warning",
            });
            ev.target.checked = this.checked;
            return;
        }
        try {
            await this.orm.call(this.props.record.resModel, "action_set_active", [[resId]]);
            await this._reloadParentForm();
            this.notification.add(_t("Active Template Version updated."), { type: "success" });
        } catch (error) {
            ev.target.checked = this.checked;
            this.notification.add(error?.data?.message || error.message || String(error), {
                type: "danger",
            });
        }
    }
}

export const exclusiveActiveBooleanField = {
    component: ExclusiveActiveBooleanField,
    supportedTypes: ["boolean"],
    additionalClasses: ["o_clm_exclusive_active_field"],
};

registry.category("fields").add("clm_exclusive_active", exclusiveActiveBooleanField);
