/** @odoo-module **/
// Copyright 2026 Nextbridge. License OPL-1.
import { Component, onMounted, onWillStart, onWillUnmount, useRef, useState } from "@odoo/owl";
import { loadBundle } from "@web/core/assets";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

// Categorical palette (first slots validated for colour-vision deficiency on light surfaces).
const PALETTE = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"];

export class NxbDashboardView extends Component {
    static template = "nxb_mcp_hub.DashboardView";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.root = useRef("root");
        this.charts = [];
        this.state = useState({ data: null, error: null, list: null });
        const a = this.props.action || {};
        this.dashboardId = (a.params && a.params.dashboard_id) || (a.context && a.context.active_id) || false;
        onWillStart(async () => {
            await loadBundle("web.chartjs_lib");
            await this.load();
        });
        onMounted(() => this.drawCharts());
        onWillUnmount(() => this.destroyCharts());
    }

    async load() {
        if (!this.dashboardId) {
            this.state.list = await this.orm.searchRead("nxb.mcp.dashboard", [], ["name", "description", "widget_count"]);
            return;
        }
        try {
            this.state.data = await this.orm.call("nxb.mcp.dashboard", "get_view_data", [[this.dashboardId]]);
        } catch (e) {
            this.state.error = (e.data && e.data.message) || String(e);
        }
    }

    async refresh() {
        await this.load();
        this.destroyCharts();
        setTimeout(() => this.drawCharts());
    }

    openDashboard(id) {
        this.action.doAction({ type: "ir.actions.client", tag: "nxb_mcp_dashboard", name: "Dashboard",
                               params: { dashboard_id: id }, context: { active_id: id } });
    }

    editDashboard() {
        this.action.doAction({ type: "ir.actions.act_window", res_model: "nxb.mcp.dashboard",
                               res_id: this.dashboardId, views: [[false, "form"]] });
    }

    openRecord(model, id) {
        this.action.doAction({ type: "ir.actions.act_window", res_model: model, res_id: id, views: [[false, "form"]] });
    }

    fmt(v, w) {
        if (typeof v === "number") {
            const opts = Math.abs(v) >= 100 || Number.isInteger(v) ? { maximumFractionDigits: 0 } : { maximumFractionDigits: 2 };
            return (w.prefix || "") + new Intl.NumberFormat(undefined, opts).format(v) + (w.suffix || "");
        }
        return v;
    }

    destroyCharts() {
        for (const c of this.charts) {
            c.destroy();
        }
        this.charts = [];
    }

    drawCharts() {
        if (!this.state.data || !this.root.el) {
            return;
        }
        const css = getComputedStyle(this.root.el);
        const ink = css.getPropertyValue("--nxb-muted").trim() || "#6b7280";
        const grid = css.getPropertyValue("--nxb-grid").trim() || "rgba(0,0,0,.08)";
        for (const w of this.state.data.widgets) {
            if (!["bar", "line", "pie"].includes(w.kind) || w.error) {
                continue;
            }
            const canvas = this.root.el.querySelector(`canvas[data-widget="${w.id}"]`);
            if (!canvas) {
                continue;
            }
            const isPie = w.kind === "pie";
            const color = PALETTE[0];
            const dataset = {
                label: w.measure_label,
                data: w.values,
                backgroundColor: isPie ? w.labels.map((_, i) => PALETTE[i % PALETTE.length]) : color,
                borderColor: isPie ? css.getPropertyValue("--nxb-card").trim() || "#fff" : color,
                borderWidth: isPie ? 2 : 2,
                borderRadius: w.kind === "bar" ? 4 : 0,
                pointRadius: w.kind === "line" ? 3 : 0,
                tension: 0.25,
                fill: false,
            };
            this.charts.push(new Chart(canvas, {
                type: isPie ? "doughnut" : w.kind,
                data: { labels: w.labels, datasets: [dataset] },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {
                        legend: { display: isPie, position: "right", labels: { color: ink, boxWidth: 12 } },
                        tooltip: { callbacks: { label: (ctx) => `${ctx.label}: ${this.fmt(ctx.parsed.y ?? ctx.parsed, w)}` } },
                    },
                    scales: isPie ? {} : {
                        x: { ticks: { color: ink, maxRotation: 45, autoSkip: true }, grid: { display: false } },
                        y: { beginAtZero: true, ticks: { color: ink }, grid: { color: grid } },
                    },
                },
            }));
        }
    }
}

registry.category("actions").add("nxb_mcp_dashboard", NxbDashboardView);
