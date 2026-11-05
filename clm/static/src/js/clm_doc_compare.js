/** @odoo-module **/

/**
 * Scroll-lock for Draftable-style side-by-side document compare panes.
 * When one pane scrolls, the other matches (Original ↔ Revised).
 *
 * Fullscreen for the PDF comparison lives in clm_compare_fullscreen.js.
 */

import { registry } from "@web/core/registry";

const BOUND = new WeakSet();

function bindCompareRoot(root) {
    if (!root || BOUND.has(root)) {
        return;
    }
    const left = root.querySelector('[data-clm-scroll-pane="left"]');
    const right = root.querySelector('[data-clm-scroll-pane="right"]');
    if (!left || !right) {
        return;
    }
    BOUND.add(root);

    let syncing = false;
    const sync = (source, target) => {
        if (syncing) {
            return;
        }
        syncing = true;
        const maxSource = source.scrollHeight - source.clientHeight;
        const maxTarget = target.scrollHeight - target.clientHeight;
        const ratio = maxSource > 0 ? source.scrollTop / maxSource : 0;
        target.scrollTop = ratio * Math.max(maxTarget, 0);
        // Keep gutter ticks roughly aligned by matching left scroll to gutter.
        const gutter = root.querySelector(".o_clm_doc_gutter");
        if (gutter) {
            gutter.scrollTop = source.scrollTop;
        }
        requestAnimationFrame(() => {
            syncing = false;
        });
    };

    left.addEventListener(
        "scroll",
        () => sync(left, right),
        { passive: true },
    );
    right.addEventListener(
        "scroll",
        () => sync(right, left),
        { passive: true },
    );
}

function scan(root = document) {
    root.querySelectorAll?.("[data-clm-doc-compare='1']").forEach(bindCompareRoot);
}

const observer = new MutationObserver((mutations) => {
    for (const mutation of mutations) {
        for (const node of mutation.addedNodes) {
            if (!(node instanceof HTMLElement)) {
                continue;
            }
            if (node.matches?.("[data-clm-doc-compare='1']")) {
                bindCompareRoot(node);
            }
            scan(node);
        }
    }
});

export function startClmDocCompare() {
    scan(document);
    observer.observe(document.body, { childList: true, subtree: true });
}

registry.category("services").add("clm_doc_compare", {
    start() {
        // Defer until body exists (backend assets load early).
        if (document.readyState === "loading") {
            document.addEventListener("DOMContentLoaded", startClmDocCompare, {
                once: true,
            });
        } else {
            startClmDocCompare();
        }
        return {};
    },
});
