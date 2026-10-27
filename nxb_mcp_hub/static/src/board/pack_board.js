/** @odoo-module **/
// Copyright 2026 Nextbridge. License OPL-1.
import { Component, onWillStart, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

export class NxbPackBoard extends Component {
    static template = "nxb_mcp_hub.PackBoard";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.state = useState({ board: null, open: {}, busy: false });
        onWillStart(async () => {
            this.state.board = await this.orm.call("nxb.mcp.pack.board", "get_board", []);
        });
    }

    async toggle(card) {
        if (!this.state.board.can_edit || this.state.busy) {
            return;
        }
        this.state.busy = true;
        const wasOn = card.switch;
        try {
            this.state.board = await this.orm.call("nxb.mcp.pack.board", "set_pack", [card.key, !wasOn]);
            this.notification.add(`${card.title} ${wasOn ? "switched off" : "switched on"}. ` +
                "Apps that cache the tool list pick this up when they reconnect.", { type: "info" });
        } finally {
            this.state.busy = false;
        }
    }

    toggleOpen(key) {
        this.state.open[key] = !this.state.open[key];
    }

    fmt(n) {
        return new Intl.NumberFormat().format(n);
    }
}

registry.category("actions").add("nxb_mcp_pack_board", NxbPackBoard);
