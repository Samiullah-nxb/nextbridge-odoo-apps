# Google Drive Attachment Storage

**User guide - Odoo 18 - Version 18.0.1.0.0 - Prepared by Nextbridge**

Website: https://www.nextbridge.pk - Support: samiullah.3277@nxb.com.pk - License: OPL-1

## 1. What the app does

The app sends the files attached to your Odoo documents to Google Drive and keeps them in step:

| In Odoo | On Google Drive |
|---|---|
| A file is attached (chatter, e-mail, report saved as attachment, import) | The file is uploaded into the right folder |
| The file content is replaced | The same Drive file gets the new content |
| The file is renamed | The Drive file is renamed |
| The file is attached to another record | The Drive file moves to that record's folder |
| The file (or its record) is deleted | The Drive file goes to the trash, is deleted, or is kept (your choice) |

Two storage modes:

- **Copy** - the file stays in Odoo and a copy is kept on Drive.
- **Move** - once uploaded, the file is removed from Odoo's storage. Odoo still shows, downloads, previews and
  e-mails it: it reads the file from Drive when needed, keeps recently used files in a local cache and checks that
  the content is exactly what was uploaded.

Uploads happen in the background (a scheduled action triggered right after each change), so users never wait for
Google and a cancelled operation never leaves Drive out of step.

## 2. Installation

1. Copy the `nxb_gdrive_attachments` folder into your addons path, or install the app from the Odoo App Store.
2. Update the apps list and install **Google Drive Attachment Storage**.
3. Open **Settings > Google Drive**.

No extra Python package is needed. The server must be able to reach `oauth2.googleapis.com` and
`www.googleapis.com` over HTTPS. The scheduled action "Google Drive: synchronise attachments" must be active (it is
by default) and Odoo must run its scheduler (default on most servers; `--max-cron-threads` greater than 0).

## 3. Connecting a Google account (OAuth)

1. Go to https://console.cloud.google.com, create (or choose) a project.
2. **APIs & Services > Library**: enable the **Google Drive API**.
3. **APIs & Services > OAuth consent screen**: configure it (Internal for Google Workspace, or External and add
   your Google account as a test user).
4. **APIs & Services > Credentials > Create credentials > OAuth client ID**, type **Web application**.
   Under *Authorized redirect URIs* add the address shown in Odoo, for example
   `https://erp.example.com/nxb_gdrive/oauth/callback`.
5. In Odoo, **Settings > Google Drive**: tick *Store attachments on Google Drive*, enter the **Client ID** and
   **Client secret**, click **Save**, then **Connect Google account**. Sign in to Google and allow access.
6. Back in Odoo the status shows *Connected* and the account. Click **Test connection**.

**Drive access**: *Only files created by Odoo* (recommended, the narrowest permission Google offers) lets Odoo
create and manage its own folder. Choose *Full Drive* only if you want Odoo to use a folder that already exists on
Drive (enter its ID in *Main folder ID*).

The web address used in the redirect URI is Odoo's `web.base.url` (Settings > Technical > System Parameters). It
must be the public HTTPS address of your Odoo.

## 4. Connecting with a service account (Google Workspace)

1. In Google Cloud, create a **service account** and a **JSON key**; enable the Google Drive API in its project.
2. Create a **Shared Drive** and add the service account's e-mail as a member (Content manager).
3. In Odoo choose *Connect with: Service account*, upload the JSON key and enter the **Shared Drive ID**
   (the last part of the Shared Drive's address).
4. Optional: with domain-wide delegation you can instead enter a user in *Act as user*.

Service accounts have no storage of their own, so use a Shared Drive (or impersonation).

## 5. Settings

| Setting | Meaning |
|---|---|
| Storage mode | Copy (files stay in Odoo too) or Move (files only on Drive) |
| Folder structure | One folder; one per document type (e.g. *Sales Order*); one per record (e.g. *Sales Order / S00016 [16]*) |
| Shared Drive ID | Put everything in this Shared Drive |
| Main folder ID | Leave empty: Odoo creates *Odoo - company name*. Changing it starts a new folder tree |
| When a file is deleted in Odoo | Move to Drive trash (default), delete permanently, or keep on Drive |
| Anyone with the link can view | Gives each uploaded file a public read link |
| Local cache (MB) | Size of the cache for files read back from Drive in Move mode |
| Document types | All documents, or only the selected ones |
| Minimum size (KB) | Smaller files stay in Odoo only |
| Images in fields | Product images, avatars, signatures (off by default: Odoo shows them constantly) |
| Public files | Website images and other public files (off by default) |

Settings are per company: in a multi-company database switch company in the top bar to configure each one.

Never sent to Drive: Odoo's technical files (views, assets, reports' templates, module data), links (URL
attachments), and files not yet attached to a record (they are sent as soon as they are attached, e.g. when an
e-mail is sent).

## 6. Day-to-day use

Nothing changes for users. Administrators find under **Settings > Google Drive**:

- **Files** - every attachment handled by the app with its status (*Waiting*, *On Google Drive*, *Error*), whether
  it is only on Drive, and its Drive link. Filters in the attachment search: *On Google Drive*, *Only on Google
  Drive*, *Waiting for Drive*, *Drive errors*.
- **Folders** - the Drive folders created by the app, with a link to each.
- **Waiting deletions** - Drive files still to be trashed or deleted.
- **Activity** - every upload, update, rename, move and deletion with its result (kept 90 days).

Each attachment's form shows **Open in Google Drive** and, for administrators, **Send to Drive now**.

### Existing attachments
Click **Send existing attachments** in the settings: every existing attachment that matches the rules is queued
and sent in the background (200 per run of the scheduled action, which is triggered immediately and repeats
every 10 minutes).

### Switching from Copy to Move
Files already copied to Drive are moved out of Odoo automatically: the app checks that Drive holds the same content
(MD5) and uploads it again if not, before releasing the local file.

### Bringing files back
**Bring all files back to Odoo** copies every file that is only on Drive back into Odoo and switches to Copy mode.
Do this before disconnecting, changing Google account, or uninstalling (both are refused while files exist only on
Drive).

## 7. Errors and retries

Temporary problems (network, Google rate limits, server errors) are retried automatically with increasing delays,
up to 6 attempts; the attachment shows *Error* with the message meanwhile. Permanent problems (revoked access,
invalid key) stop immediately. Use **Retry** in the Files list or **Send to Drive now** on the attachment after
fixing the cause.

| Message | Cause and fix |
|---|---|
| Google refused the credentials ... revoked | The access was removed in the Google account: click Reconnect |
| Google Drive answered 403 ... insufficient permissions | With *Only files created by Odoo*, Odoo cannot use a folder it did not create: clear *Main folder ID* or choose *Full Drive* |
| Google Drive answered 404 ... parent | The main folder or Shared Drive no longer exists or is not shared with the account |
| storageQuotaExceeded | The Drive (or the service account) has no space left: use a Shared Drive or free space |
| Could not read ... from Google Drive (server log) | In Move mode the file could not be downloaded; it is shown empty until Google is reachable again |

## 8. Good practice

- Start in **Copy** mode, check the result on Drive for a few days, then switch to **Move** if you want to save space.
- Keep regular Odoo backups: in Move mode the moved files are not inside Odoo's filestore backup, they are on Drive.
  Use Google's own retention/backup for the Drive folder.
- Use a dedicated Google account or a Shared Drive owned by the company, not a personal account.

## 9. Uninstalling

Bring all files back first (section 6). The app then uninstalls cleanly: its settings, logs and folder list are
removed; files stay in Odoo and on Drive.

## 10. Support

Nextbridge - https://www.nextbridge.pk - samiullah.3277@nxb.com.pk
