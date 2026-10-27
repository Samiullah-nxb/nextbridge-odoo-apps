# Multi-Database MCP Server – AI Connector for Odoo

Odoo 18 app by **Nextbridge** (https://www.nextbridge.pk) · License OPL-1 · Support: samiullah.3277@nxb.com.pk

An MCP (Model Context Protocol) server that lives inside Odoo. AI assistants connect to one address and can work
in this database, in other databases on the same server and in databases on other Odoo servers.

- 109 tools in 11 packs that can be switched on and off: databases, read data, change data, dashboards, CRM,
  email campaigns, access control, website SEO, website builder (standard and expert) and read-only SQL.
- Connector keys (hashed, read-only or read/write, per-database limits, expiry) and OAuth 2.1 sign-in with PKCE.
- Read-only mode, delete switch, record and rate limits, complete activity log.
- Depends only on `base` and `web`.

Technical name: `nxb_mcp_hub`. Endpoint: `/mcp` (or `/mcp/db/<database>` on multi-database servers).

See `doc/USER_GUIDE.md` for installation, configuration and every tool.
