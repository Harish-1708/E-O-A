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
import outreach
from schedule_logic import (COMMON_TIMEZONES, DAY_OPTIONS, build_updated_schedule_override, build_updated_stage_waits_override, get_current_schedule, timezone_display_name,
                            validate_schedule, validate_stage_waits)
from settings_logic import load_raw_override_live, override_file_path, override_to_yaml_bytes


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
        _render_waits(ctx)
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
                    client = ctx.get_client()
                    raw_override = load_raw_override_live(campaign_name, client, ctx.campaigns_dir)             # the LIVE file, not a possibly stale local copy
                    updated = build_updated_schedule_override(raw_override, selected_timezone, window_start, window_end, send_days)
                    client.create_file(
                        override_file_path(campaign_name), override_to_yaml_bytes(updated),
                        message=f"Update schedule for {campaign_name} (via Streamlit, by {ctx.current_user()})",
                    )
                    st.success("Schedule saved. May take a minute to actually reflect here while the app "
                               "redeploys — it's in effect for sending immediately either way.")
                except GitHubActionsError as exc:
                    st.error(f"Save failed: {exc}")


# ---------------------------------------------------------------- left: the follow-up waits

def _render_waits(ctx: ScheduleContext) -> None:
    """Each follow-up's wait, editable: a number box (- and + buttons, or type) per follow-up. Nothing is saved until 'Save waits'. Saving reads the LIVE
    settings file and changes only 'stage_wait_days', so it can never undo a schedule saved a moment earlier."""
    campaign_name = ctx.campaign_cfg["_campaign_name"]
    rows = L.wait_edit_rows(ctx.campaign_cfg.get("stages") or [])
    with st.container(key="sc_waits"):
        st.markdown(H.waits_heading_html(), unsafe_allow_html=True)
        if not rows:
            st.markdown(H.waits_empty_html(), unsafe_allow_html=True)
            return
        widths = [1.1, 1.2, 2.5]
        for column, text in zip(st.columns(widths), ("Stage", "Wait (days)", "Sends on")):
            column.markdown(H.waits_header_html(text), unsafe_allow_html=True)
        keys = {row["prefix"]: f"wait_{campaign_name}_{row['prefix']}" for row in rows}           # per campaign: switching campaigns must never carry a value over
        values: Dict[str, int] = {}
        for index, row in enumerate(rows):
            with st.container(key=f"sc_waitrow_{index}"):
                label_column, input_column, info_column = st.columns(widths)
                with input_column:
                    values[row["prefix"]] = int(st.number_input(f"Days before {row['label']}", min_value=0, max_value=outreach.MAX_STAGE_WAIT_DAYS, value=row["saved"] or 0, step=1,
                                                                format="%d", key=keys[row["prefix"]], label_visibility="collapsed"))
                described = L.describe_waits(rows[:index + 1], values)[-1]               # a row's day only needs the waits above it and its own
                label_column.markdown(H.wait_stage_html(row["label"]), unsafe_allow_html=True)
                info_column.markdown(H.wait_sends_on_html(described["sends_on"], described["day"]), unsafe_allow_html=True)
        changes = L.changed_waits(rows, values)
        if changes:
            st.caption("Not saved yet: " + ", ".join(f"{c['label']} {'was ' + str(c['was']) + ' days, now' if c['was'] is not None else 'now'} {c['now']} day{'' if c['now'] == 1 else 's'}" for c in changes) + ".")
        if L.shortened(changes):
            st.warning("A shorter wait can make leads due straight away: they go out on the next run, up to the daily limit.")
        with st.container(key="sc_savewaits"):
            save_clicked = st.button("💾 Save waits", type="primary", disabled=not changes, key="sc_savewaits_button")
        st.caption("Applies from the next hourly run, to every lead already waiting for that follow-up.")
        if save_clicked:
            errors = validate_stage_waits(values, {row["prefix"]: row["label"] for row in rows})
            if errors:
                for e in errors:
                    st.error(e)
                return
            try:
                client = ctx.get_client()
                raw_override = load_raw_override_live(campaign_name, client, ctx.campaigns_dir)
                updated = build_updated_stage_waits_override(raw_override, values)
                client.create_file(
                    override_file_path(campaign_name), override_to_yaml_bytes(updated),
                    message=f"Update follow-up waits for {campaign_name} (via Streamlit, by {ctx.current_user()})",
                )
                st.success("Follow-up waits saved. May take a minute to actually reflect here while the app redeploys — they are in effect for sending from the next run.")
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
