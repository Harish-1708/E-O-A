"""Adding a variant or a follow-up stage to an HTML campaign, from the Sequences tab. Each is one click: a new variant starts as a copy of an existing one in every stage (so it can be
A/B tested and then edited), and a new follow-up stage starts with a blank subject (it continues the same email thread) and the starter design, for every variant. Both write a
.txt (the subject) and a .html (the design) per email in ONE commit, so the campaign can never be left half added."""
import streamlit as st

import html_campaign_logic as hc
import sequences_html as H
from campaign_builder import build_campaign_files, next_stage_from_stages
from github_client import GitHubActionsError
from leads_logic import humanize_stage
from sequences_logic import next_available_variant_letter


def _flash(campaign_name: str, message: str) -> None:
    st.session_state[f"html_structure_flash_{campaign_name}"] = message


def _read_text(client, path: str):
    """The file's text, or None when it does not exist (404). Any other failure is raised."""
    try:
        return client.get_file_content(path).decode("utf-8")
    except GitHubActionsError as exc:
        if "404" in str(exc):
            return None
        raise


def copy_files_for_new_variant(client, campaign_name: str, stages, source: str, new_letter: str) -> list:
    """The files for variant `new_letter`: for every stage, a copy of the source variant's subject file and design (the starter design where the source has none)."""
    files = []
    for stage in stages:
        prefix = stage["template_prefix"]
        subject_file = _read_text(client, f"templates/{campaign_name}/{prefix}_{source}.txt")
        if subject_file is None:
            raise GitHubActionsError(f"Variant {source} has no subject file for {humanize_stage(prefix)} to copy from.")
        design = _read_text(client, f"templates/{campaign_name}/{prefix}_{source}.html")
        files.append({"path": f"templates/{campaign_name}/{prefix}_{new_letter}.txt", "content": subject_file.encode("utf-8")})
        files.append({"path": f"templates/{campaign_name}/{prefix}_{new_letter}.html", "content": (design if design is not None else hc.starter_html(hc.DEFAULT_LAYOUT)).encode("utf-8")})
    return files


def render_html_add_variant(ctx, client, campaign_name: str, stages, variants, set_mode) -> None:
    letter = next_available_variant_letter(variants)
    with st.container(key="he_add"):
        st.markdown(H.editor_title_html(f"Add variant {letter}" if letter else "Add variant"), unsafe_allow_html=True)
        st.button("Cancel", key="html_add_variant_cancel", on_click=set_mode, args=(campaign_name, "edit"))
        if not letter:
            st.info("Every stage already has all 4 variants (A to D). That is the maximum.")
            return
        source = "A" if "A" in variants else variants[0]
        st.markdown(f"Variant {letter} is added to **every stage** at once, as a copy of variant {source}: the same subject and the same design. Then open it and change what "
                    "you want to test. Leads are shared out between the variants.")
        if st.button(f"Add variant {letter}", type="primary", key="html_add_variant_go"):
            try:
                files = copy_files_for_new_variant(client, campaign_name, stages, source, letter)
                client.commit_campaign_files_directly(files, f"Add variant {letter} to {campaign_name} as a copy of variant {source} (via Streamlit, by {ctx.current_user()})")
            except GitHubActionsError as exc:
                st.error(f"Failed to add the variant: {exc}")
                return
            set_mode(campaign_name, "edit")
            _flash(campaign_name, f"Variant {letter} added to all {len(stages)} stage(s) as a copy of variant {source}. Choose it above to edit its subject and design. "
                                  "If it does not show yet, refresh in a minute.")
            st.rerun()


def render_html_add_stage(ctx, client, campaign_name: str, stages, variants, set_mode) -> None:
    upcoming = next_stage_from_stages(stages, variants)
    with st.container(key="he_add"):
        st.markdown(H.editor_title_html(f"Add {humanize_stage(upcoming[0])}" if upcoming else "Add a follow-up stage"), unsafe_allow_html=True)
        st.button("Cancel", key="html_add_stage_cancel", on_click=set_mode, args=(campaign_name, "edit"))
        if upcoming is None:
            st.info("There is no follow-up stage left to add.")
            return
        prefix, letters = upcoming
        wait = ctx.wait_days_for(prefix) if ctx.wait_days_for else None
        timing = f" It is sent {wait} day(s) after the email before it (change this in the Schedule tab)." if wait else ""
        st.markdown(f"{humanize_stage(prefix)} is added with one email for each variant ({', '.join(letters)}). Each starts with a **blank subject**, so it continues the same email "
                    f"thread, and with the starter design for you to replace.{timing}")
        if st.button(f"Add {humanize_stage(prefix)}", type="primary", key="html_add_stage_go"):
            files = build_campaign_files(campaign_name, prefix, {letter: {"subject": "", "body": ""} for letter in letters}, fmt=hc.FORMAT_HTML)
            try:
                client.commit_campaign_files_directly(files, f"Add stage {prefix} to {campaign_name} (via Streamlit, by {ctx.current_user()})")
            except GitHubActionsError as exc:
                st.error(f"Failed to add the stage: {exc}")
                return
            set_mode(campaign_name, "edit")
            _flash(campaign_name, f"{humanize_stage(prefix)} added with {len(letters)} variant(s). Choose it in the list on the left to write its subject (or leave it blank) and "
                                  "edit its design. If it does not show yet, refresh in a minute.")
            st.rerun()
