import streamlit as st

from auth import login_gate, current_user, logout
from page_state import mark_active_page

st.set_page_config(page_title="Outreach Control Panel", page_icon="📬", layout="wide")

if not login_gate():
    st.stop()

with st.sidebar:
    st.success(f"Logged in as **{current_user()}**")
    if st.button("Log out"):
        logout()
        st.rerun()


def _home():
    mark_active_page("home")
    st.title("📬 Outreach Control Panel")
    st.markdown(
        """
Welcome. This is a **control surface**, not a second sending system —
every Preview, Send, and Check Replies action here either runs the exact
same `outreach.py` logic directly (Preview) or triggers the same GitHub
Actions workflows you'd run manually (Send, Check Replies). Nothing here
bypasses the safety checks already built into the repo: duplicate
protection, per-account capacity, header-verified reply matching, and the
typed `SEND` confirmation gate.

Use the sidebar to navigate:

- **📋 Today** — what is due to be emailed right now, what is waiting and
  until when, and why anyone is out of play, for every running campaign.
  Read-only, and it uses the same rule Auto Send uses. Look here first
  when you wonder why something has or hasn't gone out.
- **🎯 Performance** — the business questions behind the counts: positive
  replies, which follow-up and template variant earn replies, how long a
  reply takes, negotiations and rights secured, and a "what worked best"
  report — for all campaigns together, with a table of every campaign at a
  glance. Each campaign shows its own Performance as its first tab. Read-only.
- **🗂️ Campaigns** — the everyday view, and the only place you need for
  day-to-day work. Search, see every campaign's status (Draft/Running/
  Paused/Completed/Attention needed) at a glance, create a new one right
  from here, and open any campaign for its Performance, Data, Sequences,
  Schedule, Settings (with Send), and Responses (with Check Replies).
  Start here.
- **💬 Responses** — every reply across every campaign in one place,
  filterable by status, campaign, and unread. Reply directly from here
  too — same tools as each campaign's own Responses tab, just spanning
  everything at once instead of one campaign at a time.
- **📧 Email Accounts** — add, edit, or remove sender accounts, see how
  much each has sent today, and their live connection status.
        """
    )


# Explicit titles/icons here (not embedded in filenames) — filename-embedded
# emoji is what caused the sidebar icons to render as broken/garbled
# characters for some people (a filesystem/encoding issue, not a Streamlit
# bug). Page files themselves now have plain ASCII names.
home_page = st.Page(_home, title="Home", icon="📬", default=True)
today_page = st.Page("pages/today.py", title="Today", icon="📋")
performance_page = st.Page("pages/performance.py", title="Performance", icon="🎯")
campaigns_page = st.Page("pages/campaigns.py", title="Campaigns", icon="🗂️")
responses_page = st.Page("pages/responses.py", title="Responses", icon="💬")
email_accounts_page = st.Page("pages/email_accounts.py", title="Email Accounts", icon="📧")

nav = st.navigation([home_page, today_page, performance_page, campaigns_page, responses_page, email_accounts_page])
nav.run()
