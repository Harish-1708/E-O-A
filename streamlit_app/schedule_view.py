"""The Schedule tab, laid out like the approved design: the form (send days, time zone, window, the hour strip) and the follow-up waits on the left;
the next automatic run, today and this week on the right.

Saving does exactly what it always did — the same validation, the same override file, the same commit and message. What is new is the layout, a live
picture of the window, and the cards on the right, which only DESCRIBE (they read the Sheet data the page already loaded and ask the engine's own
rules); nothing on the right changes anything.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional

import streamlit as st

import schedule_html as H
import schedule_view_logic as L
from campaign_header_logic import daily_limit, sent_today
from github_client import GitHubActionsError
from schedule_logic import COMMON_TIMEZONES, DAY_OPTIONS, build_updated_schedule_override, get_current_schedule, timezone_display_name, validate_schedule
from settings_logic import load_raw_override, override_file_path, override_to_yaml_bytes


@dataclass
class ScheduleContext:
    """What the tab needs from the page: the campaign, its leads and send log, whether it is running, when it next runs, and the page's GitHub writer."""
    campaign_cfg: Dict
    leads: List[Dict]
    send_log: List[Dict]
    running: bool
    status_note: str                      # why there is no next run when the campaign is not running ("Paused: nothing is scheduled")
    next_run: Optional[datetime]          # the next automatic send (UTC), or None
    get_client: Callable
    current_user: Callable
    campaigns_dir: str                    # where this campaign's settings files are (handed in by the page, which reads it fresh on every run)
    now: Optional[datetime] = None        # for tests; the real time otherwise
    viewer_timezone: Optional[str] = None


def _viewer_timezone() -> Optional[str]:
    """The visitor's own time zone, when Streamlit can tell (it reads it from the browser)."""
    try:
        return st.context.timezone or None
    except Exception:  # noqa: BLE001 - an older Streamlit, or no browser: the second time is simply left out
        return None


def render_schedule_tab(ctx: ScheduleContext) -> None:
    st.markdown(f"<style>{H.SCHEDULE_CSS}</style>", unsafe_allow_html=True)
    left, right = st.columns([1.55, 1], gap="large")
    with left:
        _render_form(ctx)
        stages = ctx.campaign_cfg.get("stages") or []
        st.markdown(H.waits_card_html(L.wait_rows(stages)), unsafe_allow_html=True)
    with right:
        _render_cards(ctx)


# ---------------------------------------------------------------- left: the form

def _render_form(ctx: ScheduleContext) -> None:
    campaign_name = ctx.campaign_cfg["_campaign_name"]
    current = get_current_schedule(ctx.campaign_cfg)
    with st.container(key="sc_when"):
        st.markdown(H.when_heading_html(), unsafe_allow_html=True)
        if not ctx.campaign_cfg.get("schedule"):
            st.caption("No schedule is set, so this campaign sends anytime. Saving one restricts Send Batch to the days and hours chosen here "
                       "(Preview still works anytime either way).")

        st.markdown(H.field_label_html("Send days"), unsafe_allow_html=True)
        send_days = []
        for column, (label, code) in zip(st.columns(7), DAY_OPTIONS):
            with column:
                if st.checkbox(label[:3], value=code in current["send_days"], key=f"schedule_day_{code}"):
                    send_days.append(code)

        display_names = [d for d, _ in COMMON_TIMEZONES]
        iana_names = [i for _, i in COMMON_TIMEZONES]
        current_display = timezone_display_name(current["timezone"]) or display_names[0]
        zone_column, start_column, end_column = st.columns([2.2, 1, 1])
        with zone_column:
            selected_display = st.selectbox("Time zone", display_names, index=display_names.index(current_display))
        with start_column:
            window_start = st.text_input("Start", value=current["window_start"])
        with end_column:
            window_end = st.text_input("End", value=current["window_end"])
        selected_timezone = iana_names[display_names.index(selected_display)]

        st.markdown(H.strip_html(L.hour_strip(window_start, window_end)), unsafe_allow_html=True)

        with st.container(key="sc_saverow"):
            save_column, note_column = st.columns([0.62, 2.4])
            with save_column:
                with st.container(key="sc_save"):
                    save_clicked = st.button("💾 Save schedule", type="primary")
            with note_column:
                st.markdown('<div class="sc-save-note">Changes apply to the next hourly run.</div>', unsafe_allow_html=True)

        if save_clicked:
            errors = validate_schedule(selected_timezone, window_start, window_end, send_days)
            if errors:
                for e in errors:
                    st.error(e)
            else:
                try:
                    raw_override = load_raw_override(campaign_name, ctx.campaigns_dir)
                    updated = build_updated_schedule_override(raw_override, selected_timezone, window_start, window_end, send_days)
                    client = ctx.get_client()
                    client.create_file(
                        override_file_path(campaign_name), override_to_yaml_bytes(updated),
                        message=f"Update schedule for {campaign_name} (via Streamlit, by {ctx.current_user()})",
                    )
                    st.success("Schedule saved. May take a minute to actually reflect here while the app "
                               "redeploys — it's in effect for sending immediately either way.")
                except GitHubActionsError as exc:
                    st.error(f"Save failed: {exc}")


# ---------------------------------------------------------------- right: what is happening

def _render_cards(ctx: ScheduleContext) -> None:
    cfg = ctx.campaign_cfg
    schedule = cfg.get("schedule") or {}
    tz_name = schedule.get("timezone")
    now = ctx.now or datetime.now(timezone.utc)
    viewer = ctx.viewer_timezone if ctx.viewer_timezone is not None else _viewer_timezone()

    card = L.next_run_card(ctx.next_run, now, tz_name, viewer) if ctx.running else None
    st.markdown(H.next_run_html(card, ctx.status_note if not ctx.running else "Nothing is due in the next 8 days"), unsafe_allow_html=True)

    sent = sent_today(ctx.send_log, tz_name, now)
    limit = daily_limit(cfg)
    due_now, due_tomorrow = L.due_counts(ctx.leads, cfg.get("stages") or [], tz_name)
    weekend = L.weekend_line(schedule.get("send_days") or []) if schedule else "No schedule: sends any day"
    st.markdown(H.today_html(sent, limit, L.progress_fraction(sent, limit), L.window_status(schedule, now), due_now, due_tomorrow, weekend), unsafe_allow_html=True)

    days = schedule.get("send_days") if schedule else ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
    st.markdown(H.week_html(L.week_rows(ctx.send_log, tz_name, now, days or [], limit)), unsafe_allow_html=True)
