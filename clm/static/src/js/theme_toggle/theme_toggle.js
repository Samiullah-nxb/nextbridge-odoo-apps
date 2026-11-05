import { Component } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { user } from "@web/core/user";
import { browser } from "@web/core/browser/browser";

// A one-click light/dark switch in the systray, next to the user avatar.
// web_enterprise already gives every user a System / Light / Dark choice in
// Preferences, but that is three clicks and a dialog away. This button only
// ever toggles between the two concrete schemes — it never writes "system"
// back — so a user who picked "System" there still lands on an explicit
// light or dark choice the first time they use it here, same as clicking
// either radio in that dialog would.
export class ClmThemeToggleSystray extends Component {
    static template = "clm.ThemeToggleSystray";
    static props = {};

    setup() {
        this.colorScheme = useService("color_scheme");
        this.orm = useService("orm");
    }

    get isDark() {
        return this.colorScheme.currentColorScheme === "dark";
    }

    async toggle() {
        await this.orm.write("res.users", [user.userId], {
            color_scheme: this.isDark ? "light" : "dark",
        });
        // Mirrors what the Preferences dialog does after a save: the cookie
        // the server renders CSS from is only read at page load, so the new
        // scheme does not take effect without a reload. `reload` lives on the
        // color_scheme service descriptor itself, not on the object its
        // start() returns, so it is not reachable through useService() here.
        browser.location.reload();
    }
}

registry.category("systray").add(
    "clm.ThemeToggleSystray",
    { Component: ClmThemeToggleSystray },
    { sequence: 1 }
);
