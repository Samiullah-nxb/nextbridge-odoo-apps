/** @odoo-module **/

/**
 * Fullscreen for the Original | Revised PDF comparison section.
 *
 * Uses the browser Fullscreen API when available, otherwise a fixed-position
 * CSS fallback (some browsers / embedded frames block requestFullscreen).
 *
 * Also keeps the PDF.js zoom % dropdown visible in narrow side-by-side panes
 * (PDF.js hides #scaleSelectContainer below 560px).
 */

import { registry } from "@web/core/registry";

const FS_CLASS = "o_clm_compare_fs_active";
const ZOOM_STYLE_ID = "o_clm_compare_pdfjs_zoom_dropdown";
const ZOOM_STYLE_CSS = `
/* Side-by-side panes are often < 560px; PDF.js would hide the zoom dropdown. */
@media all and (max-width: 560px) {
  #scaleSelectContainer {
    display: inline-block !important;
    min-width: 4.75rem;
    max-width: 6.5rem;
  }
  #scaleSelect {
    max-width: 6.5rem;
    font-size: 11px !important;
  }
}
`;
const _zoomWired = new WeakSet();

function _requestFullscreen(el) {
    if (!el) {
        return Promise.reject(new Error("Missing fullscreen target"));
    }
    if (el.requestFullscreen) {
        return el.requestFullscreen();
    }
    if (el.webkitRequestFullscreen) {
        return Promise.resolve(el.webkitRequestFullscreen());
    }
    if (el.msRequestFullscreen) {
        return Promise.resolve(el.msRequestFullscreen());
    }
    return Promise.reject(new Error("Fullscreen API unavailable"));
}

function _exitFullscreen() {
    if (document.exitFullscreen) {
        return document.exitFullscreen();
    }
    if (document.webkitExitFullscreen) {
        return Promise.resolve(document.webkitExitFullscreen());
    }
    if (document.msExitFullscreen) {
        return Promise.resolve(document.msExitFullscreen());
    }
    return Promise.resolve();
}

function _fullscreenElement() {
    return (
        document.fullscreenElement ||
        document.webkitFullscreenElement ||
        document.msFullscreenElement ||
        null
    );
}

function _compareSection(fromEl) {
    if (fromEl?.closest) {
        const section = fromEl.closest(".o_clm_pdf_compare_section");
        if (section) {
            return section;
        }
    }
    return document.querySelector(
        ".o_clm_comparison_form .o_clm_pdf_compare_section, .o_clm_pdf_compare_section",
    );
}

function _isCompareFullscreen(section) {
    if (!section) {
        return false;
    }
    return _fullscreenElement() === section || section.classList.contains(FS_CLASS);
}

function _enterCssFullscreen(section) {
    document.querySelectorAll(`.${FS_CLASS}`).forEach((el) => {
        el.classList.remove(FS_CLASS);
    });
    section.classList.add(FS_CLASS);
    document.body.classList.add("o_clm_compare_fs_open");
}

function _exitCssFullscreen() {
    document.querySelectorAll(`.${FS_CLASS}`).forEach((el) => {
        el.classList.remove(FS_CLASS);
    });
    document.body.classList.remove("o_clm_compare_fs_open");
}

function _syncButtons() {
    const activeNative = _fullscreenElement();
    document.querySelectorAll(".o_clm_compare_fullscreen_btn").forEach((btn) => {
        const section = _compareSection(btn);
        const isFs = Boolean(
            section &&
                (activeNative === section || section.classList.contains(FS_CLASS)),
        );
        btn.classList.toggle("active", isFs);
        btn.setAttribute("aria-pressed", isFs ? "true" : "false");
        const label = btn.querySelector(".o_clm_compare_fullscreen_label");
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

function _refitPdfIframes(section) {
    // Re-apply page-width zoom after the comparison pane size changes.
    section?.querySelectorAll?.(".o_clm_pdf_iframe").forEach((iframe) => {
        try {
            const app = iframe.contentWindow?.PDFViewerApplication;
            const viewer = app?.pdfViewer;
            if (viewer) {
                viewer.currentScaleValue = "page-width";
            }
            iframe.contentWindow?.dispatchEvent(new Event("resize"));
        } catch (_err) {
            // Cross-document access can fail briefly while PDF.js boots.
        }
    });
    try {
        window.dispatchEvent(new Event("resize"));
    } catch (_err) {
        // ignore
    }
}

async function _toggleCompareFullscreen(btn) {
    const section = _compareSection(btn);
    if (!section) {
        return;
    }
    if (_isCompareFullscreen(section)) {
        _exitCssFullscreen();
        if (_fullscreenElement() === section) {
            try {
                await _exitFullscreen();
            } catch (_err) {
                // ignore
            }
        }
        _syncButtons();
        window.setTimeout(() => _refitPdfIframes(section), 50);
        return;
    }
    try {
        await _requestFullscreen(section);
    } catch (_err) {
        _enterCssFullscreen(section);
    }
    _syncButtons();
    window.setTimeout(() => _refitPdfIframes(section), 50);
    window.setTimeout(() => _refitPdfIframes(section), 250);
}

function _onClick(ev) {
    const btn = ev.target.closest?.(".o_clm_compare_fullscreen_btn");
    if (!btn) {
        return;
    }
    ev.preventDefault();
    ev.stopPropagation();
    ev.stopImmediatePropagation?.();
    _toggleCompareFullscreen(btn);
}

function _onKeydown(ev) {
    if (ev.key !== "Escape") {
        return;
    }
    if (document.body.classList.contains("o_clm_compare_fs_open")) {
        _exitCssFullscreen();
        _syncButtons();
    }
}

function _injectZoomDropdownStyle(iframe) {
    try {
        const doc = iframe.contentDocument;
        if (!doc?.head || doc.getElementById(ZOOM_STYLE_ID)) {
            return Boolean(doc?.getElementById(ZOOM_STYLE_ID));
        }
        const style = doc.createElement("style");
        style.id = ZOOM_STYLE_ID;
        style.textContent = ZOOM_STYLE_CSS;
        doc.head.appendChild(style);
        return true;
    } catch (_err) {
        return false;
    }
}

function _wireComparePdfZoomDropdown(iframe) {
    if (!iframe || _zoomWired.has(iframe)) {
        return;
    }
    if (!iframe.closest?.(".o_clm_pdf_compare_section, .o_clm_comparison_form")) {
        return;
    }
    _zoomWired.add(iframe);

    const apply = () => {
        _injectZoomDropdownStyle(iframe);
        // Retry while PDF.js boots / replaces head content.
        window.setTimeout(() => _injectZoomDropdownStyle(iframe), 200);
        window.setTimeout(() => _injectZoomDropdownStyle(iframe), 800);
        window.setTimeout(() => _injectZoomDropdownStyle(iframe), 1600);
    };

    iframe.addEventListener("load", apply);
    try {
        if (iframe.contentDocument?.readyState === "complete") {
            apply();
        }
    } catch (_err) {
        // Wait for load.
    }
}

function _scanComparePdfIframes(root = document) {
    const scope = root?.querySelectorAll ? root : document;
    scope
        .querySelectorAll?.(
            ".o_clm_pdf_compare_section .o_clm_pdf_iframe, .o_clm_comparison_form .o_clm_pdf_iframe",
        )
        .forEach(_wireComparePdfZoomDropdown);
    if (root instanceof HTMLIFrameElement) {
        _wireComparePdfZoomDropdown(root);
    }
}

export function startClmCompareFullscreen() {
    // Capture phase so Odoo form handlers cannot swallow the click first.
    document.addEventListener("click", _onClick, true);
    document.addEventListener("keydown", _onKeydown, true);
    document.addEventListener("fullscreenchange", _syncButtons);
    document.addEventListener("webkitfullscreenchange", _syncButtons);
    document.addEventListener("MSFullscreenChange", _syncButtons);

    _scanComparePdfIframes(document);
    const observer = new MutationObserver((mutations) => {
        for (const mutation of mutations) {
            for (const node of mutation.addedNodes) {
                if (!(node instanceof HTMLElement)) {
                    continue;
                }
                if (
                    node.matches?.(".o_clm_pdf_iframe, .o_clm_pdf_compare_section, .o_clm_pdf_preview")
                ) {
                    _scanComparePdfIframes(node);
                } else if (node.querySelector?.(".o_clm_pdf_iframe")) {
                    _scanComparePdfIframes(node);
                }
            }
        }
    });
    observer.observe(document.documentElement, {childList: true, subtree: true});
}

registry.category("services").add("clm_compare_fullscreen", {
    start() {
        startClmCompareFullscreen();
        return {};
    },
});
