"""The 'Designed (HTML) emails' section of an HTML campaign's Sequences tab: choose an email, edit or upload its design, see it exactly as one lead would receive it (desktop or
mobile), read the checks, and save. Everything about WHAT is right lives in html_editor_logic / html_campaign_logic; this file only lays it out and reads/writes GitHub."""
import streamlit as st
import streamlit.components.v1 as components

import outreach
from config import CAMPAIGNS_DIR, WORKFLOW_SEND_TEST

import html_campaign_logic as hc
import html_editor_html as EH
import html_editor_logic as EL
import sequences_html as H
from github_client import GitHubActionsError
from leads_logic import humanize_stage
from settings_logic import build_footer_settings_override, load_raw_override_live, override_file_path, override_to_yaml_bytes


def _keys(campaign_name: str, prefix: str, variant: str) -> dict:
    base = f"{campaign_name}_{prefix}_{variant}"
    return {"saved": f"html_saved_{base}", "text": f"html_text_{base}", "draft": f"html_draft_{base}", "upload": f"html_upload_{base}", "upload_error": f"html_upload_error_{base}",
            "layout": f"html_layout_{base}", "test_to": f"html_test_to_{campaign_name}", "flash": f"html_flash_{base}", "width": f"html_width_{base}", "lead": f"html_lead_{base}"}


def _apply_upload(upload_key: str, text_key: str, error_key: str) -> None:
    """Runs when a file is chosen: its text goes into the editing box (nothing is saved until Save), or the reason it cannot be used is shown."""
    uploaded = st.session_state.get(upload_key)
    if uploaded is None:
        return
    text, error = EL.decode_upload(uploaded.getvalue())
    if error:
        st.session_state[error_key] = error
    else:
        st.session_state[text_key] = text
        st.session_state[error_key] = ""


def _use_layout(layout_key: str, text_key: str) -> None:
    st.session_state[text_key] = hc.starter_html(st.session_state.get(layout_key) or hc.DEFAULT_LAYOUT)


def _add_footer(text_key: str) -> None:
    st.session_state[text_key] = hc.add_footer_placeholder(st.session_state.get(text_key, ""))


def _remove_footer(text_key: str) -> None:
    st.session_state[text_key] = hc.remove_footer_placeholder(st.session_state.get(text_key, ""))


def _reload(keys: dict) -> None:
    for name in ("saved", "text", "draft", "upload_error"):
        st.session_state.pop(keys[name], None)


def _read_design(client, path: str):
    """(text, None) when the design is read; (None, "missing") when the file does not exist; (None, the problem) for any other failure."""
    try:
        return client.get_file_content(path).decode("utf-8"), None
    except GitHubActionsError as exc:
        return None, ("missing" if "404" in str(exc) else str(exc))
    except UnicodeDecodeError:
        return None, "The file on GitHub is not UTF-8 text."


def render_html_designs(ctx, client, campaign_name: str, stages, variants) -> None:
    st.markdown(f"<style>{EH.HTML_EDITOR_CSS}</style>", unsafe_allow_html=True)
    st.markdown(H.section_label_html("Designed (HTML) emails"), unsafe_allow_html=True)
    st.caption("The designed version each email is sent with. The subject and the plain-text copy are edited under \"Subject & plain text\"; this is the design that goes with them.")
    options = [(s["template_prefix"], v) for s in stages for v in variants]
    if not options:
        return
    labels = {f"{p}|{v}": f"{humanize_stage(p)} · Variant {v}" for p, v in options}
    choice = st.selectbox("Email", list(labels), format_func=labels.get, key=f"html_pick_{campaign_name}", label_visibility="collapsed")
    prefix, variant = choice.split("|")
    keys = _keys(campaign_name, prefix, variant)
    path = EL.design_path(campaign_name, prefix, variant)
    footer_now = EL.effective_footer(ctx.campaign_cfg, st.session_state.get(EL.footer_snapshot_key(campaign_name)))
    cfg_now = {**ctx.campaign_cfg, "footer": footer_now}                       # a footer saved a moment ago counts at once, before the app's own copy catches up

    if keys["saved"] not in st.session_state:
        st.session_state[keys["saved"]] = _read_design(client, path)
    saved_text, problem = st.session_state[keys["saved"]]

    if problem == "missing":
        st.warning("This email has no designed (.html) file yet, so the campaign cannot send it. Create one from the starter layout, then edit it here.")
        if st.button("Create it from the starter layout", key=f"html_create_{keys['saved']}", type="primary"):
            try:
                client.create_file(path, hc.starter_html(hc.DEFAULT_LAYOUT).encode("utf-8"), f"Add design {prefix}_{variant} to {campaign_name} (via Streamlit, by {ctx.current_user()})")
                _reload(keys)
                st.rerun()
            except GitHubActionsError as exc:
                st.error(f"Failed to create the design: {exc}")
        return
    if problem:
        st.error(f"Couldn't read this email's design: {problem}")
        st.button("Try again", key=f"html_retry_{keys['saved']}", on_click=_reload, args=(keys,))
        return

    # The box's own state is dropped by Streamlit whenever another email is on screen, so the edit is also kept in a draft of its own, and the box starts from it again.
    if keys["text"] not in st.session_state:
        st.session_state[keys["text"]] = st.session_state.get(keys["draft"], saved_text)
    flash = st.session_state.pop(keys["flash"], None)
    plain = {"subject": "", "body": ""}
    try:
        plain = ctx.fetch_content(client, campaign_name, prefix, variant)
    except Exception as exc:  # noqa: BLE001 - the design can still be edited without the text copy; the checks then say it is missing
        st.warning(f"Couldn't read this email's plain-text copy: {exc}")

    editor_column, side_column = st.columns([1.1, 1], gap="large")
    with editor_column:
        text = st.text_area("HTML", key=keys["text"], height=420, label_visibility="collapsed")
        st.session_state[keys["draft"]] = text
        st.file_uploader("Upload an .html file (replaces what is in the box; nothing is saved until you press Save)", type=["html", "htm"], key=keys["upload"],
                         on_change=_apply_upload, args=(keys["upload"], keys["text"], keys["upload_error"]))
        if st.session_state.get(keys["upload_error"]):
            st.error(st.session_state[keys["upload_error"]])
        layout_column, use_column = st.columns([2, 1])
        with layout_column:
            st.selectbox("Start from a layout", list(hc.STARTER_LAYOUTS), key=keys["layout"], format_func=lambda k: hc.STARTER_LAYOUTS[k]["label"], label_visibility="collapsed")
        with use_column:
            st.button("Use this layout", key=f"html_use_{keys['layout']}", on_click=_use_layout, args=(keys["layout"], keys["text"]), width="stretch")
        unsafe = hc.unsafe_problems(text)
        changed = text != saved_text
        save_column, state_column, reload_column = st.columns([1.2, 1.6, 1.2])
        with save_column:
            save_clicked = st.button("💾 Save design", key=f"html_save_{keys['saved']}", type="primary", disabled=not changed or not text.strip() or bool(unsafe))
        with state_column:
            if unsafe:
                st.caption("Not saved: remove what the checks list in red first.")
            elif changed:
                st.caption("● Unsaved change")
        with reload_column:
            st.button("↺ Reload", key=f"html_reload_{keys['saved']}", on_click=_reload, args=(keys,), width="stretch", disabled=not changed)
        if flash:
            st.success(flash)
        st.markdown(EH.title_html("Send me a test", "Sends the SAVED design, with a made-up lead and [TEST] in the subject, to an address you choose (use a team mailbox)."), unsafe_allow_html=True)
        test_column, test_button_column = st.columns([2.2, 1])
        with test_column:
            test_to = st.text_input("Send the test to", key=keys["test_to"], placeholder="team@yourcompany.com", label_visibility="collapsed")
        address_ok = outreach.is_valid_email_format((test_to or "").strip())
        with test_button_column:
            test_clicked = st.button("✉ Send test", key=f"html_test_{keys['saved']}", width="stretch", disabled=not address_ok or changed or bool(unsafe))
        if changed:
            st.caption("Save the design first: the test sends what is saved.")
        elif test_to.strip() and not address_ok:
            st.caption("That is not a valid email address.")
        if test_clicked:
            try:
                client.dispatch_workflow(WORKFLOW_SEND_TEST, {"campaign": campaign_name, "stage": prefix, "variant": variant, "to": test_to.strip()})
                st.success(f"Test requested for {test_to.strip()}. It arrives within a minute or two; if it does not, open the run under GitHub → Actions → Send Test Email.")
            except GitHubActionsError as exc:
                st.error(f"Couldn't start the test: {exc}")
        if save_clicked:
            try:
                client.create_file(path, text.encode("utf-8"), f"Update design {prefix}_{variant} in {campaign_name} (via Streamlit, by {ctx.current_user()})")
                st.session_state[keys["saved"]] = (text, None)
                st.session_state[keys["draft"]] = text
                st.session_state[keys["flash"]] = "Design saved. It is live for sending immediately."
                st.rerun()
            except GitHubActionsError as exc:
                st.error(f"Failed to save: {exc}")

    with side_column:
        lead_choices = EL.lead_options(ctx.leads)
        lead_labels = dict(lead_choices)
        picked = st.selectbox("Preview as", [k for k, _ in lead_choices], format_func=lead_labels.get, key=keys["lead"], label_visibility="collapsed")
        width_name = st.radio("Width", list(EL.PREVIEW_WIDTHS), horizontal=True, key=keys["width"], label_visibility="collapsed")
        preview = EL.render_preview(text, EL.pick_lead(ctx.leads, picked), cfg_now, utm_content=f"{prefix}_{variant}")
        if preview["html"] is not None:
            components.html(EL.preview_document(preview["html"]), width=EL.PREVIEW_WIDTHS[width_name], height=EL.PREVIEW_HEIGHT, scrolling=True)
        else:
            st.info("The preview is not shown while the design contains something that must not be sent (see the checks).")
        if preview["missing"]:
            st.warning("These placeholders match no column for this lead: " + ", ".join("{{ " + n + " }}" for n in preview["missing"]))
        with st.container(key="he_checks"):
            st.markdown(EH.title_html("Checks", "Errors stop the campaign launching; the rest are suggestions."), unsafe_allow_html=True)
            result = hc.html_checks(text, plain.get("body", ""), EL.known_variables(ctx.leads), footer_now)
            st.markdown(EH.checks_html(result), unsafe_allow_html=True)
        with st.expander("Plain-text copy and subject (edit them under \"Subject & plain text\")"):
            st.caption(f"Subject: {plain.get('subject') or '(blank: continues the thread)'}")
            st.code(plain.get("body", "") or "(empty)", language=None)

    _render_footer_panel(client, campaign_name, ctx, keys["text"], text, footer_now)


def _render_footer_panel(client, campaign_name: str, ctx, text_key: str, text: str, footer_now: dict) -> None:
    """Under the design of the email on screen: does THIS email have the footer (one button adds or removes it), and the footer's details, which are written once and shared by
    every email of the campaign. A footer is optional; the only rule is that an email gives some way to opt out (its footer, or the designer's own unsubscribe line)."""
    snapshot_key = EL.footer_snapshot_key(campaign_name)
    flash_key = f"footer_flash_{campaign_name}"
    flash = st.session_state.pop(flash_key, None)
    included = hc.footer_in_design(text)
    problems = hc.footer_problems(footer_now)
    st.markdown(H.section_label_html("Email footer"), unsafe_allow_html=True)
    with st.container(key="he_footer"):
        status_column, button_column = st.columns([3, 1.3], vertical_alignment="center")
        with status_column:
            if included:
                st.markdown(EH.title_html("This email has the footer", "It appears where {{ footer }} stands in the design. Remove it if the design has its own address and unsubscribe line."), unsafe_allow_html=True)
            else:
                st.markdown(EH.title_html("This email has no footer", "A footer is optional. Add one for the campaign's business name, address and opt-out line, "
                                          "or give the design its own unsubscribe line."), unsafe_allow_html=True)
        with button_column:
            if included:
                st.button("Remove from this email", key=f"html_footer_remove_{text_key}", on_click=_remove_footer, args=(text_key,), width="stretch")
            else:
                st.button("Add the footer", key=f"html_footer_add_{text_key}", type="primary", on_click=_add_footer, args=(text_key,), width="stretch")
        st.caption("Adding or removing it changes this email only (press Save design to keep it). The footer's details below are the same for every email in the campaign: every stage and every variant.")
        with st.expander("Footer details (shared by every email in this campaign)", expanded=included and bool(problems)):
            business_name = st.text_input("Business name", value=footer_now["business_name"], key=f"footer_business_name_{campaign_name}", placeholder="e.g. Kelson Agency")
            address = st.text_input("Postal address (required for marketing email in many countries)", value=footer_now["address"], key=f"footer_address_{campaign_name}",
                                    placeholder="e.g. 12 Example Street, New York, NY 10001")
            opt_out = st.text_input("How people opt out (leave blank for the standard wording)", value=footer_now["opt_out"], key=f"footer_opt_out_{campaign_name}", placeholder=hc.DEFAULT_OPT_OUT)
            draft = {"business_name": business_name.strip(), "address": address.strip(), "opt_out": opt_out.strip()}
            changed = draft != footer_now
            if not hc.footer_problems(draft):
                st.markdown(EH.title_html("How it will look"), unsafe_allow_html=True)
                st.markdown(f'<div class="he-footer-preview">{hc.build_footer_html(draft)}</div>', unsafe_allow_html=True)
            save_column, state_column = st.columns([1, 3])
            with save_column:
                save_clicked = st.button("💾 Save footer", key="footer_save", type="primary", disabled=not changed)
            with state_column:
                if changed:
                    st.caption("● Unsaved change")
                elif problems:
                    st.caption("Needs the " + " and the ".join(p.replace(" is missing", "") for p in problems) + ".")
        if flash:
            st.success(flash)
        if save_clicked:
            if hc.footer_problems(draft):
                st.error("Enter the business name and the postal address.")
            else:
                try:
                    raw_override = load_raw_override_live(campaign_name, client, CAMPAIGNS_DIR)               # the LIVE file, not a possibly stale local copy
                    updated = build_footer_settings_override(raw_override, draft["business_name"], draft["address"], draft["opt_out"])
                    client.create_file(override_file_path(campaign_name), override_to_yaml_bytes(updated),
                                       f"Update email footer for {campaign_name} (via Streamlit, by {ctx.current_user()})")
                    st.session_state[snapshot_key] = draft
                    st.session_state[flash_key] = "Footer saved for every email in this campaign. May take a minute to reach the rest of the app while it redeploys."
                    st.rerun()
                except GitHubActionsError as exc:
                    st.error(f"Failed to save: {exc}")
