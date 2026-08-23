"""The repair tool must prove which database it is about to write to.

It creates reminders on a third-party API and writes ids into production. The
first version of it reported on production while connected to localhost, and
would have said "nothing to repair" about a database it never opened, because
`AppConfig` loads `.env` with `override=True` and the file won the argument.

Nothing here reaches the network or a database: these cover the guards and the
plan, which is where that failure lived.
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tools.keiko.birthdays import repair  # noqa: E402

pytestmark = pytest.mark.unit


# --------------------------------------------------------------------------
# Naming the target, and refusing the wrong one
# --------------------------------------------------------------------------

@pytest.mark.parametrize("url,expected", [
    ("mongodb+srv://user:secret@cluster0.d7h6r.mongodb.net/guild", "cluster0.d7h6r.mongodb.net"),
    ("mongodb://localhost:27017", "localhost:27017"),
    ("", "unknown"),
])
def test_the_host_is_shown_and_the_credential_never_is(url, expected):
    assert repair.mongo_host(url) == expected


def test_the_host_of_a_real_url_leaks_no_password():
    rendered = repair.mongo_host("mongodb+srv://keiko:hunter2@cluster0.net/guild")

    assert "hunter2" not in rendered
    assert "keiko" not in rendered


def test_asking_for_prod_and_getting_localhost_is_refused(monkeypatch):
    """The failure this tool already had, in the shape it had it."""
    monkeypatch.setattr(repair, "mongo_url_from_environment", lambda: "mongodb://localhost:27017")

    with pytest.raises(SystemExit) as exit_info:
        repair.load_for_apply("prod")

    assert "localhost" in str(exit_info.value)


def test_a_missing_url_says_which_variable_to_set(monkeypatch):
    for name in ("MONGO_URL_PROD", "MONGO_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(repair, "REPO_ROOT", "/nonexistent")

    with pytest.raises(SystemExit) as exit_info:
        repair.mongo_url_from_environment()

    assert "MONGO_URL_PROD" in str(exit_info.value)


def test_missing_reminder_secrets_are_named_before_anything_opens(monkeypatch):
    monkeypatch.setattr(
        repair, "mongo_url_from_environment", lambda: "mongodb+srv://x@cluster0.net/guild"
    )
    for name in repair.REMINDER_SECRETS:
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(SystemExit) as exit_info:
        repair.load_for_apply("prod")

    message = str(exit_info.value)
    for name in repair.REMINDER_SECRETS:
        assert name in message


# --------------------------------------------------------------------------
# The plan, which is also exactly what --apply then executes
# --------------------------------------------------------------------------

@pytest.fixture
def orphans(monkeypatch):
    from app.data import birthdays as birthdays_data

    birthdays_data.upsert_birthday_config("g1", channel_id="9", timezone="UTC",
                                          notification_time="14:00")
    for user, date in (("7", "10-28"), ("8", "10-28"), ("9", "03-23")):
        birthdays_data.upsert_birthday_item("g1", user, date, reminder_id=None)
    birthdays_data.upsert_birthday_item("nocfg", "1", "01-01", reminder_id=None)
    return birthdays_data


def test_the_plan_groups_by_guild_and_date_not_by_person(orphans):
    entries = repair.plan()

    dates = sorted(entry["date"] for entry in entries if entry["guild_id"] == "g1")
    assert dates == ["03-23", "10-28"], "one reminder serves a whole date"
    assert next(e for e in entries if e["date"] == "10-28")["people"] == 2


def test_the_plan_carries_the_schedule_that_will_be_used(orphans):
    entry = next(e for e in repair.plan() if e["date"] == "10-28")

    assert entry["timezone"] == "UTC"
    assert entry["notification_time"] == "14:00"
    assert entry["configured"] is True


def test_a_guild_with_no_configuration_is_marked_not_repairable(orphans):
    entry = next(e for e in repair.plan() if e["guild_id"] == "nocfg")

    assert entry["configured"] is False


def test_an_empty_plan_says_so_rather_than_printing_a_header(orphans, capsys):
    repair.describe([])

    assert "Nothing to repair" in capsys.readouterr().out


def test_the_printed_plan_flags_what_will_be_skipped(orphans, capsys):
    repair.describe(repair.plan())

    assert "NO CONFIG" in capsys.readouterr().out
