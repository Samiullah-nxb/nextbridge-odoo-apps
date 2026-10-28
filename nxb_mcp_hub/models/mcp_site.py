# -*- coding: utf-8 -*-
# Copyright 2026 Nextbridge. License OPL-1.
"""Website permissions and page snapshots (undo). The Website app is optional: websites are
referenced by id so this module installs without it."""
from odoo import api, fields, models


class McpSitePermission(models.Model):
    _name = "nxb.mcp.site.permission"
    _description = "AI Connector: website permission"
    _rec_name = "website_key"

    @api.model
    def _website_selection(self):
        if "website" not in self.env:
            return []
        return [(str(w.id), w.name) for w in self.env["website"].sudo().search([])]

    website_key = fields.Selection("_website_selection", string="Website", required=True)
    active = fields.Boolean(default=True)
    scope = fields.Selection([("all", "All pages"), ("listed", "Only the pages listed below")], default="listed",
                             required=True)
    page_urls = fields.Text("Pages", help="One page address per line, e.g. / or /about-us. Pages created by the "
                                          "assistant are added automatically.")
    allow_seo = fields.Boolean("SEO changes", default=True,
                               help="Meta titles and descriptions, URLs and redirects, indexing, alt texts, headings.")
    allow_edit = fields.Boolean("Edit page content", default=True,
                                help="Blocks, texts, images and section styles of the allowed pages.")
    allow_create = fields.Boolean("Create pages")
    allow_delete = fields.Boolean("Delete pages")
    allow_sitewide = fields.Boolean("Site-wide changes",
                                    help="Theme colours and fonts, menus, robots.txt, website SEO settings and (in "
                                         "expert mode) custom CSS and head/footer code.")
    note = fields.Text()

    _website_uniq = models.Constraint("unique(website_key)", "This website already has permissions.")

    def _website(self):
        self.ensure_one()
        return self.env["website"].sudo().browse(int(self.website_key)).exists()

    def _urls(self):
        return [u.strip().rstrip("/") or "/" for u in (self.page_urls or "").splitlines() if u.strip()]

    def _page_allowed(self, url):
        self.ensure_one()
        if self.scope == "all":
            return True
        return ((url or "/").rstrip("/") or "/") in self._urls()

    def _add_url(self, url):
        if self.scope == "listed" and url not in self._urls():
            self.sudo().page_urls = "\n".join(self._urls() + [url])

    def _describe(self):
        self.ensure_one()
        w = self._website()
        return {"website_id": int(self.website_key), "website": w.name if w else "?",
                "pages": "all" if self.scope == "all" else self._urls(),
                "seo": self.allow_seo, "edit_content": self.allow_edit, "create_pages": self.allow_create,
                "delete_pages": self.allow_delete, "site_wide": self.allow_sitewide}


class McpSiteSnapshot(models.Model):
    _name = "nxb.mcp.site.snapshot"
    _description = "AI Connector: page snapshot"
    _order = "id desc"

    website_id_int = fields.Integer("Website id", index=True)
    view_id_int = fields.Integer("View id", index=True)
    page_url = fields.Char()
    label = fields.Char()
    arch = fields.Text()
    user_id = fields.Many2one("res.users", default=lambda s: s.env.user)
    restored = fields.Boolean()
