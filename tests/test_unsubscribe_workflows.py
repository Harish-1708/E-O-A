"""The unsubscribe page's two secrets reach exactly the workflows that BUILD emails (a real send, an automatic send and a test email), under the names the engine reads, and no other
workflow: a secret is only passed to where it is used."""
import glob
import os

import pytest
import yaml

WORKFLOWS = os.path.join(os.path.dirname(__file__), "..", ".github", "workflows")
BUILD_EMAILS = {"send_batch.yml", "auto_send.yml", "send_test_email.yml"}


def load(name):
    with open(os.path.join(WORKFLOWS, name), encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def env_blocks(document):
    return [step["env"] for job in document["jobs"].values() for step in job.get("steps", []) if isinstance(step.get("env"), dict)]


@pytest.mark.parametrize("name", sorted(BUILD_EMAILS))
def test_the_workflows_that_build_emails_pass_both_secrets_under_the_names_the_engine_reads(name):
    carrying = [env for env in env_blocks(load(name)) if "UNSUBSCRIBE_URL" in env or "UNSUBSCRIBE_SECRET" in env]
    assert len(carrying) == 1
    assert carrying[0]["UNSUBSCRIBE_URL"] == "${{ secrets.UNSUBSCRIBE_URL }}" and carrying[0]["UNSUBSCRIBE_SECRET"] == "${{ secrets.UNSUBSCRIBE_SECRET }}"
    assert carrying[0]["UNSUBSCRIBE_PAGE_URL"] == "${{ secrets.UNSUBSCRIBE_PAGE_URL }}"
    assert "EMAIL_ACCOUNTS_JSON" in carrying[0]                                  # the very step that sends


@pytest.mark.parametrize("path", sorted(p for p in glob.glob(os.path.join(WORKFLOWS, "*.yml")) if os.path.basename(p) not in BUILD_EMAILS))
def test_no_other_workflow_is_given_the_secrets(path):
    text = open(path, encoding="utf-8").read()
    assert "UNSUBSCRIBE_URL" not in text and "UNSUBSCRIBE_SECRET" not in text and "UNSUBSCRIBE_PAGE_URL" not in text


def test_the_engine_reads_exactly_the_names_the_workflows_pass():
    import unsubscribe_logic as ul
    assert ul.configuration_status({"UNSUBSCRIBE_URL": "https://x.example/p", "UNSUBSCRIBE_SECRET": "s" * 20})[0] is not None
    assert ul.configuration_status({"UNSUBSCRIBE_URL": "https://x.example/p"})[1] != ""


def test_a_missing_secret_arrives_as_nothing_and_leaves_the_page_off():
    import unsubscribe_logic as ul
    assert ul.configuration({"UNSUBSCRIBE_URL": "", "UNSUBSCRIBE_SECRET": ""}) is None                      # what an unset GitHub secret becomes
