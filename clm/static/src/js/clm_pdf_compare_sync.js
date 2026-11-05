/** @odoo-module **/

/**
 * Side-by-side PDF comparison: synchronized scrolling + Draftable-style minimap.
 *
 * - Scrolling either PDF.js pane scrolls the other by the same ratio.
 * - Middle gutter is a fixed-height minimap of the whole document.
 * - Gray viewport thumb is draggable (and the track is clickable) to scroll both panes.
 * - Red/green ticks mark change positions.
 */

import { registry } from "@web/core/registry";

const BOUND = new WeakSet();

function _pdfScrollContainer(iframe) {
    try {
        const win = iframe?.contentWindow;
        const app = win?.PDFViewerApplication;
        const container = app?.pdfViewer?.container;
        if (container) {
            return container;
        }
        return win?.document?.getElementById("viewerContainer") || null;
    } catch (_err) {
        return null;
    }
}

function _paneIframe(pane) {
    return pane?.querySelector?.(".o_clm_pdf_iframe") || null;
}

const _SCROLLBAR_STYLE_ID = "clm-pdf-compare-hide-scrollbars";
const _SCROLLBAR_CSS = `
#viewerContainer,
#viewer,
#mainContainer,
#outerContainer,
html,
body {
  scrollbar-width: none !important;
  -ms-overflow-style: none !important;
}
#viewerContainer::-webkit-scrollbar,
#viewer::-webkit-scrollbar,
#mainContainer::-webkit-scrollbar,
#outerContainer::-webkit-scrollbar,
html::-webkit-scrollbar,
body::-webkit-scrollbar {
  width: 0 !important;
  height: 0 !important;
  display: none !important;
  background: transparent !important;
}
`;

function _hidePdfIframeScrollbars(iframe) {
    try {
        const doc = iframe?.contentDocument;
        if (!doc?.head) {
            return;
        }
        if (doc.getElementById(_SCROLLBAR_STYLE_ID)) {
            return;
        }
        const style = doc.createElement("style");
        style.id = _SCROLLBAR_STYLE_ID;
        style.textContent = _SCROLLBAR_CSS;
        doc.head.appendChild(style);
    } catch (_err) {
        // Same-origin PDF.js only; ignore until the iframe is ready.
    }
}

function _clamp(value, min, max) {
    return Math.min(Math.max(value, min), max);
}

function _bindCompareRow(row) {
    if (!row || BOUND.has(row)) {
        return;
    }
    const leftPane = row.querySelector('[data-clm-pdf-pane="left"]');
    const rightPane = row.querySelector('[data-clm-pdf-pane="right"]');
    const gutter = row.querySelector("[data-clm-pdf-gutter='1']");
    if (!leftPane || !rightPane) {
        return;
    }
    BOUND.add(row);

    let syncing = false;
    let leftScroll = null;
    let rightScroll = null;
    let dragging = false;
    let dragOffsetY = 0;

    const syncFrom = (source, target) => {
        if (syncing || dragging || !source || !target) {
            return;
        }
        const maxSource = source.scrollHeight - source.clientHeight;
        const maxTarget = target.scrollHeight - target.clientHeight;
        if (maxSource <= 0) {
            return;
        }
        syncing = true;
        target.scrollTop = (source.scrollTop / maxSource) * Math.max(maxTarget, 0);
        _syncGutter(source);
        requestAnimationFrame(() => {
            syncing = false;
        });
    };

    const _primaryScroll = () => leftScroll || rightScroll;

    const _scrollBothToRatio = (ratio) => {
        const r = _clamp(ratio, 0, 1);
        syncing = true;
        for (const el of [leftScroll, rightScroll]) {
            if (!el) {
                continue;
            }
            const max = Math.max(el.scrollHeight - el.clientHeight, 0);
            el.scrollTop = r * max;
        }
        const source = _primaryScroll();
        if (source) {
            _syncGutter(source);
        }
        requestAnimationFrame(() => {
            syncing = false;
        });
    };

    const _alignGutterToViewer = () => {
        if (!gutter) {
            return;
        }
        const wrap = gutter.closest(".o_clm_pdf_compare_gutter_wrap");
        const leftFrame =
            leftPane.querySelector(".o_clm_pdf_viewer_frame") ||
            leftPane.querySelector(".o_clm_pdf_iframe");
        if (!wrap || !leftFrame) {
            return;
        }
        const rowRect = row.getBoundingClientRect();
        const frameRect = leftFrame.getBoundingClientRect();
        const topPad = Math.max(0, frameRect.top - rowRect.top);
        wrap.style.paddingTop = `${topPad}px`;
        gutter.style.height = `${frameRect.height}px`;
        gutter.style.minHeight = `${frameRect.height}px`;
    };

    const _thumbMetrics = (source) => {
        const trackH = Math.max(gutter?.clientHeight || 0, 1);
        const docH = Math.max(source?.scrollHeight || 0, 1);
        const viewH = Math.max(source?.clientHeight || 0, 1);
        const maxScroll = Math.max(docH - viewH, 0);
        const thumbH = Math.min(Math.max((viewH / docH) * trackH, 28), trackH);
        const maxThumb = Math.max(trackH - thumbH, 0);
        const scrollTop = Math.max(source?.scrollTop || 0, 0);
        const thumbTop =
            maxScroll > 0 && maxThumb > 0
                ? (scrollTop / maxScroll) * maxThumb
                : 0;
        return {trackH, thumbH, thumbTop, maxThumb, maxScroll};
    };

    const _syncGutter = (source) => {
        if (!gutter || !source) {
            return;
        }
        const rail = gutter.querySelector(".o_clm_pdf_gutter_rail");
        const viewport = gutter.querySelector("[data-clm-pdf-gutter-viewport='1']");
        if (!rail) {
            return;
        }
        _alignGutterToViewer();

        const {trackH, thumbH, thumbTop} = _thumbMetrics(source);
        rail.style.height = `${trackH}px`;
        rail.style.minHeight = `${trackH}px`;

        if (viewport) {
            viewport.style.top = `${thumbTop}px`;
            viewport.style.height = `${thumbH}px`;
        }

        rail.querySelectorAll(".o_clm_pdf_gutter_tick").forEach((tick) => {
            tick.style.position = "absolute";
            tick.style.left = "3px";
            tick.style.right = "3px";
            tick.style.height = "2px";
            tick.style.minHeight = "2px";
            tick.style.zIndex = "2";
            tick.style.pointerEvents = "none";
        });
    };

    const _applyThumbTop = (thumbTop) => {
        const source = _primaryScroll();
        if (!source || !gutter) {
            return;
        }
        const {maxThumb, thumbH} = _thumbMetrics(source);
        const nextTop = _clamp(thumbTop, 0, maxThumb);
        const ratio = maxThumb > 0 ? nextTop / maxThumb : 0;
        const viewport = gutter.querySelector("[data-clm-pdf-gutter-viewport='1']");
        if (viewport) {
            viewport.style.top = `${nextTop}px`;
            viewport.style.height = `${thumbH}px`;
        }
        _scrollBothToRatio(ratio);
    };

    const _onPointerMove = (ev) => {
        if (!dragging || !gutter) {
            return;
        }
        ev.preventDefault();
        const rect = gutter.getBoundingClientRect();
        const y = (ev.touches ? ev.touches[0].clientY : ev.clientY) - rect.top;
        _applyThumbTop(y - dragOffsetY);
    };

    const _onPointerUp = () => {
        if (!dragging) {
            return;
        }
        dragging = false;
        gutter?.classList.remove("o_clm_gutter_dragging");
        const viewport = gutter?.querySelector?.("[data-clm-pdf-gutter-viewport='1']");
        viewport?.classList.remove("is-dragging");
        window.removeEventListener("pointermove", _onPointerMove);
        window.removeEventListener("pointerup", _onPointerUp);
        window.removeEventListener("pointercancel", _onPointerUp);
        window.removeEventListener("touchmove", _onPointerMove);
        window.removeEventListener("touchend", _onPointerUp);
    };

    const _onGutterPointerDown = (ev) => {
        const source = _primaryScroll();
        if (!gutter || !source) {
            return;
        }
        // Only primary button / touch.
        if (ev.pointerType === "mouse" && ev.button !== 0) {
            return;
        }
        const rect = gutter.getBoundingClientRect();
        const clientY = ev.touches ? ev.touches[0].clientY : ev.clientY;
        const y = clientY - rect.top;
        const {thumbTop, thumbH} = _thumbMetrics(source);
        const onThumb = y >= thumbTop && y <= thumbTop + thumbH;

        if (onThumb) {
            dragOffsetY = y - thumbTop;
        } else {
            // Click track → jump so thumb centers on the click, then drag.
            dragOffsetY = thumbH / 2;
            _applyThumbTop(y - dragOffsetY);
        }

        dragging = true;
        gutter.classList.add("o_clm_gutter_dragging");
        const viewport = gutter.querySelector("[data-clm-pdf-gutter-viewport='1']");
        viewport?.classList.add("is-dragging");
        ev.preventDefault();

        window.addEventListener("pointermove", _onPointerMove, {passive: false});
        window.addEventListener("pointerup", _onPointerUp);
        window.addEventListener("pointercancel", _onPointerUp);
        window.addEventListener("touchmove", _onPointerMove, {passive: false});
        window.addEventListener("touchend", _onPointerUp);
    };

    if (gutter && !gutter.dataset.clmGutterDragBound) {
        gutter.dataset.clmGutterDragBound = "1";
        gutter.addEventListener("pointerdown", _onGutterPointerDown);
        gutter.addEventListener("touchstart", _onGutterPointerDown, {passive: false});
    }

    const wire = () => {
        const leftIframe = _paneIframe(leftPane);
        const rightIframe = _paneIframe(rightPane);
        _hidePdfIframeScrollbars(leftIframe);
        _hidePdfIframeScrollbars(rightIframe);
        const nextLeft = _pdfScrollContainer(leftIframe);
        const nextRight = _pdfScrollContainer(rightIframe);
        if (nextLeft && nextLeft !== leftScroll) {
            leftScroll = nextLeft;
            leftScroll.addEventListener(
                "scroll",
                () => syncFrom(leftScroll, rightScroll),
                {passive: true},
            );
        }
        if (nextRight && nextRight !== rightScroll) {
            rightScroll = nextRight;
            rightScroll.addEventListener(
                "scroll",
                () => syncFrom(rightScroll, leftScroll),
                {passive: true},
            );
        }
        if (leftScroll) {
            _syncGutter(leftScroll);
        }
    };

    wire();
    // PDF.js boots asynchronously inside iframes.
    row.querySelectorAll(".o_clm_pdf_iframe").forEach((iframe) => {
        iframe.addEventListener("load", () => {
            window.setTimeout(wire, 100);
            window.setTimeout(wire, 500);
            window.setTimeout(wire, 1200);
        });
    });
    window.setTimeout(wire, 300);
    window.setTimeout(wire, 1000);
    window.addEventListener("resize", () => {
        if (leftScroll) {
            _syncGutter(leftScroll);
        }
    });
}

function scan(root = document) {
    root.querySelectorAll?.('[data-clm-pdf-compare="1"]').forEach(_bindCompareRow);
}

export function startClmPdfCompareSync() {
    scan(document);
    const observer = new MutationObserver((mutations) => {
        for (const mutation of mutations) {
            for (const node of mutation.addedNodes) {
                if (!(node instanceof HTMLElement)) {
                    continue;
                }
                if (node.matches?.('[data-clm-pdf-compare="1"]')) {
                    _bindCompareRow(node);
                } else if (node.querySelector?.('[data-clm-pdf-compare="1"]')) {
                    scan(node);
                }
            }
        }
    });
    observer.observe(document.documentElement, {childList: true, subtree: true});
}

registry.category("services").add("clm_pdf_compare_sync", {
    start() {
        startClmPdfCompareSync();
        return {};
    },
});
