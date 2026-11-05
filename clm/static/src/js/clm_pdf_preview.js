/** @odoo-module **/

/**
 * Fullscreen + default zoom for native Contract PDF Preview (PDF.js iframe).
 *
 * PDF.js often ignores ``#zoom=…`` when localStorage history/preferences still
 * say Automatic Zoom. After the iframe loads we re-apply the intended scale.
 *
 * Comparison panes use ``page-width`` so zoom stays responsive to pane/screen size.
 */

const _ZOOM_100 = "1";
const _ZOOM_COMPARE = "page-width";
const _wiredIframes = new WeakSet();

function _isComparePdfIframe(iframe) {
    return Boolean(
        iframe?.closest?.(
            ".o_clm_pdf_compare_section, .o_clm_comparison_form, .o_clm_compare_pdf_pane",
        ),
    );
}

function _preferredZoom(iframe) {
    const root = iframe?.closest?.(".o_clm_pdf_preview");
    const fromData = root?.getAttribute?.("data-clm-pdf-zoom");
    if (fromData) {
        return String(fromData);
    }
    if (_isComparePdfIframe(iframe)) {
        return _ZOOM_COMPARE;
    }
    return _ZOOM_100;
}

function _requestFullscreen(el) {
    if (!el) {
        return;
    }
    if (el.requestFullscreen) {
        return el.requestFullscreen();
    }
    if (el.webkitRequestFullscreen) {
        return el.webkitRequestFullscreen();
    }
    if (el.msRequestFullscreen) {
        return el.msRequestFullscreen();
    }
}

function _exitFullscreen() {
    if (document.exitFullscreen) {
        return document.exitFullscreen();
    }
    if (document.webkitExitFullscreen) {
        return document.webkitExitFullscreen();
    }
    if (document.msExitFullscreen) {
        return document.msExitFullscreen();
    }
}

function _fullscreenElement() {
    return (
        document.fullscreenElement ||
        document.webkitFullscreenElement ||
        document.msFullscreenElement ||
        null
    );
}

function _syncFullscreenButtons() {
    const active = _fullscreenElement();
    document.querySelectorAll(".o_clm_pdf_preview").forEach((root) => {
        const frame = root.querySelector(".o_clm_pdf_viewer_frame");
        const btn = root.querySelector(".o_clm_pdf_fullscreen_btn");
        if (!btn || !frame) {
            return;
        }
        const isFs = active === frame;
        btn.classList.toggle("active", isFs);
        btn.setAttribute("aria-pressed", isFs ? "true" : "false");
        const label = btn.querySelector(".o_clm_pdf_fullscreen_label");
        if (label) {
            label.textContent = isFs ? "Exit Fullscreen" : "Fullscreen";
        }
        const icon = btn.querySelector("i");
        if (icon) {
            icon.classList.toggle("fa-compress", isFs);
            icon.classList.toggle("fa-expand", !isFs);
        }
    });
}

function _applyPreferredZoom(iframe) {
    try {
        const app = iframe.contentWindow && iframe.contentWindow.PDFViewerApplication;
        const viewer = app && app.pdfViewer;
        if (!viewer) {
            return false;
        }
        const wanted = _preferredZoom(iframe);
        if (String(viewer.currentScaleValue) !== String(wanted)) {
            viewer.currentScaleValue = wanted;
        }
        return Boolean(app.pdfDocument || app.initialized);
    } catch (_err) {
        return false;
    }
}

function _forcePreferredZoom(iframe) {
    if (!iframe || _wiredIframes.has(iframe)) {
        return;
    }
    _wiredIframes.add(iframe);

    const schedule = () => {
        _applyPreferredZoom(iframe);
        // Beat PDF.js history restore / unequal-page re-layout after open.
        window.setTimeout(() => _applyPreferredZoom(iframe), 200);
        window.setTimeout(() => _applyPreferredZoom(iframe), 800);
        window.setTimeout(() => _applyPreferredZoom(iframe), 1600);
    };

    const onLoad = () => {
        try {
            const win = iframe.contentWindow;
            const app = win && win.PDFViewerApplication;
            if (app && app.initializedPromise) {
                app.initializedPromise.then(() => {
                    schedule();
                    try {
                        app.eventBus.on("documentinit", () => _applyPreferredZoom(iframe));
                        app.eventBus.on("pagesloaded", () => _applyPreferredZoom(iframe));
                    } catch (_err) {
                        // eventBus may not be ready yet; timeouts still cover it.
                    }
                });
            } else {
                schedule();
            }
        } catch (_err) {
            schedule();
        }
    };

    iframe.addEventListener("load", onLoad);
    try {
        if (iframe.contentDocument && iframe.contentDocument.readyState === "complete") {
            onLoad();
        }
    } catch (_err) {
        // Ignore cross-document access until load fires.
    }
}

function _wirePdfPreviews(root) {
    const scope = root && root.querySelectorAll ? root : document;
    scope.querySelectorAll(".o_clm_pdf_iframe").forEach(_forcePreferredZoom);
}

function _refitComparePdfZooms() {
    document
        .querySelectorAll(
            ".o_clm_pdf_compare_section .o_clm_pdf_iframe, .o_clm_comparison_form .o_clm_pdf_iframe",
        )
        .forEach((iframe) => {
            _applyPreferredZoom(iframe);
            try {
                iframe.contentWindow?.dispatchEvent(new Event("resize"));
            } catch (_err) {
                // ignore
            }
        });
}

let _compareResizeTimer = null;
function _onCompareViewportChange() {
    window.clearTimeout(_compareResizeTimer);
    _compareResizeTimer = window.setTimeout(() => {
        _refitComparePdfZooms();
        // PDF.js layout settles after the container finishes resizing.
        window.setTimeout(_refitComparePdfZooms, 150);
    }, 80);
}

document.addEventListener("click", (ev) => {
    const btn = ev.target.closest(".o_clm_pdf_fullscreen_btn");
    if (!btn) {
        return;
    }
    ev.preventDefault();
    ev.stopPropagation();
    const root = btn.closest(".o_clm_pdf_preview");
    const frame = root && root.querySelector(".o_clm_pdf_viewer_frame");
    if (!frame) {
        return;
    }
    if (_fullscreenElement() === frame) {
        _exitFullscreen();
    } else {
        _requestFullscreen(frame);
    }
});

document.addEventListener("fullscreenchange", () => {
    _syncFullscreenButtons();
    _onCompareViewportChange();
});
document.addEventListener("webkitfullscreenchange", () => {
    _syncFullscreenButtons();
    _onCompareViewportChange();
});
document.addEventListener("MSFullscreenChange", () => {
    _syncFullscreenButtons();
    _onCompareViewportChange();
});
window.addEventListener("resize", _onCompareViewportChange);

_wirePdfPreviews(document);
const _observer = new MutationObserver((mutations) => {
    for (const mutation of mutations) {
        for (const node of mutation.addedNodes) {
            if (!(node instanceof HTMLElement)) {
                continue;
            }
            if (node.matches?.(".o_clm_pdf_iframe, .o_clm_pdf_preview")) {
                _wirePdfPreviews(node);
            } else if (node.querySelector?.(".o_clm_pdf_iframe")) {
                _wirePdfPreviews(node);
            }
        }
    }
});
_observer.observe(document.documentElement, {childList: true, subtree: true});
