/** @odoo-module **/
// Copyright 2026 Nextbridge. License OPL-1.
import { Component, onWillStart, useState } from "@odoo/owl";
import { browser } from "@web/core/browser/browser";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

export class NxbOverview extends Component {
    static template = "nxb_mcp_hub.Overview";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");
        this.state = useState({ d: null });
        onWillStart(() => this.load());
    }

    async load() {
        this.state.d = await this.orm.call("nxb.mcp.overview", "get_overview", []);
    }

    get maxDay() {
        return Math.max(1, ...this.state.d.per_day.map((p) => p.ok + p.bad));
    }

    async copy(text) {
        await browser.navigator.clipboard.writeText(text);
        this.notification.add("Copied", { type: "success" });
    }

    open(xmlid) {
        this.action.doAction(xmlid);
    }

    openLog(id) {
        this.action.doAction({ type: "ir.actions.act_window", res_model: "nxb.mcp.log", res_id: id,
                               views: [[false, "form"]] });
    }

    openConnection(id) {
        this.action.doAction({ type: "ir.actions.act_window", res_model: "nxb.mcp.connection", res_id: id,
                               views: [[false, "form"]] });
    }
}

registry.category("actions").add("nxb_mcp_overview", NxbOverview);
