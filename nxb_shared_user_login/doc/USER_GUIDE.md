# Shared User Login with Employee PIN

**User guide - Odoo 18 - Version 18.0.1.0.0 - Prepared by Nextbridge**

Website: https://www.nextbridge.pk - Support: samiullah.3277@nxb.com.pk - License: OPL-1

## 1. What the app does

On shared screens (shop floor, warehouse, front desk, workshop) several employees often use the same Odoo user.
Without this app, every message, note and e-mail is signed with that shared user, and nobody knows who did what.

With the app:

1. The shared user logs in with its login and password, as usual.
2. A **"Who is working?"** screen appears. The employee taps their name and types their PIN.
3. Odoo opens with the employee's name and photo in the top bar.
4. Everything the employee posts is signed with their name: chatter messages, logged notes, field-change
   tracking, Discuss messages and outgoing e-mails, on every model, including custom apps.
5. **Switch employee** in the user menu hands the screen to a colleague.

Nothing in Odoo is reachable on a shared account before an employee has signed in: pages redirect to the
"Who is working?" screen, and data calls and downloads are refused.

## 2. Installation

1. Copy the `nxb_shared_user_login` folder into your addons path, or install the app from the Odoo App Store.
2. Update the apps list and install **Shared User Login with Employee PIN**.

Dependencies: Discuss (`mail`) and Employees (`hr`).

## 3. Setup

### 3.1 Switch the feature on
**Settings > General Settings > Users > Shared user accounts**. When it is on you can also set:

| Setting | Meaning | Default |
|---|---|---|
| Ask for the PIN again after (minutes idle) | Locks the screen after this many minutes without activity; 0 = never | 0 |
| Wrong PINs before locking | After this many wrong PINs in a row, the employee's PIN is locked | 5 |
| Lock duration (minutes) | How long the PIN stays locked | 15 |
| Allow the external API for shared accounts | XML-RPC / JSON-RPC calls cannot ask for a PIN, so they are refused for shared accounts unless this is ticked | Off |

### 3.2 Give each employee a PIN
**Employees > employee > Settings tab > PIN Code** (the same PIN Odoo uses for attendance kiosks). Use 4 to 12
digits. Employees can later change their own PIN from the user menu (**Change my PIN**).

### 3.3 Share a user
**Settings > Users & Companies > Users > the user > Shared account tab**: tick **Shared account** and add the
employees who use it. The filter *Shared accounts* in the user list shows all shared users.

Give the shared user only the access rights the team needs. Administrator accounts and portal users cannot be
shared.

## 4. Daily use

- **Sign in:** after the normal login, choose your name, type your PIN, click **Sign in**.
- **Switch employee:** user menu (top right) > **Switch employee**. The next person signs in with their PIN.
- **Change my PIN:** user menu > **Change my PIN** (current PIN, then the new one twice).
- **Log out:** user menu > Log out, as usual. The "Who is working?" screen also has a *Log out* link.
- **Idle lock:** if switched on, Odoo asks for a PIN again after the chosen idle time.

### What shows the employee's name

| Where | Shown |
|---|---|
| Chatter messages and logged notes | Employee as author (name and photo) |
| Field changes (tracking) | Employee as author |
| Discuss messages | Employee as author |
| E-mails sent to customers | Employee's name; address = employee's work e-mail, or the shared user's e-mail |
| Top bar | Employee's name and photo |
| Created by / Last updated by | The shared user (Odoo's technical fields do not change) |

Replies to e-mails still reach the shared account (it stays the follower), so the team does not miss them.

## 5. Security

- **PIN lockout:** after the configured number of wrong PINs, the PIN is locked for the configured time. HR
  officers can unlock it in the employee form (**Unlock** next to *PIN locked until*).
- **Idle lock:** optional, see 3.1.
- **External API:** refused for shared accounts by default, because nobody can be identified there.
- **History:** **Settings > Users & Companies > Shared account sign-ins** lists every sign-in, switch, idle lock,
  wrong PIN, lock and PIN change with date, employee and IP address (kept 12 months).
- Employees only see the names of the employees allowed on that shared account.

## 6. Troubleshooting

| Situation | Fix |
|---|---|
| "has no PIN yet" | Set the employee's PIN Code in the employee form (Settings tab) |
| "Too many wrong PINs" | Wait for the lock to end or unlock the employee in the employee form |
| "No employee can use this account yet" | Add employees in the user's Shared account tab |
| An integration using this user stopped working | It uses the external API: give it its own user, or tick *Allow the external API for shared accounts* |
| The Shared account tab is missing | Switch the feature on in General Settings first; it is not available for portal users |

## 7. Licensing note

This app identifies who works on a shared screen. Odoo Enterprise subscriptions are priced per person who uses the
system: check your subscription terms before sharing one account between several people.

## 8. Uninstalling

Uninstalling removes the settings, the shared-account links and the sign-in history. Messages keep the employee
names they were posted with.

## 9. Support

Nextbridge - https://www.nextbridge.pk - samiullah.3277@nxb.com.pk
