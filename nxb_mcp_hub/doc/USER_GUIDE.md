# Multi-Database MCP Server – AI Connector for Odoo

**User guide · Version 18.0.1.0.0 · Prepared by Nextbridge**

Website: https://www.nextbridge.pk · Support: samiullah.3277@nxb.com.pk · License: OPL-1

---

## 1. What the app does

The app turns Odoo into an MCP server (Model Context Protocol, the open standard AI assistants use to call tools).
An AI app connects to one address and gets a set of tools to read and change Odoo data, build dashboards, work in
the CRM, prepare email campaigns, manage access restrictions and improve the website.

One connector can reach **several databases**:

| Kind | How it is reached | Tools available |
|---|---|---|
| This database | directly | every pack that is switched on |
| Another database on this server | opened by the hub, authenticated with an Odoo API key of that database | every pack when this app is installed there, otherwise data tools |
| A database on another Odoo server | Odoo's external API (XML-RPC) with an API key | data tools; every pack when that server runs this app too ("remote connector") |

The assistant always acts as a real Odoo user, so Odoo's access rights, record rules and multi-company rules apply.

## 2. Installation

1. Copy the `nxb_mcp_hub` folder into your addons path, or install it from the Odoo App Store.
2. Update the apps list and install **Multi-Database MCP Server – AI Connector for Odoo**.
3. A new **AI Connector** app appears in the main menu (administrators and members of the *AI Connector / User* group).

Requirements: Odoo 18, no extra Python packages. The app depends only on `base` and `web`. The CRM, Email campaigns
and Website tools become available when the CRM, Email Marketing and Website apps are installed.

### 2.1 Servers hosting several databases

If each database has its own domain (`dbfilter`), nothing else is needed: the address is `https://your-domain/mcp`.

If one server hosts several databases on the same domain, load the app server-wide so every database is reachable
at `/mcp/db/<database name>`. In the Odoo configuration file:

```
server_wide_modules = base,web,nxb_mcp_hub
```

and restart Odoo. The app shows the correct address automatically on the Overview page, in Settings and in the key
wizard. Databases hidden by the server's `dbfilter` are never reachable through the connector.

### 2.2 Reverse proxy

The endpoint is a normal HTTP endpoint (JSON responses, no streaming connection), so no special proxy settings are
needed. If you use a proxy, make sure `web.base.url` is the public address (it is used in the addresses shown to
users) and that the `Authorization` header is forwarded.

## 3. Connecting an AI app

### 3.1 With a connector key

1. Open **AI Connector → Connect → New connector key**.
2. Choose the user the assistant acts as, read-only or read/write, optionally the databases it may use and an expiry date.
3. Click **Create key** and copy what you need. The key is shown once; only a hash is stored.

The wizard gives three ways to use the key:

- **Header** (desktop apps, IDEs, agents): address `https://your-domain/mcp` and header `Authorization: Bearer nxbm_…`.
- **URL with key** for apps that only accept an address: `https://your-domain/mcp/key/nxbm_…`. Treat this URL like a password.
- **JSON configuration** for desktop apps that start local servers; it uses `npx mcp-remote` to bridge to the address.

### 3.2 With OAuth sign-in (no key)

Apps that support MCP authorization can connect with the plain address. On first use they register themselves,
open the Odoo consent page in the browser, the user signs in and chooses *Read and change data* or *Read only*.
Tokens are short-lived and refreshed automatically; see and revoke them in **Activity → Sign-ins** (registered apps are in **Configuration → Signed-in apps**). OAuth and
self-registration can be switched off in Settings.

### 3.3 First questions to try

- "Which databases can you reach, and what can you do in each?"
- "How many open opportunities does each database have?"
- "Build a dashboard with this month's revenue, open pipeline and revenue per salesperson."
- "Find customers without email address in Branch B."

## 4. Databases

**Connect → Databases** lists the databases the assistant can reach. *This database* is created automatically.

To add another database:

| Field | Meaning |
|---|---|
| Name | shown to the assistant, e.g. "Spain company" |
| Code | short identifier used in the `database` argument, e.g. `spain` |
| Kind | another database on this server, or a database on another Odoo server |
| Database name | the technical database name |
| Server URL | other servers only, e.g. `https://erp.example.com` |
| User login + Odoo API key | the user the assistant acts as there; create the key in that database: user menu → My Preferences → Account Security → New API Key |
| Remote connector URL + key | optional, other servers running this app: every pack then works on that database too |
| Allowed users | leave empty for everyone with a connector key, or restrict |
| Note for the assistant | short context, e.g. "Spanish subsidiary, prices in EUR" |

Click **Test connection** to check it. The assistant sees a `database` choice on every tool and can use
`hub_search_all` to run the same search or count in every database.

## 5. Tool packs

**Connect → Tool packs** switches packs on and off. The page shows how many tools are on and an estimate of the
tool descriptions sent with every request: switching off unused packs makes answers faster and cheaper.

Defaults: Databases, Read data, Create & change data and Dashboards are on; the others are off until an
administrator switches them on. A pack that needs an app (CRM, Email Marketing, Website) only works in databases
where that app is installed.

## 6. Features by pack

### 6.1 Dashboards
The assistant creates dashboards made of KPI numbers, bar, line and pie charts, tables and record lists. Each widget
has a model, an optional filter, a measure (count, sum, average, minimum, maximum), an optional group-by and date
period. Dashboards are private to their owner unless shared. Open them in **AI workspace → Dashboards**; the
**Open dashboard** button shows the charts, and the assistant sends a direct link. Viewers only see figures for
records they may access.

### 6.2 CRM & prospecting
Salespeople write a **brief** (AI workspace → Briefs): goal, target audience, offer, exclusions, number of leads and
tags. The assistant reads the newest ready brief, adds leads (skipping duplicates by email or company website
domain; common free-mail domains are ignored), and reports back on the brief. It can also summarise the pipeline,
find leads, move stages, schedule follow-ups and mark leads won or lost.

### 6.3 Email campaigns
From a campaign brief the assistant creates mailing lists, adds contacts, drafts a campaign from building blocks
(headings, text, buttons, images, product cards, dividers) and schedules it. Sending always needs an explicit
confirmation and only draft campaigns can be changed.

### 6.4 Access control
Administrators can ask the assistant to:
hide menus or show only some apps, hide fields or make them read-only, hide buttons, tabs or other view elements,
hide the chatter, block create/edit/delete (or read) on a model, and add record rules for groups.

Every restriction is listed in **AI workspace → Access restrictions**, where it can be switched off or deleted.
Restrictions never apply to administrators, cannot target the administrator group, and the assistant cannot grant
administrator rights.

### 6.5 Website SEO and website builder
First allow a website in **AI workspace → Website permissions**: all pages or listed pages, and separately SEO
changes, editing, creating pages, deleting pages and site-wide changes (menus, theme, robots.txt, custom code).

The SEO tools audit pages and fix meta titles and descriptions, URLs (with automatic 301 redirects), headings, alt
texts, indexing, redirects, robots.txt and the sitemap.

The builder creates pages from Odoo's own building blocks, edits texts, links and images, moves, duplicates and
styles sections, changes menus, colours and fonts. Every content change is saved in **Activity → Website change
history** and can be undone. Expert mode adds HTML sections (scripts and event handlers are removed), custom CSS,
head/footer code and importing a public web page (only for pages you own or may reuse).

### 6.6 SQL & schema
Administrators only: list tables, describe a table and run a single read-only `SELECT` with a 15-second time limit.
Writing statements, multiple statements and file/system functions are refused; the query runs in a read-only
transaction.

## 7. Settings

**Configuration → Settings → AI Connector**:

| Setting | Effect |
|---|---|
| Read-only for everyone | no assistant may create, change or delete, whatever key it uses |
| Allow deleting records | when off, delete tools are hidden |
| Maximum records per answer | cap on search results (default 500) |
| Calls per minute per key | rate limit (default 120, 0 = unlimited) |
| Allow OAuth sign-in / self-registration | sign-in for apps without a key |
| Log arguments, keep activity for (days) | activity log detail and retention (default 90 days) |

## 8. Activity and security

**Activity → Tool calls** records every call: time, user, key or signed-in app, tool, database, status, duration
and error; arguments are stored with secrets masked. **Activity → Connected apps** shows the MCP sessions of AI apps.

Good practice:
- give each person or app its own key, read-only where possible, with an expiry date;
- act as a user with the rights the assistant needs, not as the administrator;
- keep Access control, Website expert mode and SQL off unless needed;
- revoke keys and signed-in apps that are no longer used.

## 9. Troubleshooting

| Symptom | Cause and fix |
|---|---|
| 401 Authentication required | missing, revoked or expired key; create a new key |
| 404 on `/mcp/db/<name>` | the database does not exist, is hidden by `dbfilter`, or the app is not loaded server-wide (§2.1) |
| 400 "Several databases are hosted here" | use `/mcp/db/<name>` (§2.1) |
| "feature is switched off" | switch the pack on in Tool packs |
| "not installed in this database" | install CRM / Email Marketing / Website there, or the app itself for local databases |
| "Rate limit reached" | wait a minute or raise the limit in Settings |
| Remote database: "Access denied" | check login and Odoo API key of that database; use *Test connection* |
| Website: "not allowed" | add or extend the website permission |

## 10. Uninstalling

Uninstalling removes the app's tables, menus, keys and logs. All access restrictions, including the record rules
created by the assistant, are removed first, so nothing keeps applying afterwards. Website content, theme changes
and data created by the assistant (records, dashboards' source data, pages) stay, as they are ordinary Odoo data.

## 11. Support

Nextbridge · https://www.nextbridge.pk · samiullah.3277@nxb.com.pk

## Appendix – Tool reference

Tools marked *changes data* are hidden for read-only keys and in read-only mode; *needs confirmation* tools require `confirm=true`; *admin* tools are only for Odoo administrators; *remote* tools also work on databases of other servers without the app. Every tool except the Databases pack accepts an optional `database` argument.

### Databases & discovery (4 tools, on by default)

List connected databases, explain what is available, search several databases at once.

| Tool | What it does | Notes |
|---|---|---|
| `hub_whoami` | Return the Odoo user, database, access level and the tool packs that are switched on. Call this first if you are unsure what you may do. | remote |
| `hub_databases` | List every Odoo database you can work with, with its code (use it as the 'database' argument of the other tools), what it is, and whether all tool packs or only basic record tools work there. | remote |
| `hub_capabilities` | Explain which features are available in a database: switched-on tool packs, installed business apps (Sales, CRM, Invoicing, Inventory, Website, ...) and any limits. Use it to answer 'what can you do?'. | remote |
| `hub_search_all` | Run the same search on several connected databases at once and return the results per database. Ideal for comparisons such as 'open invoices in every company database'. | remote |

### Read data (9 tools, on by default)

Find, read, count and summarise records of any model the user may see.

| Tool | What it does | Notes |
|---|---|---|
| `hub_installed_apps` | List the business applications installed in a database (technical name and title). | remote |
| `data_models` | Find the technical model name for a business object (e.g. 'invoice' → account.move, 'customer' → res.partner). Searches model names and descriptions. | remote |
| `data_fields` | List the fields of a model with type, label, whether they are required/read-only, the related model for links and the allowed values for selections. Use before creating or filtering records. | remote |
| `data_search` | Search records of any model with an Odoo domain and return the chosen fields plus the total number of matches. Relations come back as [id, name]. Use small limits and page with offset. | remote |
| `data_count` | Count the records matching a domain. | remote |
| `data_read` | Read specific records by id with the chosen fields (all readable fields if none are given). | remote |
| `data_lookup` | Find records by (part of) their name, the way Odoo's search boxes do. Returns [id, name] pairs. Use it to get the id of a customer, product, user, etc. before creating or updating linked records. | remote |
| `data_summarise` | Aggregate records: totals, averages, counts grouped by fields or by date periods. Examples: revenue per month (group_by ['date_order:month'], measures ['amount_total:sum']), leads per stage. | remote |
| `data_companies` | List the companies the user can work in (multi-company databases). | remote |

### Create & change data (5 tools, on by default)

Create, update and delete records and run model actions, within the user's rights.

| Tool | What it does | Notes |
|---|---|---|
| `data_create` | Create one or more records. For links use ids (many2one: 7; many2many/one2many: [[6, 0, [ids]]] or [[0, 0, {values}]] to create lines). Check required fields with the field tool first. | changes data, remote |
| `data_update` | Change field values on existing records. | changes data, remote |
| `data_delete` | Permanently delete records. Prefer archiving (set active=false) when the model supports it. You must first tell the user exactly what will be deleted, then call with confirm=true. | changes data, needs confirmation, remote |
| `data_run_action` | Call a public method of a model, e.g. action_confirm on sale.order, action_post on account.move, button_validate on stock.picking. Private methods (starting with _) are not allowed. | changes data, remote |
| `data_add_note` | Post an internal note (default) or a message to followers in the chatter of a record. | changes data, remote |

### Dashboards (9 tools, on by default)

Let the assistant build KPI and chart dashboards that open inside Odoo.

| Tool | What it does | Notes |
|---|---|---|
| `dashboard_guide` | Explain the widget types, options and good practice for building dashboards. Read this once before creating a dashboard. |  |
| `dashboard_list` | List the dashboards you can see, with their widgets. |  |
| `dashboard_show` | Return a dashboard's definition and its current figures, so you can check the result or answer questions from it. |  |
| `dashboard_create` | Create a dashboard with its widgets in one call. Returns the link to open it in Odoo and the computed figures, so you can verify every widget works. | changes data |
| `dashboard_edit` | Change a dashboard's title, description or sharing. | changes data |
| `dashboard_widget_add` | Add a widget to an existing dashboard (at the end, or at a position). | changes data |
| `dashboard_widget_edit` | Change one or more settings of a widget (same keys as when creating it). | changes data |
| `dashboard_widget_remove` | Remove a widget from its dashboard. | changes data |
| `dashboard_delete` | Delete a whole dashboard. Tell the user which one first, then call with confirm=true. | changes data, needs confirmation |

### CRM & prospecting (8 tools, off by default)

Work with leads and opportunities, guided by prospecting briefs. Needs the `crm` app.

| Tool | What it does | Notes |
|---|---|---|
| `crm_brief_read` | Read a prospecting brief written by a salesperson: goal, target audience, offer, exclusions, how many leads, tags to use. Without an id, returns the newest brief that is ready. |  |
| `crm_brief_done` | Mark a brief as done and store a short summary of what you did, for the salesperson. | changes data |
| `crm_pipeline` | Summarise the CRM pipeline: number of opportunities and expected revenue per stage, and per salesperson. |  |
| `crm_lead_find` | Find leads and opportunities by text (name, company, contact, email), stage, salesperson, tag or type. |  |
| `crm_lead_add` | Create a lead or opportunity. Skips duplicates: if a lead with the same email or website domain exists, it is returned instead of creating a new one. Tags are created when missing. | changes data |
| `crm_lead_change` | Update a lead or opportunity: stage, salesperson, revenue, probability, contact details, add tags, or convert a lead into an opportunity. | changes data |
| `crm_lead_followup` | Add an internal note to a lead and/or schedule a follow-up activity (call, email, meeting, to-do). | changes data |
| `crm_lead_close` | Mark an opportunity as won, or as lost with a reason. | changes data |

### Email campaigns (11 tools, off by default)

Build mailing lists, contacts and draft email campaigns from campaign briefs. Needs the `mass_mailing` app.

| Tool | What it does | Notes |
|---|---|---|
| `mail_guide` | Guidance for good email campaigns and the block types the campaign tools accept. |  |
| `mail_brief_read` | Read an email campaign brief: goal, audience, message, products to feature, call to action, link and tone. Without an id, returns the newest brief that is ready. |  |
| `mail_lists` | List mailing lists with their number of contacts. |  |
| `mail_list_create` | Create a mailing list (returns the existing one if the name is taken). | changes data |
| `mail_contact_add` | Add contacts to one or more mailing lists. Existing contacts (same email) are reused and just subscribed. | changes data |
| `mail_contact_find` | Find mailing contacts by name, email or company, optionally in one list. |  |
| `mail_campaign_draft` | Create an email campaign as a DRAFT for the given mailing lists, from content blocks (or ready HTML). Nothing is sent. Returns the id and a link for the user to review. | changes data |
| `mail_campaign_update` | Change the subject, preview, lists or content of a campaign that is still a draft. | changes data |
| `mail_campaign_find` | List email campaigns with status and results (sent, opened, clicked, replied, bounced). |  |
| `mail_campaign_send` | Send a draft campaign now, or schedule it. Emails cannot be recalled: confirm with the user first and pass confirm=true. | changes data |
| `mail_brief_done` | Mark a campaign brief as done and store a short summary of what you prepared. | changes data |

### Access control (17 tools, off by default)

Hide menus, fields, buttons and chatter, block models and add record rules.

| Tool | What it does | Notes |
|---|---|---|
| `access_users_find` | Find Odoo users by name or login and show their groups. |  |
| `access_groups_list` | List user groups (roles), optionally only one application, with the number of users. |  |
| `access_user_summary` | Explain what a user can do: groups, restrictions from this app, record rules from their groups, and whether they can read/create/edit/delete a given model. |  |
| `access_user_create` | Create an internal user with a login and optional groups. The user sets a password via the invitation email (if outgoing mail is configured) or an administrator sets it in Odoo. | changes data |
| `access_user_groups` | Add groups to a user or remove groups from them. | changes data |
| `access_menus_find` | Find menus by name and return their full path and id (use the ids in the menu rules). |  |
| `access_menus_hide` | Hide menus (and everything under them) for some users or groups. | changes data |
| `access_menus_only` | Limit users or groups to the given menus/apps: every other menu disappears for them. | changes data |
| `access_fields_restrict` | Hide fields, or make them read-only, in the screens of a model for some users or groups. This changes the interface; for data security also use a model block or record rule. | changes data |
| `access_view_elements` | List the buttons, tabs, smart buttons and fields in a model's form or list screen, with the selector to use in access_elements_hide. |  |
| `access_elements_hide` | Hide buttons, tabs or other elements of a model's screens for some users or groups. | changes data |
| `access_chatter_hide` | Hide the message/activity panel (chatter) of a model's form for some users or groups. | changes data |
| `access_model_block` | Forbid reading, editing, creating and/or deleting records of a model for some users or groups. Enforced everywhere (screens, imports, API), on top of Odoo's normal rights. | changes data |
| `access_record_rule_add` | Limit which records of a model some groups can see or change, with an Odoo domain. Example: salespeople see only their own customers: model res.partner, domain [["user_id", "=", "user.id"]]. Use the string 'user.id' or 'company_ids' for the current user's values. | changes data |
| `access_rules_list` | List the access restrictions created with this app (menus, fields, elements, chatter, model blocks, record rules), optionally for one model or user. |  |
| `access_rule_switch` | Temporarily disable or re-enable a restriction without deleting it. | changes data |
| `access_rule_remove` | Delete a restriction created with this app (and its record rule, if any). | changes data |

### Website SEO (16 tools, off by default)

Audit the website and fix titles, descriptions, URLs, redirects, indexing and alt text. Needs the `website` app.

| Tool | What it does | Notes |
|---|---|---|
| `seo_guide` | Rules of thumb for titles, descriptions, URLs, headings and alt texts, and how the SEO tools work. |  |
| `seo_audit` | Check the website's pages for SEO problems (titles, descriptions, headings, alt texts, indexing, URLs, thin content) and rank pages by how much they need work. |  |
| `seo_page` | Show one page's SEO data: meta title and description, keywords, headings, images with alt texts, word count, indexing, and its issues. |  |
| `seo_meta_set` | Set the meta title, meta description, keywords and/or social sharing image of a page. | changes data |
| `seo_meta_bulk` | Set meta titles and descriptions on many pages in one call. | changes data |
| `seo_url_set` | Give a page a new address. By default a permanent (301) redirect from the old address is created so links and rankings are kept. | changes data |
| `seo_redirects` | List the website's URL redirects. |  |
| `seo_redirect_add` | Redirect an old address to a new one (301 permanent, 302 temporary) or mark it as gone (404). | changes data |
| `seo_redirect_remove` | Delete a redirect by id. | changes data |
| `seo_indexing` | Allow or prevent search engines from indexing a page. | changes data |
| `seo_alt_texts` | Set the alt text of images on a page. Identify images by their address (src) as listed by seo_page. | changes data |
| `seo_headings_fix` | Make a page have exactly one H1: keep the first H1 (or promote the first H2 when there is none) and turn other H1s into H2s. Optionally set the H1 text. | changes data |
| `seo_robots` | Show the website's robots.txt, or replace it (site-wide permission needed). | changes data |
| `seo_sitemap_refresh` | Clear the cached sitemap so search engines get an up-to-date list of pages on their next visit. | changes data |
| `seo_site_settings` | Read or change site-wide SEO settings: website name, Google Analytics key and the default social sharing image (site-wide permission needed to change). | changes data |
| `seo_undo` | Restore a page to the version before a change made by the SEO tools (alt texts, headings). | changes data |

### Website builder (22 tools, off by default)

Edit website pages with Odoo's own building blocks, texts, images, theme and menus. Needs the `website` app.

| Tool | What it does | Notes |
|---|---|---|
| `site_guide` | Explain how the website builder tools work, the permission model and good practice. Read it before your first website change. |  |
| `site_overview` | List the websites with their domain, languages, number of pages, menu, theme and exactly what the assistant is allowed to change on each. |  |
| `site_pages` | List the pages of a website with address, title, published and indexed status. |  |
| `site_page_structure` | Show the sections of a page in order, with their block type, texts, buttons, links and images, and the element paths used by the editing tools. |  |
| `site_blocks` | List Odoo's website building blocks (banners, text-image, columns, pricing, team, FAQ, call to action, ...) that can be added to pages. |  |
| `site_history` | List the recent changes made by the assistant on a page, with the undo ids. |  |
| `site_page_create` | Create a new website page (unpublished unless asked), optionally with Odoo building blocks and a menu entry. | changes data |
| `site_page_update` | Change a page's name, publication, header/footer visibility, or add it to / remove it from the menu. | changes data |
| `site_page_delete` | Delete a website page permanently (cannot be undone). Tell the user first, then call with confirm=true. | changes data, needs confirmation |
| `site_block_add` | Add an Odoo building block (see site_blocks) to a page, at the end or after a given section. | changes data |
| `site_block_move` | Move a section to another position on the page. | changes data |
| `site_block_remove` | Remove a section from a page (can be undone with site_undo). | changes data |
| `site_block_duplicate` | Copy a section and insert the copy right after it. | changes data |
| `site_text_set` | Replace the text of a heading, paragraph, button or other text element. Use blank lines in the text to create several paragraphs when the element is a paragraph. | changes data |
| `site_link_set` | Change where a button or link points to, and optionally its label. | changes data |
| `site_image_set` | Replace an image on a page with an image from an address (downloaded into Odoo) or one already uploaded (/web/image/...), and set its alt text. | changes data |
| `site_image_upload` | Download an image from an address into Odoo's media library and return its local address, ready for site_image_set or HTML. | changes data |
| `site_section_style` | Change a section's look with the theme's own styles: background colour scheme, vertical spacing, text alignment, and extra CSS classes. | changes data |
| `site_undo` | Restore a page to the version before one of the assistant's changes (use the undo_id returned by the change, or see site_history). The undo itself can be undone with the returned redo_id. | changes data |
| `site_menu` | Show the website's menu tree with ids, labels and addresses. |  |
| `site_menu_change` | Add, rename, re-point, move or remove a website menu entry (site-wide permission needed). | changes data |
| `site_theme` | Change the website theme: a preset colour palette, the five main colours, and the body/heading/button fonts (site-wide permission needed; cannot be undone automatically — the result reports the previous values). | changes data |

### Website builder – expert mode (5 tools, off by default)

Raw HTML sections, custom CSS, head/footer code and copying an external page. Needs the `website` app.

| Tool | What it does | Notes |
|---|---|---|
| `site_section_html` | Replace a section with your own HTML, or insert a new HTML section. Scripts and event handlers are removed. Such sections display correctly but have fewer options in Odoo's website editor — tell the user so. | changes data |
| `site_custom_css` | Read the website's custom CSS, append rules to it or replace it (site-wide permission needed). The previous CSS is returned so it can be restored. | changes data |
| `site_custom_code` | Read or set code added to every page's <head> or end of <body> (e.g. analytics or chat widgets). Site-wide permission needed. Returns the previous code. | changes data |
| `site_web_preview` | Fetch a public web page and summarise it: title, headings, main texts, images and links. Use it for inspiration or before copying a page. |  |
| `site_web_import` | Copy the main content of a public web page into a NEW unpublished page of this website, split into sections by heading. Only copy pages the user owns or has the rights to reuse — remind the user of this. | changes data |

### SQL & schema (3 tools, off by default)

Read-only SQL queries and table information for administrators.

| Tool | What it does | Notes |
|---|---|---|
| `sql_tables` | List PostgreSQL tables of the database with their approximate row count (administrators only). | admin |
| `sql_table_info` | Columns, types, nullability and foreign keys of a PostgreSQL table, plus its exact row count. | admin |
| `sql_query` | Run one SELECT (or WITH ... SELECT) query. The database enforces read-only: anything that would change data fails. 15 second time limit. Prefer the record tools; they respect access rights, SQL does not. | admin |

