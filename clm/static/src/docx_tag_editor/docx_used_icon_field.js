/** @odoo-module **/

import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { standardFieldProps } from "@web/views/fields/standard_field_props";
import { Component } from "@odoo/owl";

/**
 * Read-only boolean as centered tick / cross icons (list-friendly).
 */
export class UsedIconBooleanField extends Component {
    static template = "clm.UsedIconBooleanField";
    static props = { ...standardFieldProps };

    get used() {
        return Boolean(this.props.record.data[this.props.name]);
    }

    get ariaLabel() {
        return this.used
            ? _t("Used by at least one contract")
            : _t("Not used by any contract yet");
    }
}

export const usedIconBooleanField = {
    component: UsedIconBooleanField,
    supportedTypes: ["boolean"],
    additionalClasses: ["o_clm_used_icon_field"],
};

registry.category("fields").add("clm_used_icon", usedIconBooleanField);
