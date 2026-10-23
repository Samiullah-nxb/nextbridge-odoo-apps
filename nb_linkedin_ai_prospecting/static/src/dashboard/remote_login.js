/** @odoo-module **/
import { Component, onMounted, onWillUnmount, useRef, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { rpc } from "@web/core/network/rpc";
import { useService } from "@web/core/utils/hooks";

const SPECIAL = new Set(["Enter", "Backspace", "Tab", "Delete", "ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown",
                         "Escape", "Home", "End"]);

/** LinkedIn's sign-in page streamed from the engine: screenshots out, clicks and keys in. */
export class LiRemoteLogin extends Component {
    static template = "nb_linkedin_ai_prospecting.RemoteLogin";
    static props = ["*"];

    setup() {
        this.action = useService("action");
        this.notification = useService("notification");
        this.screen = useRef("screen");
        this.profileId = this.props.action.params.profile_id;
        this.state = useState({ image: null, loggedIn: false, error: null, busy: false });
        this.buffer = "";
        onMounted(async () => {
            try {
                await rpc(`/li/engine/${this.profileId}/open_login`, {});
            } catch (e) {
                this.state.error = e.data?.message || String(e);
            }
            this.refresh();
            this.timer = setInterval(() => this.refresh(), 1200);
        });
        onWillUnmount(() => clearInterval(this.timer));
    }

    base() { return `/li/engine/${this.profileId}`; }

    async refresh() {
        if (this.refreshing) return;
        this.refreshing = true;
        try {
            const r = await rpc(this.base() + "/screen", {});
            if (r.shot) this.state.image = r.shot.image;
            this.state.loggedIn = !!r.logged_in;
            this.state.error = r.error || null;
        } catch (e) {
            this.state.error = e.data?.message || String(e);
        } finally {
            this.refreshing = false;
        }
    }

    async send(params) {
        await this.flushText();
        await rpc(this.base() + "/input", params);
        this.refresh();
    }

    async flushText() {
        if (!this.buffer) return;
        const text = this.buffer;
        this.buffer = "";
        await rpc(this.base() + "/input", { kind: "type", text });
    }

    onClick(ev) {
        const img = ev.currentTarget;
        const rect = img.getBoundingClientRect();
        const x = ((ev.clientX - rect.left) / rect.width) * img.naturalWidth;
        const y = ((ev.clientY - rect.top) / rect.height) * img.naturalHeight;
        this.screen.el.focus();
        this.send({ kind: "click", x, y });
    }

    onWheel(ev) {
        ev.preventDefault();
        this.send({ kind: "scroll", y: ev.deltaY });
    }

    onKey(ev) {
        if (ev.ctrlKey || ev.metaKey) return; // let paste through
        if (SPECIAL.has(ev.key)) {
            ev.preventDefault();
            this.send({ kind: "key", key: ev.key });
        } else if (ev.key.length === 1) {
            ev.preventDefault();
            this.buffer += ev.key;
            clearTimeout(this.flushTimer);
            this.flushTimer = setTimeout(() => this.flushText().then(() => this.refresh()), 250);
        }
    }

    onPaste(ev) {
        const text = ev.clipboardData.getData("text");
        if (text) {
            ev.preventDefault();
            this.send({ kind: "type", text: text.trim() });
        }
    }

    async finish() {
        this.state.busy = true;
        try {
            const r = await rpc(this.base() + "/finish_login", {});
            if (r.ok) {
                this.notification.add(`Connected as ${r.name || "your account"}` +
                    (r.english ? "" : " — set LinkedIn's language to English"), { type: r.english ? "success" : "warning" });
                this.action.doAction({ type: "ir.actions.act_window_close" });
            } else {
                this.state.error = r.error || "Not signed in yet";
            }
        } finally {
            this.state.busy = false;
        }
    }
}

registry.category("actions").add("li_remote_login", LiRemoteLogin);
