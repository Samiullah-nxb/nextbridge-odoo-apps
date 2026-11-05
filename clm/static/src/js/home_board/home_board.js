/** @odoo-module **/

/**
 * NextContract Home Board — icon tile grid for in-app modules.
 * Multi-option modules expand in place with destinations; single tiles open directly.
 */

import { Component, onWillStart, useExternalListener, useRef, useState } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { standardActionServiceProps } from "@web/webclient/actions/action_service";

export class ClmHomeBoard extends Component {
    static template = "clm.HomeBoard";
    static props = { ...standardActionServiceProps };

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.rootRef = useRef("root");
        this.state = useState({
            tiles: [],
            loading: true,
            error: "",
            expandedId: null,
        });

        onWillStart(async () => {
            try {
                const tiles = await this.orm.call(
                    "res.users",
                    "clm_get_home_board_tiles",
                    [],
                );
                this.state.tiles = tiles || [];
            } catch (_err) {
                this.state.error = _t("Could not load NextContract modules.");
            } finally {
                this.state.loading = false;
            }
        });

        useExternalListener(window, "pointerdown", this.onOutsidePointerDown.bind(this));
    }

    isExpandable(tile) {
        return Array.isArray(tile?.children) && tile.children.length > 1;
    }

    isExpanded(tile) {
        return this.state.expandedId === tile?.id;
    }

    collapse() {
        this.state.expandedId = null;
    }

    onOutsidePointerDown(ev) {
        if (!this.state.expandedId) {
            return;
        }
        const root = this.rootRef.el;
        if (root && !root.contains(ev.target)) {
            this.collapse();
        }
    }

    onTileClick(tile) {
        if (!tile) {
            return;
        }
        if (this.isExpandable(tile)) {
            this.state.expandedId =
                this.state.expandedId === tile.id ? null : tile.id;
            return;
        }
        this.collapse();
        if (tile.action_id) {
            this.action.doAction(tile.action_id);
        }
    }

    onDestinationClick(ev, option) {
        ev.stopPropagation();
        if (!option?.action_id) {
            return;
        }
        this.collapse();
        this.action.doAction(option.action_id);
    }
}

registry.category("actions").add("clm_home_board", ClmHomeBoard);
