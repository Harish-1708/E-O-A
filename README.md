# Email Outreach Automation

Sends personalised cold-email sequences to creators, notices when they reply, stops the sequence for anyone who answers, keeps Asana and the tracking sheets in step, and gives the team a web control panel to run all of it.

> **Last checked against the code: 6 October 2026.** If something here disagrees with the code or with what you see on screen, the code is right — please fix the README. Volatile details (test counts, campaign names) are deliberately left out so this stays true longer.

## Contents

- [The 60-second picture](#the-60-second-picture)
- [What happens to a lead](#what-happens-to-a-lead)
- [Repository layout](#repository-layout)
- [The pieces](#the-pieces) — [engine](#the-engine-outreachpy) · [workflows](#github-actions-workflows) · [control panel](#the-streamlit-control-panel) · [Google Sheets](#google-sheets) · [templates](#templates) · [Asana](#asana) · [Creator Tracker](#creator-tracker-sheet) · [UGC tracker](#ugc-video-edits-tracker)
- [Schedule: when things run](#schedule-when-things-run)
- [Configuration reference](#configuration-reference)
- [Setup from scratch](#setup-from-scratch)
- [Day-to-day use](#day-to-day-use)
- [Safety design](#safety-design)
- [Privacy and security](#privacy-and-security)
- [Testing and CI](#testing-and-ci)
- [Troubleshooting](#troubleshooting)
- [Known limitations](#known-limitations)

---

## The 60-second picture

```
                     ┌───────────────────────────┐
   You ─────────────►│  Streamlit control panel  │   reads Google Sheets (read-only account)
                     │  streamlit_app/           │   never holds an email password
                     └─────────────┬─────────────┘
                commits files, or  │
                starts a workflow  ▼
                     ┌───────────────────────────┐
                     │  GitHub Actions           │   scheduled + on demand
                     │  .github/workflows/       │   holds every real credential
                     └─────────────┬─────────────┘
                                   ▼
                     ┌───────────────────────────┐        ┌──────────────────────────────┐
                     │  outreach.py  (engine)    │───────►│ Google Sheets (source of     │
                     └───┬───────────────┬───────┘        │ truth, one set per campaign) │
                         │               │                └──────────────────────────────┘
        email accounts   ▼               ▼   Asana · Creator Tracker sheet · Claude API (optional)
        SMTP send / IMAP read
```

Four ideas explain almost everything:

1. **Google Sheets is the source of truth.** Each campaign has its own tabs. Every run reads the Sheet, decides what is due, acts, and writes the result back.
2. **GitHub Actions does everything that needs a credential** — sending, reading inboxes, Asana. The control panel only reads, and asks Actions to act.
3. **The control panel never holds sending credentials.** It reads Sheets with a separate *viewer-only* service account. Anything that changes data is either a file it commits to this repo or a workflow it starts.
4. **Every scheduled run is a full sweep.** It works out what is due *right now* from the Sheet. So a run that is skipped or delayed loses nothing — the next one picks up the same work.

---

## What happens to a lead

1. **Imported** (CSV upload in the control panel) into the campaign's Master Sheet. Imports leave `Approval` blank — see the note below.
2. **Intro email** goes out once the campaign is *Active*, inside its sending window, and the daily limit allows.
3. **Follow-ups** go out one after another. Each follow-up waits `wait_days_after_previous` (default **2 days**) counted from when the *previous* stage was actually sent — not from the intro.
4. **A genuine reply or a hard bounce stops the sequence** for that lead (`Status` becomes `Stopped - Replied` / `Stopped - Bounced`). Out-of-office, auto-replies and soft bounces are logged but do **not** stop it.
5. A person can also stop one lead by hand from **Manage a lead** (for example `Stopped - Manual` or `Stopped - Rejected`); `Paused` and `Completed` also exist as hand-set stopped statuses.
6. After the last stage nothing further is due — the lead simply stays at `<last stage> Sent`. The engine never sets `Completed` by itself. Removing a lead is always soft (`Status = Removed`); nothing is ever hard-deleted.

**A lead is eligible for a stage when** it has an email address, is not in a stopped status, has not replied, has not already been sent *this* stage (duplicate protection), and — for follow-ups — the previous stage was sent and its wait has passed. Duplicate emails within one batch are dropped.

> **`Approval` does not gate sending.** It is an informational column (`Pending | Yes | No | Paused`). Some older help text in the control panel and in the *Run workflow* form still says "Approval must be Yes" — that text is out of date. Every lead meeting the rules above is eligible whatever `Approval` says.

**How sending is paced.** Sending works in *rounds*: one email per distinct sender account, all at the same moment, then a random pause of `delay_min_minutes`–`delay_max_minutes` (default **3–7 minutes**) before the next round. So a batch takes as long as its rounds take — an hour or more is normal. Limits: `daily_limit` per campaign (default 100) and an optional `per_account_daily_limit`. Which account sends: the lead's own `SenderAccount` cell, else the campaign's `default_sender_account`, else the global default in `config/settings.yaml`; optionally rotated across accounts (`sender_rotation`).

**What blocks a send** (checked for both scheduled and manual sends): the campaign is `paused`, `draft` or `deleted`; or it is outside its **sending window** (see [the important note on `schedule:`](#sending-window--read-this)). Preview is deliberately *not* blocked by either, so you can always review.

**How replies are found.** Check Replies reads each configured inbox (read-only IMAP) and matches a message to a lead by email threading headers first, then by sender address. Only a header match, or a sender match whose subject also matches the lead's original email, is acted on; a sender-only match is logged as unverified and does **not** touch the lead. Each message is classified as `Genuine Reply`, `Auto-Reply`, `Out of Office`, `Bounce (Hard)` or `Bounce (Soft)` and logged once to the Response Sheet. If an Anthropic API key is configured, each new genuine reply also gets a one-time intent label; without a key those fields are simply blank.

---

## Repository layout

```
outreach.py                  the engine: all sending, reply-checking, Asana and Sheets logic, as a CLI
ugc_tracker_sync.py          standalone: Asana "Rights Secured" -> UGC Video Edits Tracker sheet
ugc_tracker_logic.py         pure matching/labelling logic for the UGC sync
asana_client.py              minimal Asana API client used by the UGC sync
requirements.txt             Python dependencies for the engine and its tests

config/
  settings.yaml              global defaults shared by every campaign (+ the shared Sheet id)
  campaigns/<name>.yaml      OPTIONAL per-campaign overrides (status, schedule, limits, Asana ...)
  campaigns-overrides-README.md   how overrides and stage auto-discovery work
  email_account_slots.yaml   non-secret map of which email account sits in which secret slot

templates/<campaign>/        the email templates: <stage>_<variant>.txt   (this folder IS the campaign)

imports/  removals/  replies/  mark_read/
                             short-lived "payload" files the control panel commits and workflows
                             consume then delete (see Privacy and security)

.github/workflows/           every workflow (see table below)
streamlit_app/               the control panel (has its own README)
tests/                       engine + workflow-schedule tests (fixtures under tests/fixtures/)
streamlit_app/tests/         control-panel tests (fixture repo under streamlit_app/tests/fixtures/)
```

---

## The pieces

### The engine: `outreach.py`

One file, run as `python outreach.py <command>`. Run `python outreach.py --help`, or `python outreach.py <command> --help` for arguments. Credentials come from environment variables (set by the workflows).

| Command | What it does | Run by |
|---|---|---|
| `preview` | Shows what *would* be sent; sends nothing | `preview_batch.yml` (the control panel runs the same logic in-app) |
| `send` | Sends one stage's batch | `send_batch.yml` |
| `auto-send-all` | For every *Active* campaign, tries every stage that is due | `auto_send.yml` |
| `check-replies` / `check-replies-all` | Reads inboxes, logs replies and bounces, stops sequences | `check_replies.yml` |
| `import-leads` | Adds leads from a payload file | `import_leads.yml` |
| `remove-leads` | Soft-removes leads | `remove_leads.yml` |
| `set-lead-override` | Hand-sets a lead's status, Asana stage or reply status | `set_lead_override.yml` |
| `send-reply` | Sends one manual reply from the Responses inbox | `send_reply.yml` |
| `mark-responses-read` | Marks responses as read | `mark_responses_read.yml` |
| `sync-asana` / `sync-asana-all` | Creates/updates Asana tasks (and the Creator Tracker sheet) | `sync_asana.yml` |
| `check-account-health` | Tests each email account's IMAP login, writes the result to a tab | `check_account_health.yml` |
| `dashboard` | Recomputes the dashboard tab(s) | `dashboard.yml`; also after every send and reply check |
| `backfill-thread-subject` | One-time repair for leads already mid-sequence | `backfill_thread_subject.yml` |

### GitHub Actions workflows

| Workflow | Triggers | Lock | Purpose |
|---|---|---|---|
| Auto Send | schedule + manual | `google-sheets-api` | Send whatever is due, for every Active campaign |
| Check Replies | schedule + manual | `google-sheets-api` | Read inboxes; log replies/bounces; refresh dashboards |
| Sync Asana + Creator Tracker | schedule + manual | `google-sheets-api` | Push lead state to Asana and the Creator Tracker sheet |
| Update Dashboard | schedule + manual | `google-sheets-api` | Recompute dashboard tabs |
| Check Account Health | schedule (every 2 h, around the clock) + manual | `google-sheets-api` | Test email account logins |
| UGC Tracker Sync | schedule + manual | `ugc-tracker-sync` | Asana → UGC Video Edits Tracker sheet |
| Send Batch | manual (typed `SEND`) | `send-batch-<campaign>` | One manual batch |
| Preview Batch | manual | — | Preview without sending |
| Import / Remove Leads | manual | per campaign | Consume a payload, then delete it |
| Send Reply · Mark Responses Read · Set Lead Override · Backfill Thread Subject | manual | — | Small one-off actions |
| CI | push + pull request | — | Runs the test suites |

*Manual* means started from the control panel or from the Actions tab → *Run workflow*. Exact times for the scheduled ones are in [Schedule](#schedule-when-things-run).

### The Streamlit control panel

Full detail is in [`streamlit_app/README.md`](streamlit_app/README.md). In short, the pages are:

- **Campaigns** — the everyday page. A searchable list (create, open, duplicate). Inside a campaign, the Pause / Resume / Launch controls sit at the top, above tabs for **Analytics**, **Data** (upload leads, remove, *Manage a lead*), **Sequences** (edit templates, add variants/stages), **Schedule**, **Settings** (accounts, limits, Asana, Creator Tracker, Send Batch, maintenance, Danger Zone) and **Responses** (conversations, reply, mark read, *Check Replies Now*).
- **Controls** — Preview, Send Batch, Check Replies, Backfill in one place.
- **Responses** — one inbox across every campaign.
- **Email Accounts** — add, edit, remove sending accounts, and see their connection status.
- **New Campaign / Add Stage**, **Overview** (totals for every campaign), **Dashboard** (per-campaign dashboards).

### Google Sheets

One spreadsheet (`shared_sheet_id` in `config/settings.yaml`). Each campaign gets these tabs, named from the campaign name unless the campaign overrides them:

| Tab | Holds |
|---|---|
| `<name> Master Sheet` | One row per lead — the heart of the system |
| `<name> Response Sheet` | One row per inbound message: classification, how it was matched, action taken, optional intent, read/unread |
| `<name> Custom Log Sheet` | The send log: one row per email sent |
| `<name> Error Log` | Failures, with type and detail |
| `<name> Dashboard` | Computed metrics |

Shared tabs: `All Campaigns Dashboard` and `Email Accounts Health`. A campaign's tabs are created automatically the first time Preview, Send or Check Replies runs for it — until then the control panel says *"Tab … doesn't exist yet."*

Key Master Sheet columns (the engine locates columns by header name, so the order in the Sheet does not matter, and extra columns you add are preserved):

| Column(s) | Meaning |
|---|---|
| `LeadID`, `Email`, `FirstName`, `LastName`, `Company`, `Campaign` | Identity |
| `Approval` | Informational only |
| `SenderAccount` | Optional per-lead sending account |
| `CurrentStage`, `NextEligibleAt` | Where the lead is in the sequence |
| `IntroSentAt`, `IntroVariant`, `FollowUp1SentAt` … `FollowUp10Variant` | When each stage was sent, and which variant |
| `Status`, `LastActionAt`, `Error` | Current state |
| `ReplyStatus`, `ReplyAt`, `LastInboundClassification`, `LastInboundAt` | Reply tracking |
| `MessageID`, `ThreadReferences`, `ThreadSubject` | Keeps follow-ups in the same email thread |
| `AsanaTaskGID`, `ManualAsanaStage`, `LastSyncedAsanaStage` | Asana link and stage control (see [Asana](#asana)) |

Any other column (for example `Product` or `Creator`) can be used as a `{{placeholder}}` in a template.

### Templates

`templates/<campaign>/<stage>_<variant>.txt`. **The folder is the campaign** — no registration needed.

- **Stages:** `intro`, then `followup1` … `followup10` (up to 11 in total).
- **Variants:** `A`–`D` (up to four versions of each stage, for testing wording).
- **Format:** the first line is `Subject: …`, then a blank line, then the body. `{{ColumnName}}` is replaced with that lead's value; a placeholder with no matching column is reported in the Error Log.
- **Auto-discovery rules:** stages must be contiguous starting from `intro`, and every stage must offer exactly the same variant letters as `intro`. A mismatch is rejected with a clear error rather than silently shrinking the campaign. Details in [`config/campaigns-overrides-README.md`](config/campaigns-overrides-README.md).

### Asana

Per campaign (turn on under *Settings → Asana Sync*), each lead becomes a task in an Asana project, placed in one of six sections:

| Stage | How it is set |
|---|---|
| Sourced · Outreach Sent · Follow-up · Negotiating | Derived automatically from sends and replies |
| **Rights Secured · Declined / Dead** | **Human decisions only** — a sync never moves a task into or out of either on its own |

- Task title is `[Client] | [CreatorHandle] – [Product]`; custom fields are filled by matching Asana field names to Sheet column names. Leads are matched to existing tasks by `AsanaTaskGID`, with a second safeguard that matches on the video, so a lost GID does not create a duplicate.
- **`ManualAsanaStage`** (set from *Manage a lead*) overrides the derived stage for one lead.
- **`LastSyncedAsanaStage`** is written by the sync itself — never edit it. It records where the sync last left the task. If a task sits in *Rights Secured* or *Declined / Dead* and has since been dragged there directly in Asana, the live position wins over an older `ManualAsanaStage` override that points somewhere else. An override that itself names *Rights Secured* or *Declined / Dead* always wins.
- Tasks are never moved backwards automatically.

### Creator Tracker sheet

A separate, much larger sheet shared across campaigns (`CREATOR_TRACKER_SHEET_ID` / `CREATOR_TRACKER_WORKSHEET_NAME`; enable per campaign with `tracker_sync.enabled`). The sync **never creates rows and never touches any column except `Contact Status`, `Last Contacted Date` and `Rights Duration`** — every other column belongs to a different process. A lead with no matching row, or an ambiguous name match, is reported as a warning, never guessed at.

### UGC Video Edits Tracker

A standalone automation (`ugc_tracker_sync.py`, run by `ugc_tracker_sync.yml`) for the editing team. Every run it finds each Asana task in **Rights Secured** and makes sure it has a row in the tracker sheet's `Tracker` tab:

- Columns: `Asana Task GID` · `Creator` · `Brand` · `Product` · `Tiktok` · `Raw` · `Rights expiration` · `Status` · `Edited folder` · `Notes`.
- `Brand` is always the constant **DudeRobe** (the client). `Product` is the real value from Asana's *Product* field (DudeRobe, BroThrow or SheRobe). Duplicate creators get `@handle`, `@handle_2`, …
- `Tiktok` and `Raw` are hyperlinks to matching Google Drive items. Raw Contents is searched for files **and** folders (multi-clip creators); Tiktok Contents for files only. Matching uses a bracketed task id in the file name first — e.g. `DudeRobe - @handle – DudeRobe [1218941738388881].mov` — and falls back to handle + product. More than one candidate is logged and **never written**.
- A cell that already has content is **never overwritten**. A blank `Tiktok`/`Raw`/`Product` cell is re-checked every run, since raw files often arrive later.
- `Status` (dropdown: Not edited / Edited / Live, colour-coded), `Edited folder` and `Notes` belong to the editing team; the automation never writes them.
- It has its own lock (`ugc-tracker-sync`), so it can never block, or be blocked by, the email workflows.

---

## Schedule: when things run

Five workflows run every 30 minutes, but **only between 5:00 PM and 5:00 AM IST** (24 runs a day each). Outside that window nothing runs on its own. Manual buttons and *Run workflow* work at any hour.

The window is the IST equivalent of US business hours: the active campaigns send 9–5 New York / Chicago time, roughly 6:30 PM–4:30 AM IST, so the window contains it. The schedule only decides when a job *wakes up*; each campaign's own sending window still decides whether anything is sent.

| Workflow | Runs at (IST, every half hour) | Cron lines (UTC) |
|---|---|---|
| Check Replies | :07 and :37 — 5:07 PM to 4:37 AM | `37 11` / `7,37 12-22` / `7 23` |
| Auto Send | :13 and :43 — 5:13 PM to 4:43 AM | `43 11` / `13,43 12-22` / `13 23` |
| Sync Asana + Creator Tracker | :19 and :49 — 5:19 PM to 4:49 AM | `49 11` / `19,49 12-22` / `19 23` |
| Update Dashboard | :25 and :55 — 5:25 PM to 4:55 AM | `55 11` / `25,55 12-22` / `25 23` |
| UGC Tracker Sync | :04 and :34 — 5:04 PM to 4:34 AM | `34 11` / `4,34 12-22` / `4 23` |

(each cron line is followed by `* * *`.) GitHub cron is always UTC and IST is UTC+5:30, so 5 PM–5 AM IST is 11:30–23:30 UTC; the window never crosses midnight in UTC. The minutes are staggered in the order *Check Replies → Auto Send → Sync Asana → Dashboard*, so a reply is recorded before the next send decides who is due. They avoid :00 and :30 because GitHub delays — and sometimes drops — scheduled runs under load, worst at the start of the hour. Check Account Health is separate: every 2 hours, around the clock.

### What happens when an Auto Send run takes longer than 30 minutes

This is normal. The next scheduled Auto Send does **not** start a second copy; it waits, so two sends never overlap and no one gets a duplicate email. GitHub's rule for a concurrency group is **one running and one waiting** run. If another run arrives while one is already waiting, the older waiting run is dropped (shown as **Cancelled** — expected, not an error) and the newer takes its place. The four Google-Sheets workflows share one group (`google-sheets-api`), so during a long send:

- nothing else that shares the lock can start (Check Replies, Sync Asana and Dashboard wait);
- of everything that arrives meanwhile, only the newest keeps its place;
- when the send ends, that waiting run starts and the normal rhythm returns within about a cycle.

A dropped run loses nothing (each run is a full sweep). The real cost is that replies are not checked, and Asana is not synced, *while a long send is running*.

**Why Auto Send stays inside the shared lock.** Sending and reply-checking both write the `Status` column on the same rows. If they overlapped, a reply could be overwritten by a send in progress, and a lead who had just replied could be sent a follow-up. Do not give Auto Send its own lock to "unblock" the others.

**Timeouts.** Check Replies, Sync Asana and Dashboard stop after 30 / 30 / 20 minutes so one hung call cannot hold the shared lock for hours; UGC Tracker Sync after 10. Auto Send deliberately keeps GitHub's maximum (360): force-stopping it mid-round could leave an email sent but not yet recorded, and the next run would send it again.

### Things only the real Actions tab can confirm

- **`schedule` is best-effort.** A run can start minutes late, and under heavy GitHub load occasionally not at all.
- **60-day inactivity.** In a public repository GitHub switches scheduled workflows off after 60 days with no repository activity. The control panel's commits count; a repo left untouched for two months would silently stop. Re-enable from the Actions tab.
- **Schedules run only from the default branch**, so a change takes effect once merged to `main`.
- **Replies that arrive between about 5 AM and 5 PM IST wait until about 5:07 PM IST** unless someone clicks *Check Replies Now*; the same applies to the Asana, Creator Tracker, UGC and dashboard refreshes. `reply_monitor.lookback_hours` (default 24) must stay comfortably longer than that 12.5-hour gap — a test guards this.

To move the window, edit the three cron lines in each workflow and the constants at the top of `tests/test_workflow_schedules.py`.

---

## Configuration reference

**`config/settings.yaml`** — global. `shared_sheet_id`; `email_accounts.default_account`; and `default_campaign_settings` (the wait days per stage, `sending`, `reply_monitor`) which every campaign inherits.

**`config/campaigns/<name>.yaml`** — optional; only what differs from the defaults is needed (it is deep-merged over the defaults):

```yaml
status: active              # active | paused | draft | deleted   (default: active)
schedule:                   # the SENDING WINDOW — see the note below
  timezone: America/New_York      # a real IANA name, never "EST"
  window_start: '09:00'
  window_end: '17:00'
  send_days: [mon, tue, wed, thu, fri]
sending:
  daily_limit: 100
  per_account_daily_limit: 10     # optional
  sender_rotation: false
  rotation_accounts: [sales1, sales2]   # optional; omit to rotate across all accounts
  delay_min_minutes: 3
  delay_max_minutes: 7
default_sender_account: sales1
reply_monitor:
  lookback_hours: 24              # keep >= 14 (see Schedule)
asana:
  enabled: true
  project_name: Creator Outreach
tracker_sync:
  enabled: true
# Advanced: declare BOTH together to turn off stage auto-discovery and require every file:
# stages: [...]   variants: [...]
# Advanced: sheet_id, master_tab, responses_tab, send_log_tab, error_log_tab, dashboard_tab
```

### Sending window — read this

**A campaign's sending window comes only from its `schedule:` block.** If a campaign has no `schedule:` block, the code treats it as "always allowed" — it can send at any hour. The `timezone` / `window_start` / `window_end` that appear under `sending:` in `config/settings.yaml` are required by validation but are **not used to enforce a window**. Set a schedule for every campaign: *Campaigns → the campaign → Schedule*.

### Email accounts

Each sending account is one JSON secret, `EMAIL_ACCOUNT_SLOT_1` … `EMAIL_ACCOUNT_SLOT_10`:

```json
{"name": "sales1", "address": "sales1@example.com", "app_password": "…"}
```

Gmail needs only those three fields (an app password, not the login password). A non-Gmail provider also sets `smtp_host`, `smtp_port`, `smtp_username` and the matching `imap_host`, `imap_port`, `imap_username`. The control panel's *Email Accounts* page manages the slots for you and records name → slot → address (never a password) in `config/email_account_slots.yaml`. The older single `EMAIL_ACCOUNTS_JSON` secret still works and is merged with the slots; **a slot wins over a same-named `EMAIL_ACCOUNTS_JSON` entry**, so migrating an account is just "Add Account" with the same name.

---

## Setup from scratch

### 1. Google

1. Create a Google Cloud project; enable the **Google Sheets API** (and the **Google Drive API** if you use the UGC tracker).
2. Create a service account and download its JSON key — this is `GOOGLE_SERVICE_ACCOUNT_JSON` for GitHub Actions. Share the campaign spreadsheet, the Creator Tracker sheet and the UGC tracker sheet with its email address as **Editor**. For the UGC tracker, also share the Raw and Tiktok Drive folders with it (a Drive `403` means either the Drive API is off for the project or the folder is not shared).
3. Create a **second**, separate service account for the control panel and share the campaign spreadsheet with it as **Viewer** only. Its JSON goes in Streamlit Secrets. Keeping it viewer-only is what guarantees the web app can never write to your data.

### 2. GitHub repository secrets

*Settings → Secrets and variables → Actions.*

| Secret | Needed for | Notes |
|---|---|---|
| `GOOGLE_SERVICE_ACCOUNT_JSON` | everything | the Editor service account |
| `EMAIL_ACCOUNT_SLOT_1` … `_10` (and/or `EMAIL_ACCOUNTS_JSON`) | send, replies, health | see Email accounts |
| `ANTHROPIC_API_KEY` | reply intent labels | optional; blank labels without it |
| `ASANA_ACCESS_TOKEN` | Asana sync, UGC sync | |
| `ASANA_DEFAULT_ASSIGNEE_EMAIL` | Asana sync | optional default task assignee |
| `CREATOR_TRACKER_SHEET_ID`, `CREATOR_TRACKER_WORKSHEET_NAME` | Creator Tracker sync | the tab's own name, not the file name |
| `UGC_TRACKER_SHEET_ID`, `UGC_TRACKER_RAW_FOLDER_ID`, `UGC_TRACKER_TIKTOK_FOLDER_ID`, `UGC_TRACKER_ASANA_PROJECT_GID` | UGC tracker | all required |
| `UGC_TRACKER_SHARED_DRIVE_ID` | UGC tracker | optional; only if automatic Shared Drive discovery fails |

### 3. The control panel

1. Deploy on [Streamlit Community Cloud](https://share.streamlit.io): main file `streamlit_app/app.py`.
2. Create a **fine-grained GitHub token** scoped to *only this repository* with: **Actions** (read & write — start workflows, poll status), **Contents** (read & write — templates, settings and campaign files are committed directly) and **Secrets** (read & write — only for the Email Accounts page's Add/Edit/Remove; it lets the token overwrite or delete account credentials, though it can never read one back). **No Pull requests permission is needed.**
3. For each colleague run `python streamlit_app/tools/generate_password_hash.py` and paste the printed `[auth_users.<name>]` block into Streamlit Secrets (only a salted hash is stored).
4. Fill in Streamlit Secrets from `streamlit_app/secrets.toml.example`: `shared_sheet_id`, `[github]` (token, owner, repo), `[google_sheets_readonly]`, `[auth_users.*]`, and optionally `[email_accounts_directory]` (names and addresses only) for accounts still managed the legacy way. **Never commit a real `secrets.toml`.**

### 4. Asana

Create (or use) a project with the six sections named exactly as in the table under [Asana](#asana), put the API token in `ASANA_ACCESS_TOKEN`, and set `asana.enabled: true` and `asana.project_name` for each campaign (from *Settings → Asana Sync*). Asana custom-field names should match Sheet column names where you want them filled automatically.

### 5. First campaign

Create it from the control panel (*Campaigns → ＋ New Campaign*), upload leads, **set its Schedule**, run *Preview*, then *Launch*.

---

## Day-to-day use

- **Start a campaign:** ＋ New Campaign → edit templates under *Sequences* → upload leads under *Data* → set the *Schedule* → *Preview* → *Launch*. A new campaign starts as a **draft** and sends nothing until launched.
- **Watch it:** *Analytics* (by stage, variant and sender; errors) and the *Overview* / *Dashboard* pages. Each Auto Send run writes a plain summary in the workflow's job summary — including a line for every campaign that was skipped or had nothing due.
- **Handle replies:** *Responses* shows conversations (all messages from one person grouped). Reply from there, mark as read, or click *Check Replies Now* for an immediate check.
- **Pause:** *Pause* on the campaign. Paused, draft and deleted campaigns are never sent to.
- **Fix one lead:** *Data → Manage a lead* (stop it, change its Asana stage, correct its reply status).
- **Remove a campaign:** *Temporarily Remove* hides it and stops it sending; *Permanently Delete* is irreversible. Both are under *Settings → Danger Zone*.
- **Force fresh data:** the control panel caches Sheet reads for 30 seconds; *Refresh* or *Force a live check (bypasses every cache)* skips the wait.

---

## Safety design

What stops the bad outcomes:

- **No duplicate emails.** The "already sent this stage" check reads the Sheet; sends are serialised by the shared lock; a running job is never cancelled; and Auto Send is never given a short timeout.
- **A reply is never overwritten.** Sheet writes touch only the named cells — never a whole row — and sending and replying take turns.
- **Typed confirmation.** Send Batch needs `SEND` typed exactly, both in the control panel and as a check inside the workflow.
- **Sending window, daily and per-account limits, and campaign status** are enforced inside `send_batch` itself, so they apply to manual and scheduled sends alike.
- **A broken campaign cannot stop the others.** Auto Send, Check Replies and the hub list isolate failures per campaign and report them.
- **The web app cannot damage data.** It reads with a viewer-only account and has no email passwords.
- **Manual decisions are respected.** Nothing moves an Asana task into or out of *Rights Secured* / *Declined / Dead* by itself, and the Creator Tracker and UGC syncs never overwrite content a person has entered.

---

## Privacy and security

- **Keep this repository private.** The control panel commits lead imports (names and email addresses), reply texts, removal lists and read-marks into `imports/`, `replies/`, `removals/` and `mark_read/`. Each workflow deletes its file after processing, **but a deleted file remains in git history**, and in a public repository that history is readable by anyone. Public repositories do get free Actions minutes; a private one has a monthly allowance that this schedule would likely exceed on a free plan — that trade-off is real, but it is a decision to make knowingly.
- **Secrets live only in GitHub Secrets and Streamlit Secrets.** Email passwords can be written from the control panel but never read back.
- **Login** to the control panel is username + password, stored as a salted PBKDF2 hash.
- **`shared_sheet_id` is committed** in `config/settings.yaml`. It is an identifier, not a credential — the spreadsheet itself must stay shared only with the service accounts and your team.
- **Least privilege:** the control panel's Google account is viewer-only and its GitHub token is limited to this repository.

---

## Testing and CI

```bash
pip install -r requirements.txt
python -m pytest tests/ -q                       # engine + workflow schedules

pip install -r requirements.txt -r streamlit_app/requirements.txt pytest
python -m pytest streamlit_app/tests/ -q         # control panel
```

CI (`.github/workflows/ci.yml`) runs both suites on every push to `main` and every pull request, and first checks that the control-panel fixture repo is committed. Tests never touch real Google, GitHub, Asana or email: they use fakes and fixture data (`tests/fixtures/`, `streamlit_app/tests/fixtures/`).

`tests/test_workflow_schedules.py` expands the real cron lines into concrete IST run times and fails if a run drifts outside 5 PM–5 AM, off the 30-minute grid or out of order; if two lock-holders share a minute; if a lock or timeout setting could cancel a send; or if a reply lookback is shorter than the overnight gap.

**What tests cannot prove:** real timing on GitHub (delays, queue replacement, the 60-day rule), live Google/Asana/email behaviour, and the deployed Streamlit app. Those are only visible on the real Actions tab and in the running app.

---

## Troubleshooting

| You see | Likely cause | What to do |
|---|---|---|
| A workflow run marked **Cancelled** | A newer run took the single waiting slot | Normal; nothing was lost (see Schedule) |
| Nothing sent overnight | Campaign not *Active*; outside its own `schedule:` window or send days; daily limit reached; nobody due yet | Read the Auto Send job summary — it states why for every campaign |
| A campaign sends at odd hours | It has no `schedule:` block | Set its Schedule |
| *"Tab … doesn't exist yet"* | Tabs are created on first Preview / Send / Check Replies | Run one of those |
| Google `429` / quota errors | Too many Sheets reads in a minute | Wait; the shared lock already serialises the workflows |
| An Asana task keeps returning to a stage | A stale `ManualAsanaStage` override disagrees with where you moved it | *Manage a lead* → clear or update the override. Moves made directly in Asana to *Rights Secured* / *Declined / Dead* are respected automatically |
| Replies seem missing | Check Replies only runs 5 PM–5 AM IST; or an inbox login is failing; or lookback too short | *Check Replies Now*; *Email Accounts* page for health; keep lookback ≥ 14 h |
| UGC tracker: Drive `403` | Drive API off for the project, or the folder is not shared with the service account | Enable the API; share the exact folder; set `UGC_TRACKER_SHARED_DRIVE_ID` if it is on a Shared Drive |
| UGC tracker: a GID shows as `1.21894E+15` | An old row written before the text-format fix | Retype it with a leading apostrophe, e.g. `'1218941739494020` |
| Scheduled runs stopped entirely | GitHub disabled schedules after 60 days of inactivity | Re-enable under the Actions tab |
| A page shows old data | 30-second read cache | *Refresh* / *Force a live check* |

---

## Known limitations

- **Replies and syncs pause 5 AM–5 PM IST** (by design of the window) unless triggered by hand.
- **A long Auto Send blocks the other lock-holders** until it finishes (by design — they write the same cells).
- **Manual *Send Batch* and Auto Send use different locks** (`send-batch-<campaign>` vs `google-sheets-api`), so nothing prevents both running at once; if they overlapped they could both send the same stage.
- **The sending window is checked when each stage's batch starts**, not between rounds, so a batch that starts inside the window can keep sending after it closes.
- **`Approval` is informational**; some old help text still says otherwise.
- **`sending.timezone/window_*` in `settings.yaml` are not enforced** (only `schedule:` is).
- **Check Account Health is not subject to the 5 PM–5 AM window** (it runs every 2 hours, around the clock). Manual runs never are.
- **The UGC tracker is not shown in the control panel** — it runs only from GitHub Actions.
- **Streamlit Community Cloud's free tier sleeps** after about 12 hours idle; the next visitor waits roughly 30 seconds.
