/** @odoo-module **/
import { Component, onMounted, onWillStart, onWillUnmount, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { WORLD_LAND_PATH } from "./world_land";

// Map groups: 3 categorical slots (validated all-pairs) + neutral gray for closed.
const STAGE_GROUP = {
    queued: "pipeline", on_hold: "pipeline", invited: "pipeline", followed: "pipeline",
    connected: "talking", conversation: "talking", taken_over: "talking",
    lead: "lead",
};
const GROUP_LABEL = { pipeline: "Queued / invited", talking: "Connected / talking", lead: "Warm lead", closed: "Closed" };

export class LiDashboard extends Component {
    static template = "nb_linkedin_ai_prospecting.Dashboard";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.state = useState({
            data: null, profileId: false, personaId: false, days: 30,
            tip: null, mapTip: null, showTable: false,
        });
        this.landPath = WORLD_LAND_PATH;
        this.groupLabel = GROUP_LABEL;
        onWillStart(() => this.load());
        onMounted(() => { this.timer = setInterval(() => this.load(), 60000); });
        onWillUnmount(() => clearInterval(this.timer));
    }

    async load() {
        this.state.data = await this.orm.call("li.dashboard", "get_data", [], {
            profile_id: this.state.profileId || false,
            persona_id: this.state.personaId || false,
            days: this.state.days,
        });
    }

    onFilter(ev, key) {
        const v = ev.target.value;
        this.state[key] = key === "days" ? parseInt(v) : (v ? parseInt(v) : false);
        if (key === "profileId") this.state.personaId = false;
        this.load();
    }

    // ---- charts --------------------------------------------------------
    get perDay() {
        const rows = this.state.data.per_day;
        const max = Math.max(1, ...rows.map((r) => r.count));
        const W = 900, H = 200, pad = 28, gap = 2;
        const bw = (W - pad) / rows.length - gap;
        return {
            W, H, max,
            ticks: [0, Math.ceil(max / 2), max],
            bars: rows.map((r, i) => {
                const h = Math.max((r.count / max) * (H - 30), r.count ? 3 : 0);
                return { ...r, x: pad + i * (bw + gap), y: H - 18 - h, w: bw, h, labelY: H - 4,
                         showLabel: i % 2 === 1 || rows.length < 10 };
            }),
        };
    }

    get funnel() {
        const rows = this.state.data.funnel;
        const max = Math.max(1, ...rows.map((r) => r.count));
        return rows.map((r, i) => ({
            ...r, pct: (r.count / max) * 100,
            conv: i && rows[i - 1].count ? Math.round((100 * r.count) / rows[i - 1].count) + "%" : "",
        }));
    }

    get mapPoints() {
        return this.state.data.points.map((p) => ({
            ...p,
            x: ((p.lon + 180) / 360) * 1000,
            y: ((90 - p.lat) / 180) * 500,
            group: STAGE_GROUP[p.stage] || "closed",
        }));
    }

    showTip(ev, text) {
        const box = ev.currentTarget.closest(".o_li_card").getBoundingClientRect();
        this.state.tip = { text, x: ev.clientX - box.left + 12, y: ev.clientY - box.top - 8,
                           card: ev.currentTarget.closest(".o_li_card").dataset.card };
    }
    hideTip() { this.state.tip = null; }

    showMapTip(ev, p) {
        const box = ev.currentTarget.closest(".o_li_card").getBoundingClientRect();
        this.state.mapTip = { p, x: ev.clientX - box.left + 12, y: ev.clientY - box.top - 8 };
    }

    openProspect(id) {
        this.action.doAction({ type: "ir.actions.act_window", res_model: "li.prospect", res_id: id,
                               views: [[false, "form"]] });
    }
    openProspects(domain) {
        this.action.doAction({ type: "ir.actions.act_window", name: "Prospects", res_model: "li.prospect",
                               views: [[false, "list"], [false, "form"]], domain });
    }
    openStage(stage) { this.openProspects([["stage", "=", stage]]); }
    openAction(xmlid) { this.action.doAction(xmlid); }
}

registry.category("actions").add("li_dashboard", LiDashboard);
