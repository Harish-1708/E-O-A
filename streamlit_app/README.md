# Outreach Control Panel (Streamlit app)

The web control panel for the outreach system. It is a **window onto** the engine, not a second sending system: it reads Google Sheets, and everything that changes data is either a file it commits to the repository or a GitHub Actions workflow it starts. The system as a whole — lifecycle, schedule, setup, safety — is described in the [root README](../README.md); this file covers only the app.

> **Last checked against the code: 6 October 2026.** If this disagrees with the code or the screen, the code is right.

## What it does and does not do

- **Reads** every campaign's Sheets with a **viewer-only** Google service account. It cannot write to a Sheet and holds no email passwords.
- **Writes** in exactly two ways: it **commits files** to the repo (templates, campaign settings, lead-import and reply payloads, account metadata), or it **starts a workflow** (Send Batch, Check Replies, Import, Sync, …). The workflows run with the real credentials.
- **Preview** runs the engine's own logic inside the app, read-only, so what you preview is exactly what would be sent.
- **Send Batch** requires typing `SEND`; the workflow checks it again.

## Pages

| Page | Use it for |
|---|---|
| **Campaigns** | The everyday page. A searchable list of campaigns (status, counts), with ＋ New Campaign, Open and ⧉ Duplicate; a *Deleted Campaigns* list for temporarily removed ones. Opening a campaign shows six tabs (below). |
| **Controls** | Preview, Send Batch, Check Replies and the ThreadSubject backfill in one place. |
| **Responses** | One inbox across every campaign: conversations grouped per person, reply, mark as read, *Check Replies Now (all campaigns)*. |
| **Email Accounts** | Add, edit and remove sending accounts, and see each account's connection status. |
| **New Campaign / Add Stage** | Create a campaign or add a stage to one. New campaigns start as drafts. |
| **Overview** | A table of totals for every campaign. |
| **Dashboard** | The per-campaign dashboards computed by the engine. |

**Inside a campaign (Campaigns → Open):** the **Pause / Resume / Launch** buttons sit at the top of the page, above these tabs.

| Tab | What is there |
|---|---|
| **Analytics** | Counts by stage, variant and sender account, and all-time errors. |
| **Data** | A *Diagnose* panel showing exactly what the page is reading; *Add Leads (upload CSV)* with column mapping; the leads table; *Remove leads* (soft remove); *Manage a lead* (stop one lead, set its Asana stage or reply status). |
| **Sequences** | Edit templates (written straight to the repo — one commit per changed file), add a variant, add a follow-up stage, delete a variant or the last stage. |
| **Schedule** | Timezone, start/end time and send days — the sending window. |
| **Settings** | Sender accounts, sending limits, a maintenance tool (ThreadSubject backfill), *Asana Sync* and *Creator Tracker Sync* settings with "sync now" buttons, **Send Batch**, and the *Danger Zone* (*Temporarily Remove*, *Permanently Delete*). |
| **Responses** | This campaign's conversations, reply, mark as read, *Check Replies Now*. |

> Set a **Schedule** for every campaign. A campaign without one has no sending window at all (see the root README).

## Secrets

Set in Streamlit Community Cloud → App settings → Secrets (template: `secrets.toml.example`). **Never commit a real `secrets.toml`.**

| Key | Purpose |
|---|---|
| `shared_sheet_id` | The spreadsheet holding every campaign's tabs |
| `[github]` `token`, `owner`, `repo` | Fine-grained token scoped to **only this repository** |
| `[google_sheets_readonly.service_account_json]` | A **separate, viewer-only** Google service account |
| `[auth_users.<name>]` `salt`, `password_hash` | One block per colleague; generate with `python streamlit_app/tools/generate_password_hash.py` |
| `[email_accounts_directory]` *(optional)* | Names → addresses (no passwords) for accounts still managed the legacy way |

The GitHub token needs three repository permissions: **Actions** (read & write), **Contents** (read & write) and **Secrets** (read & write — only for the Email Accounts page's Add/Edit/Remove). It does **not** need Pull requests. Note that `secrets.toml.example` still carries an outdated comment about pull-request permissions; ignore it.

Free-tier note: Streamlit Community Cloud sleeps an idle app after about 12 hours; the next visitor waits roughly 30 seconds.

## Run it locally

```bash
pip install -r streamlit_app/requirements.txt
# create .streamlit/secrets.toml (git-ignored) from secrets.toml.example, then:
streamlit run streamlit_app/app.py
```

## How it stays fast and correct

- **Sheet reads are cached for 30 seconds**, and the data-heavy pages read their independent tabs (and, on the list pages, different campaigns) **at the same time**, up to six at once. *Refresh* and *Force a live check (bypasses every cache)* skip the wait.
- **Live reads from GitHub.** Campaign status, template and stage information are read from GitHub, not only from the app's own checkout, because that checkout can lag behind a very recent commit. A small commit-keyed cache (`github_client.py`) avoids re-downloading unchanged files: a change made by anyone else appears within a few seconds, and the app's *own* writes appear immediately.
- **Worker threads only run plain Sheets/GitHub reads — they never call Streamlit.** Cached connectors are resolved on the normal script thread, then handed to the threads that do the slow reads (`parallel_logic.py`).
- Rule of thumb for contributors: **logic lives in `*_logic.py` modules with no Streamlit in them** (tested without a browser); pages stay thin.

## Code map

| Path | Role |
|---|---|
| `app.py` | Entry point: page config, login gate, navigation |
| `pages/` | One file per page (`campaigns`, `controls`, `responses`, `email_accounts`, `new_campaign`, `overview`, `dashboard`) |
| `sheets_readonly.py` | The viewer-only Sheets connector (looks each tab up once, retries once if a tab was replaced) |
| `github_client.py` | Commits, workflow dispatch, run status, secrets, and the commit-keyed read cache |
| `parallel_logic.py` | Runs independent slow reads together; results always in input order |
| `send_logic.py` · `schedule_logic.py` · `launch_logic.py` · `settings_logic.py` | The typed-`SEND` gate; the Schedule tab; launch/pause/resume; reading and writing campaign override files |
| `campaigns_hub_logic.py` · `overview_logic.py` · `responses_hub_logic.py` | The campaign list, the overview table, and conversation grouping |
| `auth.py` · `tools/generate_password_hash.py` | Login (salted PBKDF2) and the tool that generates a user's block |

## Tests

```bash
pip install -r requirements.txt -r streamlit_app/requirements.txt pytest
python -m pytest streamlit_app/tests/ -q
```

Tests read a committed **fixture repository** at `streamlit_app/tests/fixtures/repo` instead of live campaigns, and fake Google, GitHub and Asana — nothing real is touched. CI checks that the fixture repo is present before running. Two modules (`test_github_read_cache_pages.py`, `test_sheet_speed_pages.py`) deliberately live apart from `test_pages_smoke.py`, because the smoke module replaces GitHub reads for every test in it, which would bypass the very caches those two exist to test.

What the tests cannot prove: the deployed app on Streamlit Cloud, real Google/GitHub latency, and live credentials.
