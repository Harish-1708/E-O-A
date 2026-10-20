"""Creating, duplicating and listing HTML campaigns through the real Campaigns page. Every plain-text behaviour is checked beside its HTML twin."""
import contextlib
import os
import re
import sys
from unittest.mock import patch

import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "tests"))          # the engine's own test helpers

import html_campaign_logic as hc  # noqa: E402
from test_campaigns_hub_page import ROWS, _every_test_uses_the_fixture_repo, _clear_streamlit_caches, _open_new, hub, markup, row  # noqa: E402

_AUTOUSE_FIXTURES = (_every_test_uses_the_fixture_repo, _clear_streamlit_caches)        # imported so pytest applies them here exactly as in the hub page tests


def html_row(name, status="draft", **kw):
    return dict(row(name, status, **kw), format="html")


@contextlib.contextmanager
def writes():
    """Replaces GitHub's write calls with recorders: what was committed, and which workflows were started."""
    record = {"commits": [], "workflows": []}

    def commit(self, files, commit_message):
        record["commits"].append({"files": files, "message": commit_message})

    def dispatch(self, workflow, inputs=None, *a, **k):
        record["workflows"].append((workflow, inputs))

    with patch("github_client.GitHubClient.commit_campaign_files_directly", commit), patch("github_client.GitHubClient.dispatch_workflow", dispatch), patch("time.sleep", lambda s: None):
        yield record


def radio(at, key):
    return next(r for r in at.radio if r.key == key)


def _fill_and_create(at, name):
    next(t for t in at.text_input if t.key == "new_campaign_name").set_value(name)
    at.run(timeout=30)
    next(c for c in at.checkbox if c.key == "new_campaign_confirm").set_value(True)
    at.run(timeout=30)
    next(b for b in at.button if b.key == "new_campaign_go").click()
    at.run(timeout=30)


# ================================================================ the dialog

def test_the_new_campaign_dialog_asks_for_the_format_and_starts_on_plain_text_with_no_layout_choice():
    with hub() as (at, _):
        _open_new(at)
        choice = radio(at, "hub_new_campaign_format")
        assert choice.options == ["Plain text", "Designed (HTML)"] and choice.value == "text" and list(at.exception) == []
        assert not any(r.key == "hub_new_campaign_layout" for r in at.radio)
        text = markup(at)
        assert "intro_A.txt" in text and "intro_A.html" not in text and "format: html" not in text


def test_choosing_html_shows_the_starting_layouts_and_what_will_be_created():
    with hub() as (at, _):
        _open_new(at)
        radio(at, "hub_new_campaign_format").set_value("html")
        at.run(timeout=30)
        layout = radio(at, "hub_new_campaign_layout")
        assert layout.options == [hc.STARTER_LAYOUTS[k]["label"] for k in hc.STARTER_LAYOUTS] and layout.value == hc.DEFAULT_LAYOUT
        text = markup(at)
        assert "intro_A.html" in text and "format: html" in text and "Your_Campaign" in text


def test_the_preview_of_what_is_created_follows_the_typed_name_and_cannot_inject_markup():
    with hub() as (at, _):
        _open_new(at)
        next(t for t in at.text_input if t.key == "new_campaign_name").set_value("<script>x</script>")
        at.run(timeout=30)
        assert "<script>x" not in markup(at) and "&lt;script&gt;" in markup(at)


def test_the_dialog_warns_that_the_format_cannot_be_changed_later():
    with hub() as (at, _):
        _open_new(at)
        assert any("cannot be changed later" in c.value for c in at.caption)


def test_the_dialog_always_opens_on_plain_text_even_if_an_html_choice_was_left_behind_from_before():
    with hub(prime={"hub_new_campaign_format": "html", "hub_new_campaign_layout": "announcement"}) as (at, _):
        _open_new(at)
        assert radio(at, "hub_new_campaign_format").value == "text" and not any(r.key == "hub_new_campaign_layout" for r in at.radio)
        radio(at, "hub_new_campaign_format").set_value("html")
        at.run(timeout=30)
        assert radio(at, "hub_new_campaign_layout").value == hc.DEFAULT_LAYOUT


def test_reopening_the_dialog_starts_on_plain_text_again():
    with hub() as (at, _):
        _open_new(at)
        radio(at, "hub_new_campaign_format").set_value("html")
        at.run(timeout=30)
        next(b for b in at.button if b.key == "new_campaign_cancel").click()
        at.run(timeout=30)
        _open_new(at)
        assert radio(at, "hub_new_campaign_format").value == "text"


# ================================================================ creating

def test_creating_a_plain_text_campaign_commits_exactly_the_one_placeholder_file_as_before():
    with writes() as record, hub() as (at, _):
        _open_new(at)
        _fill_and_create(at, "Text_Camp")
        assert list(at.exception) == [] and len(record["commits"]) == 1
        files = record["commits"][0]["files"]
        assert [f["path"] for f in files] == ["templates/Text_Camp/intro_A.txt"]
        assert files[0]["content"].decode().startswith("Subject: Write your subject here\n\nWrite your intro email here.")
        assert any("created with a placeholder Intro" in s.value and "HTML" not in s.value for s in at.success)


def test_creating_an_html_campaign_commits_the_pair_of_files_and_the_settings_file_in_one_save_and_starts_the_sheet_tabs():
    with writes() as record, hub() as (at, _):
        _open_new(at)
        radio(at, "hub_new_campaign_format").set_value("html")
        at.run(timeout=30)
        radio(at, "hub_new_campaign_layout").set_value("announcement")
        at.run(timeout=30)
        _fill_and_create(at, "Spring_Launch")
        assert list(at.exception) == [] and list(at.error) == [] and len(record["commits"]) == 1
        files = {f["path"]: f["content"] for f in record["commits"][0]["files"]}
        assert sorted(files) == ["config/campaigns/Spring_Launch.yaml", "templates/Spring_Launch/intro_A.html", "templates/Spring_Launch/intro_A.txt"]
        assert files["templates/Spring_Launch/intro_A.html"].decode() == hc.starter_html("announcement")
        assert yaml.safe_load(files["config/campaigns/Spring_Launch.yaml"]) == {"format": "html"}
        txt = files["templates/Spring_Launch/intro_A.txt"].decode()
        assert txt.startswith("Subject: Write your subject here\n\n") and txt.rstrip().endswith("{{ footer }}") and "{{ FirstName }}" in txt
        assert record["workflows"] and record["workflows"][0][1] == {"campaign": "Spring_Launch"}
        assert any("created as an HTML campaign" in s.value and "footer" in s.value for s in at.success)


def test_the_files_made_for_a_new_html_campaign_really_load_as_an_html_campaign(tmp_path):
    """Not just the right names: put the exact committed files on disk and load them with the real engine."""
    import outreach
    from test_stage_wait_overrides import _campaign
    with writes() as record, hub() as (at, _):
        _open_new(at)
        radio(at, "hub_new_campaign_format").set_value("html")
        at.run(timeout=30)
        _fill_and_create(at, "Camp")
        files = record["commits"][0]["files"]
    kw = _campaign(tmp_path, None)
    folder = os.path.join(kw["templates_root"], "camp")
    for name in os.listdir(folder):
        os.remove(os.path.join(folder, name))
    for f in files:
        if f["path"].startswith("templates/"):
            open(os.path.join(folder, os.path.basename(f["path"])), "wb").write(f["content"])
        else:
            open(os.path.join(kw["campaigns_dir"], "camp.yaml"), "wb").write(f["content"])
    cfg = outreach.get_campaign("camp", **kw)
    assert cfg["format"] == "html" and [s["name"] for s in cfg["stages"]] == ["intro"]


def test_a_name_that_is_refused_commits_nothing_in_either_format():
    with writes() as record, hub() as (at, _):
        _open_new(at)
        radio(at, "hub_new_campaign_format").set_value("html")
        at.run(timeout=30)
        _fill_and_create(at, "bad name")
        assert record["commits"] == [] and any("Use only letters" in e.value for e in at.error)


# ================================================================ duplicating

def _duplicate(source_files, override, new_name="Copy_Camp"):
    """Runs the Duplicate dialog for a source whose GitHub folder holds `source_files` and whose settings file is `override` (a dict, or None for no file)."""
    @contextlib.contextmanager
    def _ctx():
        with writes() as record:
            def lister(self, path, ref="main"):
                return sorted(source_files)

            def content(self, path, ref="main"):
                name = path.rsplit("/", 1)[-1]
                return yaml.safe_dump(override).encode() if name.endswith(".yaml") else source_files[name].encode()

            def sha(self, path, ref="main"):
                return "abc" if (path.endswith(".yaml") and override is not None) else None

            with patch("github_client.GitHubClient.list_directory_files", lister), patch("github_client.GitHubClient.get_file_content", content), \
                    patch("github_client.GitHubClient.get_file_sha", sha), hub() as (at, _):
                next(b for b in at.button if b.key == "duplicate_Live_17SEP").click()
                at.run(timeout=30)
                next(t for t in at.text_input if t.key == "duplicate_campaign_new_name").set_value(new_name)
                at.run(timeout=30)
                next(c for c in at.checkbox if c.key == "duplicate_campaign_confirm").set_value(True)
                at.run(timeout=30)
                next(b for b in at.button if b.key == "duplicate_campaign_go").click()
                at.run(timeout=30)
                yield at, record
    return _ctx()


def test_duplicating_an_html_campaign_copies_every_text_file_and_every_designed_file_and_its_settings():
    source = {"intro_A.txt": "Subject: S\n\nB", "intro_A.html": "<p>design A</p>", "followup1_A.txt": "Subject: \n\nF", "followup1_A.html": "<p>design F</p>", "notes.md": "x"}
    with _duplicate(source, {"format": "html", "footer": {"business_name": "K", "address": "A"}, "status": "running"}) as (at, record):
        assert list(at.exception) == [] and len(record["commits"]) == 1
        files = {f["path"]: f["content"] for f in record["commits"][0]["files"]}
        assert sorted(p for p in files if p.startswith("templates/")) == ["templates/Copy_Camp/followup1_A.html", "templates/Copy_Camp/followup1_A.txt",
                                                                          "templates/Copy_Camp/intro_A.html", "templates/Copy_Camp/intro_A.txt"]
        assert files["templates/Copy_Camp/intro_A.html"] == b"<p>design A</p>"
        copied = yaml.safe_load(files["config/campaigns/Copy_Camp.yaml"])
        assert copied["format"] == "html" and copied["footer"] == {"business_name": "K", "address": "A"} and copied["status"] == "draft"
        assert any("with 4 template file(s)" in s.value for s in at.success)


def test_duplicating_a_plain_text_campaign_copies_only_the_text_files_even_if_a_stray_html_file_is_there():
    source = {"intro_A.txt": "Subject: S\n\nB", "intro_A.html": "<p>stray</p>", "followup1_A.txt": "Subject: \n\nF"}
    with _duplicate(source, {"status": "running"}) as (at, record):
        assert list(at.exception) == [] and len(record["commits"]) == 1
        paths = sorted(f["path"] for f in record["commits"][0]["files"] if f["path"].startswith("templates/"))
        assert paths == ["templates/Copy_Camp/followup1_A.txt", "templates/Copy_Camp/intro_A.txt"]
        assert any("with 2 template file(s)" in s.value for s in at.success)


def test_duplicating_a_campaign_with_no_settings_file_still_works_and_copies_only_the_text_files():
    with _duplicate({"intro_A.txt": "Subject: S\n\nB", "intro_A.html": "<p>stray</p>"}, None) as (at, record):
        assert list(at.exception) == [] and [f["path"] for f in record["commits"][0]["files"] if f["path"].startswith("templates/")] == ["templates/Copy_Camp/intro_A.txt"]


# ================================================================ the list

def test_with_no_html_campaign_there_is_no_format_filter_and_no_badge_at_all():
    with hub() as (at, _):
        assert not any(g.key == "hub_format" for g in at.get("button_group")) and 'class="hb-fmt' not in markup(at)


def test_once_a_campaign_is_html_every_card_gets_a_badge_and_a_format_filter_with_counts_appears():
    rows = ROWS + [html_row("Spring_Launch")]
    with hub(rows=rows) as (at, _):
        group = next(g for g in at.get("button_group") if g.key == "hub_format")
        assert [o.content for o in group.proto.options] == [f"All formats {len(rows)}", f"Text {len(rows) - 1}", "HTML 1"]
        text = markup(at)
        assert text.count('class="hb-fmt hb-fmt-html"') == 1 and text.count('class="hb-fmt hb-fmt-text"') == len(ROWS)


def test_choosing_the_html_filter_shows_only_html_campaigns_and_text_shows_the_rest():
    rows = ROWS + [html_row("Spring_Launch")]
    with hub(rows=rows) as (at, _):
        next(g for g in at.get("button_group") if g.key == "hub_format").set_value("html")
        at.run(timeout=30)
        assert re.findall(r'hb-name">([^<]*)', markup(at)) == ["Spring_Launch"]
        next(g for g in at.get("button_group") if g.key == "hub_format").set_value("text")
        at.run(timeout=30)
        assert "Spring_Launch" not in re.findall(r'hb-name">([^<]*)', markup(at)) and len(re.findall(r'hb-name">([^<]*)', markup(at))) == len(ROWS)


def test_clearing_the_filters_also_resets_the_format_filter():
    rows = ROWS + [html_row("Spring_Launch")]
    with hub(rows=rows) as (at, _):
        next(g for g in at.get("button_group") if g.key == "hub_format").set_value("html")
        at.run(timeout=30)
        next(t for t in at.text_input if t.key == "hub_search").set_value("zzzz")
        at.run(timeout=30)
        next(b for b in at.button if b.key == "hub_clear_filters").click()
        at.run(timeout=30)
        assert next(g for g in at.get("button_group") if g.key == "hub_format").value in ("all", None) and len(re.findall(r'hb-name">', markup(at))) == len(rows)
