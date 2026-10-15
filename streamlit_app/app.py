import streamlit as st

from auth import login_gate, current_user, logout
from page_state import mark_active_page

st.set_page_config(page_title="Outreach Control Panel", page_icon="📬", layout="wide")

if not login_gate():
    st.stop()

from shell_view import render_home, render_sidebar, render_sidebar_markers  # noqa: E402  (after the login gate: nothing below runs for a visitor who is not signed in)

PAGES = {}


def _home():
    mark_active_page("home")
    render_home(PAGES, current_user())


# Explicit titles/icons here (not embedded in filenames) — filename-embedded
# emoji is what caused the sidebar icons to render as broken/garbled
# characters for some people (a filesystem/encoding issue, not a Streamlit
# bug). Page files themselves now have plain ASCII names.
PAGES["home"] = st.Page(_home, title="Home", icon="📬", default=True)
PAGES["today"] = st.Page("pages/today.py", title="Today", icon="📋")
PAGES["performance"] = st.Page("pages/performance.py", title="Performance", icon="🎯")
PAGES["campaigns"] = st.Page("pages/campaigns.py", title="Campaigns", icon="🗂️")
PAGES["responses"] = st.Page("pages/responses.py", title="Responses", icon="💬")
PAGES["accounts"] = st.Page("pages/email_accounts.py", title="Email Accounts", icon="📧")

# The built-in page list is hidden: render_sidebar draws our own (brand, links, the signed-in user and Log out).
nav = st.navigation(list(PAGES.values()), position="hidden")
render_sidebar(PAGES, current_user(), logout, current=nav)
try:
    nav.run()
finally:
    # After the page, so the page is never held up by it (and still drawn when a page stops itself with st.stop()).
    render_sidebar_markers()
