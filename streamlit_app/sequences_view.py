"""The Sequences tab, laid out like the approved design: the stages on the left, the editor in the middle (one stage and one variant at a
time), the email preview and the pre-save checks on the right.

Every action keeps the logic it always had — Save (one commit for every edit), Add variant (to EVERY stage), Add a follow-up stage, Delete a
variant (from every stage) and Delete the last stage (each checked against GitHub's live structure first) — with the same validation, the same
messages and the same commits. What is new is the layout, and the safeguards that the new layout needs:

* Only one variant's editor is on screen at a time, and Streamlit forgets anything typed into an input that is not drawn. So what you type is
  also kept in a DRAFT store, and moving between stages or variants never loses an unsaved edit. A draft is dropped (and you are told) if the
  saved text changed since you started it, so saving can never overwrite newer text with an old copy.
* The 'I understand' boxes for deleting are tied to the specific variant or stage, so a tick left over from one can never confirm another.
"""
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

import streamlit as st

import outreach
import sequences_html as H
import sequences_view_logic as L
from campaign_builder import TOTAL_STAGE_COUNT, build_campaign_files, next_stage_from_stages, validate_variant_content
from github_client import GitHubActionsError
from leads_logic import humanize_stage
from sequences_logic import (
    build_new_variant_files_for_all_stages, build_stage_deletion_paths, build_variant_deletion_paths, build_variant_edit_file, can_delete_stage,
    can_delete_variant, has_content_changed, next_available_variant_letter, validate_new_variant_contents,
)

STATE_KEY = "seq_state"


@dataclass
class SequencesContext:
    """What the tab needs from the page: the campaign, its leads and performance, and the page's own readers and writers."""
    campaign_cfg: Dict
    leads: List[Dict]
    perf: Optional[Dict]
    just_arrived: bool
    get_client: Callable
    fetch_structure: Callable          # (client, campaign_name) -> (stages, variants), read live from GitHub
    fetch_content: Callable            # (client, campaign_name, prefix, variant) -> {"subject", "body"}, read live from GitHub
    current_user: Callable


# ---------------------------------------------------------------- state and callbacks

def _campaign_state(campaign_name: str) -> Dict:
    everything = st.session_state.setdefault(STATE_KEY, {})
    return everything.setdefault(campaign_name, {"stage": 0, "mode": "edit", "drafts": {}, "flash": None})


def _pick_stage(campaign_name: str, index: int) -> None:
    state = _campaign_state(campaign_name)
    state["stage"], state["mode"] = index, "edit"


def _set_mode(campaign_name: str, mode: str) -> None:
    _campaign_state(campaign_name)["mode"] = mode


def _discard(campaign_name: str) -> None:
    """Throw away every unsaved edit and put each typed value back to the saved text (the inputs forget what was typed)."""
    state = _campaign_state(campaign_name)
    for draft in list(state["drafts"].values()):
        for kind in ("unlock", "subject", "body"):
            st.session_state.pop(f"{kind}_{draft['prefix']}_{draft['variant']}", None)
    state["drafts"].clear()


def _insert_placeholder(body_key: str, pick_key: str) -> None:
    token = st.session_state.get(pick_key)
    if token:
        st.session_state[body_key] = (st.session_state.get(body_key) or "") + token
        st.session_state[pick_key] = None


# ---------------------------------------------------------------- the whole tab

def render_sequences_tab(ctx: SequencesContext) -> None:
    campaign_name = ctx.campaign_cfg["_campaign_name"]
    if ctx.just_arrived:
        st.session_state.get(STATE_KEY, {}).pop(campaign_name, None)             # a fresh visit starts fresh: no old drafts, no half-open form
    st.markdown(f"<style>{H.SEQUENCES_CSS}</style>", unsafe_allow_html=True)

    try:
        client = ctx.get_client()
        stages, variants = ctx.fetch_structure(client, campaign_name)
    except Exception as exc:  # noqa: BLE001
        st.error(f"Couldn't read templates for '{campaign_name}': {exc}")
        return

    # Pre-load every template file in parallel so the reads further down find them already cached (best-effort; a no-op when caching is off).
    client.warm_file_cache([f"templates/{campaign_name}/{s['template_prefix']}_{v}.txt" for s in stages for v in variants])

    state = _campaign_state(campaign_name)
    state["stage"] = max(0, min(int(state.get("stage") or 0), len(stages) - 1))
    left, middle, right = st.columns([1.05, 2.15, 1.65], gap="large")
    with left:
        _render_stage_list(ctx, client, campaign_name, stages, variants, state)
    with middle:
        if state["mode"] == "add_variant":
            current = _render_add_variant(ctx, campaign_name, stages, variants, state)
        elif state["mode"] == "add_stage":
            current = _render_add_stage(ctx, campaign_name, stages, variants, state)
        else:
            current = _render_editor(ctx, client, campaign_name, stages, variants, state)
    with right:
        if current is not None:
            _render_preview(ctx, current)


# ---------------------------------------------------------------- left: the stages

def _render_stage_list(ctx, client, campaign_name, stages, variants, state) -> None:
    st.markdown(H.section_label_html("Stages"), unsafe_allow_html=True)
    cards = L.stage_cards(stages, ctx.campaign_cfg.get("stages"), len(variants))
    for index, card in enumerate(cards):
        with st.container(key=f"sq_stage_{index}"):
            st.markdown(H.stage_card_html(card, index == state["stage"] and state["mode"] == "edit"), unsafe_allow_html=True)
            st.button(card["title"], key=f"sq_pick_{index}", on_click=_pick_stage, args=(campaign_name, index))

    next_stage = next_stage_from_stages(stages, variants)
    with st.container(key="sq_add_stage"):
        st.button("＋ Add a follow-up stage" if next_stage else "＋ Add a follow-up stage (none left)", key="sq_add_stage_button", use_container_width=True,
                  disabled=next_stage is None, on_click=_set_mode, args=(campaign_name, "add_stage"))

    # ---------- Delete the last stage (stages must stay contiguous) ----------
    with st.expander("🗑️ Delete the last stage"):
        last_stage = stages[-1]
        can_delete_s, block_reason_s = can_delete_stage(stages, last_stage["template_prefix"])
        if can_delete_s:
            st.warning(f"This permanently removes '{last_stage['name']}' — all {len(variants)} "
                       "variant file(s) for it — not recoverable from this app.")
            confirm_stage_delete = st.checkbox(f"I understand, delete {last_stage['name']}", key=f"confirm_delete_stage_{last_stage['template_prefix']}")
            if st.button(f"Delete {last_stage['name']}", disabled=not confirm_stage_delete, key="delete_stage_button"):
                try:
                    live_stages, live_variants = ctx.fetch_structure(ctx.get_client(), campaign_name)
                    live_last_stage = live_stages[-1]
                    if live_last_stage["template_prefix"] != last_stage["template_prefix"]:
                        st.error(
                            f"The live structure on GitHub shows '{live_last_stage['name']}' as the last "
                            f"stage right now, not '{last_stage['name']}' — this page's view was out of "
                            "date. Refresh and try again rather than deleting based on a stale view."
                        )
                    else:
                        paths = build_stage_deletion_paths(campaign_name, last_stage["template_prefix"], live_variants)
                        delete_client = ctx.get_client()
                        for path in paths:
                            delete_client.delete_file(path, message=f"Delete stage {last_stage['name']} from "
                                                                    f"{campaign_name} (via Streamlit, by {ctx.current_user()})")
                        st.success(f"'{last_stage['name']}' deleted. May take a minute to actually reflect "
                                   "here while the app redeploys.")
                except outreach.ConfigError as exc:
                    st.error(f"Couldn't verify the campaign's current structure before deleting — nothing "
                             f"was changed: {exc}")
                except GitHubActionsError as exc:
                    st.error(f"Failed to delete stage: {exc}")
        else:
            st.info(block_reason_s)


# ---------------------------------------------------------------- middle: the editor

def _render_editor(ctx, client, campaign_name, stages, variants, state) -> Optional[Dict]:
    stage_index = state["stage"]
    prefix = stages[stage_index]["template_prefix"]
    is_first_stage = stage_index == 0
    drafts: Dict = state["drafts"]
    variant_key = f"sq_variant_{campaign_name}"
    if st.session_state.get(variant_key) not in variants:
        st.session_state[variant_key] = variants[0]                                  # first visit, or the chosen variant was deleted
    variant = st.session_state[variant_key]
    unlock_key, subject_key, body_key = f"unlock_{prefix}_{variant}", f"subject_{prefix}_{variant}", f"body_{prefix}_{variant}"
    draft = drafts.get(L.draft_key(prefix, variant))

    try:
        original = ctx.fetch_content(client, campaign_name, prefix, variant)
    except Exception as exc:  # noqa: BLE001
        original = None
        load_error = f"{prefix}_{variant}: couldn't load — {exc}"

    # An edit started on text that has since changed on GitHub must not be saved over the newer text. Checked BEFORE the inputs are drawn, so
    # they can start again from what is saved now.
    stale_notice = None
    if original is not None and draft is not None and tuple(draft["base"]) != (original["subject"], original["body"]):
        drafts.pop(L.draft_key(prefix, variant), None)
        for kind in ("unlock", "subject", "body"):
            st.session_state.pop(f"{kind}_{prefix}_{variant}", None)
        draft = None
        stale_notice = f"Set aside your unsaved edit to {prefix}_{variant}: what is saved there is no longer what you started from."

    with st.container(key="sq_editor"):
        flash = state.pop("flash", None)
        if flash:
            (st.success if flash[0] == "success" else st.error)(flash[1])
        if stale_notice:
            st.warning(stale_notice)
        editing_now = bool(st.session_state.get(unlock_key, draft is not None))
        if original is not None:
            with st.container(key="sq_unlock"):
                unlocked = st.checkbox("🔓 Unlocked" if editing_now else "🔒 Locked", key=unlock_key, value=draft is not None)
        st.markdown(H.editor_title_html(humanize_stage(prefix)), unsafe_allow_html=True)
        with st.container(key="sq_variantbar"):
            st.radio("Variant", variants, key=variant_key, horizontal=True, label_visibility="collapsed", format_func=lambda letter: f"Variant {letter}")
            with st.container(key="sq_add_variant"):
                letter = next_available_variant_letter(variants)
                st.button("＋ Add variant", key="sq_add_variant_button", disabled=not letter, on_click=_set_mode, args=(campaign_name, "add_variant"))
            if not letter:
                st.caption("All 4 variants (A–D) in use — the maximum.")
        if original is None:
            st.warning(load_error)
            return None

        rows = (ctx.perf or {}).get("variant_stage_rows") or []
        st.markdown(H.stats_chips_html(L.variant_stats(rows, prefix, variant), f"{humanize_stage(prefix)} · Variant {variant}"), unsafe_allow_html=True)

        subject_label = "Subject (required — this is the first stage)" if is_first_stage else "Subject (blank continues the previous thread)"
        subject = st.text_input(subject_label, value=draft["subject"] if draft else original["subject"], key=subject_key, disabled=not unlocked)
        body = st.text_area("Body", value=draft["body"] if draft else original["body"], key=body_key, height=280, disabled=not unlocked)
        L.record_draft(drafts, prefix, variant, original, subject, body, unlocked and has_content_changed(original, subject, body))

        tokens = L.placeholder_tokens(ctx.leads, _system_columns())
        st.markdown(H.placeholders_html(), unsafe_allow_html=True)
        pick_key = f"sq_insert_{prefix}_{variant}"
        st.pills("Insert a placeholder", tokens, key=pick_key, label_visibility="collapsed", disabled=not unlocked, on_change=_insert_placeholder, args=(body_key, pick_key))

        pending = _settle(ctx, client, campaign_name, stages, variants, drafts, prefix, variant, original)
        errors = []
        for p_prefix, p_variant, p_subject, p_body in pending:
            error = validate_variant_content(p_subject, p_body, is_first_stage=(p_prefix == stages[0]["template_prefix"]))
            if error:
                errors.append(f"{p_prefix}_{p_variant}: {error}")
        for message in errors:
            st.error(message)

        with st.container(key="sq_actions"):
            with st.container(key="sq_save"):
                save_clicked = st.button(f"💾 Save changes ({len(pending)})", key="sq_save_button", type="primary", disabled=not pending or bool(errors))
            with st.container(key="sq_discard"):
                st.button("↺ Discard", key="sq_discard_button", disabled=not pending, on_click=_discard, args=(campaign_name,))
            with st.container(key="sq_delete_variant"):
                with st.popover("🗑 Delete variant"):
                    _render_delete_variant(ctx, campaign_name, stages, variants, variant)

        if save_clicked:
            try:
                files = [build_variant_edit_file(campaign_name, p_prefix, p_variant, p_subject, p_body) for p_prefix, p_variant, p_subject, p_body in pending]
                save_client = ctx.get_client()
                save_client.commit_campaign_files_directly(
                    files=files,
                    commit_message=f"Edit {len(files)} template(s) in {campaign_name} (via Streamlit, by {ctx.current_user()})",
                )
                for p_prefix, p_variant, _, _ in pending:
                    drafts.pop(L.draft_key(p_prefix, p_variant), None)
                st.success(f"Saved {len(files)} template(s). May take a minute to actually reflect here "
                           "while the app redeploys — the change is live for sending immediately either way.")
            except GitHubActionsError as exc:
                st.error(f"Save failed: {exc}")

    return {"subject": subject, "body": body, "is_first_stage": is_first_stage}


def _system_columns() -> List[str]:
    columns = list(outreach.MASTER_COLUMNS)
    for index in range(TOTAL_STAGE_COUNT):
        columns.extend(outreach.stage_field_names(index).values())
    return columns


def _settle(ctx, client, campaign_name, stages, variants, drafts, prefix, variant, original) -> List:
    """The edits really waiting to be saved: the one on screen plus any kept from other stages and variants, each checked against the text that is
    saved right now. A draft whose saved text changed, or whose variant is gone, is set aside and you are told."""
    originals = {(prefix, variant): original}
    exists = {(stage["template_prefix"], letter) for stage in stages for letter in variants}
    for draft in list(drafts.values()):
        key = (draft["prefix"], draft["variant"])
        if key in originals or key not in exists:                  # (a variant or stage that is gone has no file to read; its edit is simply set aside)
            continue
        try:
            originals[key] = ctx.fetch_content(client, campaign_name, draft["prefix"], draft["variant"])
        except Exception as exc:  # noqa: BLE001
            st.warning(f"{draft['prefix']}_{draft['variant']}: couldn't load — {exc}")
    pending, dropped = L.settle_drafts(drafts, originals, has_content_changed)
    for key in dropped:
        st.warning(f"Set aside your unsaved edit to {key.replace('|', '_')}: what is saved there is no longer what you started from.")
    return pending


def _render_delete_variant(ctx, campaign_name, stages, variants, variant) -> None:
    """Delete the variant on screen from EVERY stage (variants are campaign-wide, matching how add works)."""
    can_delete_v, block_reason_v = can_delete_variant(variants, variant)
    if not can_delete_v:
        st.info(block_reason_v)
        return
    st.warning(f"This permanently removes variant {variant} from EVERY stage "
               f"({len(stages)} file(s)) — not recoverable from this app.")
    confirm_variant_delete = st.checkbox(f"I understand, delete variant {variant}", key=f"confirm_delete_variant_{variant}")
    if st.button(f"Delete Variant {variant}", disabled=not confirm_variant_delete, key="delete_variant_button"):
        try:
            client = ctx.get_client()
            live_stages, live_variants = ctx.fetch_structure(client, campaign_name)
            if variant not in live_variants:
                st.error(
                    f"The live structure on GitHub no longer has variant '{variant}' — "
                    "this page's view was out of date. Refresh and try again."
                )
            else:
                paths = build_variant_deletion_paths(campaign_name, live_stages, variant)
                for path in paths:
                    client.delete_file(path, message=f"Delete variant {variant} from "
                                                     f"{campaign_name} (via Streamlit, by {ctx.current_user()})")
                st.success(f"Variant {variant} deleted from all {len(paths)} stage(s). May "
                           "take a minute to actually reflect here while the app redeploys.")
        except outreach.ConfigError as exc:
            st.error(f"Couldn't verify the campaign's current structure before deleting — nothing "
                     f"was changed: {exc}")
        except GitHubActionsError as exc:
            st.error(f"Failed to delete variant: {exc}")


# ---------------------------------------------------------------- middle: adding a variant or a stage

def _render_add_variant(ctx, campaign_name, stages, variants, state) -> None:
    """Add a variant to EVERY stage at once (variants are campaign-wide)."""
    next_letter = next_available_variant_letter(variants)
    with st.container(key="sq_editor"):
        st.markdown(H.editor_title_html(f"Add variant {next_letter}" if next_letter else "Add variant"), unsafe_allow_html=True)
        with st.container(key="sq_cancel_variant"):
            st.button("Cancel", key="sq_cancel_variant_button", on_click=_set_mode, args=(campaign_name, "edit"))
        if not next_letter:
            st.info("Every stage already has all 4 variants (A–D) — that's the maximum.")
            return None
        st.caption(f"Variant {next_letter} needs content for EVERY existing stage — variants are "
                   "campaign-wide, not per-stage, so this adds it everywhere at once.")
        contents_by_stage = {}
        for stage in stages:
            prefix = stage["template_prefix"]
            st.markdown(f"**{stage['name']}**")
            # Keyed by campaign AND variant letter as well as stage, so what was typed for an earlier attempt (adding B, then later C, or another
            # campaign that needs the same letter) can never carry over into this one.
            subject = st.text_input("Subject", key=f"newvariant_subject_{campaign_name}_{prefix}_{next_letter}")
            body = st.text_area("Body", height=120, key=f"newvariant_body_{campaign_name}_{prefix}_{next_letter}")
            contents_by_stage[prefix] = {"subject": subject, "body": body}

        if st.button(f"Add Variant {next_letter}", type="primary"):
            errors = validate_new_variant_contents(stages, contents_by_stage)
            if errors:
                for message in errors:
                    st.error(message)
            else:
                try:
                    files = build_new_variant_files_for_all_stages(campaign_name, stages, next_letter, contents_by_stage)
                    client = ctx.get_client()
                    client.commit_campaign_files_directly(
                        files=files,
                        commit_message=f"Add variant {next_letter} to {campaign_name} (via Streamlit, by {ctx.current_user()})",
                    )
                    state["flash"] = ("success", f"Variant {next_letter} added to all {len(stages)} stage(s). May take a "
                                                 "minute to actually reflect here while the app redeploys — it's live for "
                                                 "sending immediately either way.")
                    state["mode"] = "edit"
                    st.session_state[f"sq_variant_{campaign_name}"] = next_letter
                    st.rerun()
                except GitHubActionsError as exc:
                    st.error(f"Failed to add variant: {exc}")
    return None


def _render_add_stage(ctx, campaign_name, stages, variants, state) -> None:
    """Add the next follow-up stage (the same logic as the New Campaign 'Add Stage'), computed from the LIVE structure just read."""
    next_stage = next_stage_from_stages(stages, variants)
    with st.container(key="sq_editor"):
        st.markdown(H.editor_title_html("Add a follow-up stage"), unsafe_allow_html=True)
        with st.container(key="sq_cancel_stage"):
            st.button("Cancel", key="sq_cancel_stage_button", on_click=_set_mode, args=(campaign_name, "edit"))
        if next_stage is None:
            st.info(f"This campaign already has all {TOTAL_STAGE_COUNT} stages.")
            return None
        stage_prefix, required_variants = next_stage
        st.write(f"**Next stage:** `{stage_prefix}` · **Required variants:** {', '.join(required_variants)}")
        variant_inputs = {}
        for letter in required_variants:
            st.markdown(f"**Variant {letter}**")
            # Keyed by campaign AND stage as well as letter, so nothing typed for an earlier follow-up (or another campaign) carries over.
            subject = st.text_input("Subject (leave blank to continue the previous thread)", key=f"followup_subject_{campaign_name}_{stage_prefix}_{letter}")
            body = st.text_area("Body", height=120, key=f"followup_body_{campaign_name}_{stage_prefix}_{letter}")
            variant_inputs[letter] = {"subject": subject, "body": body}

        if st.button(f"Add {stage_prefix}", type="primary"):
            errors = []
            for letter, content in variant_inputs.items():
                content_error = validate_variant_content(content["subject"], content["body"], is_first_stage=False)
                if content_error:
                    errors.append(f"Variant {letter}: {content_error}")
            if errors:
                for message in errors:
                    st.error(message)
            else:
                try:
                    files = build_campaign_files(campaign_name, stage_prefix, variant_inputs)
                    client = ctx.get_client()
                    client.commit_campaign_files_directly(
                        files=files,
                        commit_message=f"Add {stage_prefix} to {campaign_name} (via Streamlit, by {ctx.current_user()})",
                    )
                    state["flash"] = ("success", f"'{stage_prefix}' added. May take a minute to actually reflect here while "
                                                 "the app redeploys — it's live for sending immediately either way.")
                    state["mode"], state["stage"] = "edit", len(stages)
                    st.rerun()
                except GitHubActionsError as exc:
                    st.error(f"Failed to add stage: {exc}")
    return None


# ---------------------------------------------------------------- right: the preview and the checks

def _render_preview(ctx, current: Dict) -> None:
    leads = ctx.leads
    subject, body, is_first_stage = current["subject"], current["body"], current["is_first_stage"]
    campaign_name = ctx.campaign_cfg["_campaign_name"]
    lead: Optional[Dict] = None
    if leads:
        options = L.lead_options(leads)
        pick_key = f"sq_preview_lead_{campaign_name}"
        if st.session_state.get(pick_key) not in options:
            st.session_state[pick_key] = options[0]
        chosen = st.session_state[pick_key]
        st.markdown(H.section_label_html("Preview — using your first lead" if chosen == options[0] else "Preview — using the lead you picked"), unsafe_allow_html=True)
        chosen = st.selectbox("Preview lead", options, key=pick_key, label_visibility="collapsed")
        lead = leads[options.index(chosen)]
        shown_subject = outreach.render_text(subject, lead) if subject.strip() else ("(subject is required for the first stage)" if is_first_stage else "(blank — continues the previous thread)")
        sender = str(lead.get("SenderAccount") or "").strip()
        st.markdown(H.preview_html(f"You ({sender})" if sender else "You", L.lead_label(lead).split(" <")[0], shown_subject, outreach.render_text(body, lead)), unsafe_allow_html=True)
    else:
        st.markdown(H.section_label_html("Preview"), unsafe_allow_html=True)
        st.caption("No leads yet, so the preview shows your template as written.")
        st.markdown(H.preview_html("You", "your lead", subject or "(blank)", body), unsafe_allow_html=True)
    st.markdown(H.checks_html(L.check_items(subject, body, lead, is_first_stage)), unsafe_allow_html=True)
