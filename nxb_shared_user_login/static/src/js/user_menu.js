/** @odoo-module **/
// Copyright 2026 Nextbridge. License OPL-1.
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { session } from "@web/session";
import { imageUrl } from "@web/core/utils/urls";
import { patch } from "@web/core/utils/patch";
import { UserMenu } from "@web/webclient/user_menu/user_menu";

const employee = session.nxb_shared_employee;

if (employee) {
    // show the signed-in employee instead of the shared account in the top bar
    patch(UserMenu.prototype, {
        setup() {
            super.setup(...arguments);
            this.userName = employee.name;
            if (employee.partner_id) {
                this.source = imageUrl("res.partner", employee.partner_id, "avatar_128");
            }
        },
    });

    const items = registry.category("user_menuitems");
    items.add("nxb_shared_switch", () => ({
        type: "item",
        id: "nxb_shared_switch",
        description: _t("Switch employee"),
        href: "/shared_login/switch",
        callback: () => {
            window.location.href = "/shared_login/switch";
        },
        sequence: 1,
    }));
    items.add("nxb_shared_pin", () => ({
        type: "item",
        id: "nxb_shared_pin",
        description: _t("Change my PIN"),
        href: "/shared_login/pin",
        callback: () => {
            window.location.href = "/shared_login/pin";
        },
        sequence: 2,
    }));
}
