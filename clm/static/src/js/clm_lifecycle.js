/** @odoo-module **/

// The one toolbar control of the Contract Lifecycle chart: a toggle that
// expands every phase card, or collapses every one.
//
// The chart is server-rendered HTML inside a readonly html field, so there is
// no component to own this behaviour. Two delegated listeners on the document
// serve every chart on screen, now and after any re-render. They only flip the
// native `open` flag of the <details> cards inside the chart the control
// belongs to — no request, no write, nothing outside that chart.

const TOGGLE = "[data-clm-lifecycle='toggle']";

/**
 * Point the toggle at whatever it would do next.
 *
 * The button is a state, not a command pair: `aria-expanded` says whether the
 * phases are open, and the icon, label and tooltip all follow from it. Keeping
 * them in one function is what stops them drifting apart.
 *
 * @param {HTMLElement} chart the .o_clm_lifecycle root
 */
function syncToggle(chart) {
    const button = chart.querySelector(TOGGLE);
    if (!button) {
        return;
    }
    const cards = chart.querySelectorAll("details.o_clm_phase_card");
    // "Expanded" only when every card is open, so a half-open chart still
    // offers Expand all — the reading that leaves nothing hidden.
    const expanded = cards.length > 0 && [...cards].every((card) => card.open);

    button.setAttribute("aria-expanded", String(expanded));
    button.dataset.tooltip = expanded
        ? "Collapse every phase"
        : "Expand every phase to see its stages";

    const icon = button.querySelector("i");
    if (icon) {
        icon.classList.toggle("fa-angle-double-up", expanded);
        icon.classList.toggle("fa-angle-double-down", !expanded);
    }
    const label = button.querySelector(".o_clm_lifecycle_tool_label");
    if (label) {
        label.textContent = expanded ? "Collapse all" : "Expand all";
    }
}

document.addEventListener("click", (ev) => {
    const button = ev.target.closest(TOGGLE);
    if (!button) {
        return;
    }
    const chart = button.closest(".o_clm_lifecycle");
    if (!chart) {
        return;
    }
    ev.preventDefault();
    // Act on the state the button currently advertises, so one click always
    // does what its label just said it would.
    const expand = button.getAttribute("aria-expanded") !== "true";
    for (const card of chart.querySelectorAll("details.o_clm_phase_card")) {
        card.open = expand;
    }
    syncToggle(chart);
});

// A reader can also open or close a single phase by clicking its header. When
// they do, the toggle has to catch up — otherwise it claims "Collapse all" over
// a chart that is mostly shut. `toggle` does not bubble, hence the capture.
document.addEventListener(
    "toggle",
    (ev) => {
        const card = ev.target;
        if (!(card instanceof HTMLElement) || !card.matches("details.o_clm_phase_card")) {
            return;
        }
        const chart = card.closest(".o_clm_lifecycle");
        if (chart) {
            syncToggle(chart);
        }
    },
    true,
);
