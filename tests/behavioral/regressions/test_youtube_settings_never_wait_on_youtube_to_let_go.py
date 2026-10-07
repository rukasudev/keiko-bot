"""Removing a youtuber or turning YouTube off never waits on YouTube.

Reported in the review of the callback token: once an error answer from the Data API
raised instead of reading as "not found", the YouTube hooks the settings engine runs
before it stores a change raised too. During a quota or key error, or a hub that timed out,
an admin could not remove a youtuber or disable YouTube; at the base both went through.

Shared behaviour: `unsubscribe_youtube_new_video` and `subscribe_youtube_new_video`, the
subscription hooks the YouTube feature hands the settings engine.
Exposed by: a YouTube quota error, a revoked API key, a hub that times out.

The check of the next pass found that an edit whose unsubscribe of the old name went through
and whose subscribe of the new name then failed deleted the renewal of a youtuber still
followed; that removing the last enabled follower of a youtuber a paused server follows
unsubscribed the hub; and that setup ignored the hub's answer, so a youtuber added while
the hub could not take it waited four days for its renewal.

Guaranteed: removing a youtuber and disabling YouTube complete whatever YouTube answers;
the hub's unsubscribe is best-effort, logged as an error with its context when it fails,
and is not sent while another server, paused or not, still follows the youtuber. The
unsubscribe never deletes the renewal: the renewal drops itself at its next run once no
server follows, so the subscription ends with its lease. Setup, adding and editing a
youtuber need the channel, so a failed lookup fails them as a timeout does: the admin is
told the command could not be processed, nothing is saved, and the youtuber still followed
keeps its renewal. Once the channel is found, the hub's answer no longer decides whether
the change is saved: a hub that cannot take it now gets Keiko's renewal an hour later, with
a warning, and one that refuses it is an error with its context.
"""
import logging
from datetime import date, datetime, time, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
import requests

from app.data.reminder import find_reminder_by_value, insert_renewal_reminder
from app.integrations import reminder_webhook
from app.integrations import youtube as youtube_integration
from app.services.utils import ml
from tests.behavioral.golden.paths.common import GUILD_ID, open_manager
from tests.behavioral.golden.paths.notifications_youtube_video import (
    FORM,
    _card,
    _channel,
    _seed,
    _seed_two_entries,
    _submit_messages,
    _submit_youtuber,
)

pytestmark = [
    pytest.mark.behavioral,
    pytest.mark.regression,
    pytest.mark.shared_contract("webhooks"),
]

CLOCK = datetime(2026, 10, 1, 15, 0, tzinfo=ZoneInfo("America/Sao_Paulo"))


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    """The moment a new renewal is scheduled from, where reminders-api reads it."""
    class Clock:
        @classmethod
        def now(cls, tz=None):
            return CLOCK.astimezone(tz) if tz is not None else CLOCK

    monkeypatch.setattr(reminder_webhook, "datetime", Clock, raising=False)


def youtube_answers(deps, trouble):
    """YouTube timing out, refusing with a quota error, or its hub answering 503."""
    if trouble == "hub-503":
        deps.youtube.unsubscribe_from_new_video_event = (
            lambda channel_id: SimpleNamespace(status_code=503)
        )
        return

    def fail(*args, **kwargs):
        if trouble == "timeout":
            raise requests.Timeout("YouTube did not answer in 10 s")
        raise youtube_integration.YoutubeAPIError("YouTube's channels answered 403 (quotaExceeded)")

    deps.youtube.get_channel_id_from_username = fail


def followed(deps):
    document = deps.mongo_client.guild[FORM].find_one({"guild_id": GUILD_ID})
    if document is None:
        return None
    return [item["youtuber"]["value"] for item in document["notifications"]["values"]]


def unsubscribe_errors(caplog):
    return [
        record.context.extra["youtuber"] for record in caplog.records
        if record.levelno >= logging.ERROR
        and getattr(getattr(record, "context", None), "flow", None) == "youtube_unsubscribe"
    ]


def told_it_could_not_be_processed(scenario):
    title = ml("errors.command-generic-error.title", locale="en-us")
    return any(title in (embed.title or "") for embed in scenario.current_message.embeds or [])


@pytest.mark.parametrize("trouble", ["timeout", "data-api-error", "hub-503"])
async def test_removing_a_youtuber_completes_whatever_youtube_answers(
    scenario_factory, deps, caplog, trouble,
):
    scenario = await open_manager(scenario_factory, deps, "en-us", FORM, _seed_two_entries)
    insert_renewal_reminder(7001, "pewdiepie")
    youtube_answers(deps, trouble)

    with caplog.at_level("INFO"):
        await scenario.click("remove")
        await scenario.select_option("notifications$0")

    assert followed(deps) == ["mrbeast"]
    assert unsubscribe_errors(caplog) == ["pewdiepie"]
    assert find_reminder_by_value("pewdiepie") is not None, "kept until it finds nobody follows"


@pytest.mark.parametrize("trouble", [None, "data-api-error"], ids=["hub-takes-it", "data-api-error"])
async def test_the_renewal_an_unsubscribe_leaves_behind_drops_itself(
    scenario_factory, deps, trouble,
):
    """The unsubscribe never deletes the renewal, whatever the hub says; the renewal does."""
    from app.data.reminder import find_reminder_by_id
    from app.webhooks.reminder import renew_youtube_subscription

    scenario = await open_manager(scenario_factory, deps, "en-us", FORM, _seed_two_entries)
    insert_renewal_reminder(7001, "pewdiepie")
    if trouble:
        youtube_answers(deps, trouble)
    await scenario.click("remove")
    await scenario.select_option("notifications$0")

    assert find_reminder_by_value("pewdiepie") is not None, "the unsubscribe leaves it"
    renew_youtube_subscription(7001)

    assert find_reminder_by_id(7001) is None
    deps.bot.reminder.delete_reminder.assert_called_once_with(7001)
    deps.bot.reminder.update_reminder.assert_not_called()


@pytest.mark.parametrize("trouble", ["timeout", "data-api-error", "hub-503"])
async def test_turning_youtube_off_completes_whatever_youtube_answers(
    scenario_factory, deps, caplog, trouble,
):
    scenario = await open_manager(scenario_factory, deps, "en-us", FORM, _seed)
    youtube_answers(deps, trouble)

    with caplog.at_level("INFO"):
        await scenario.click("disable")
        await scenario.submit_confirmation()

    scenario.expect_not_persisted("guild", FORM, {"guild_id": GUILD_ID})
    assert unsubscribe_errors(caplog) == ["pewdiepie"]


async def _set_up(scenario_factory, deps):
    scenario = await _card(scenario_factory, deps, "en-us")
    await _channel(scenario)
    await _submit_youtuber(scenario, "mrbeast")
    await _submit_messages(scenario)
    return scenario, ("done", "confirm")


async def _add(scenario_factory, deps):
    scenario = await open_manager(scenario_factory, deps, "en-us", FORM, _seed)
    await scenario.click("add")
    await _channel(scenario)
    await _submit_youtuber(scenario, "mrbeast")
    await _submit_messages(scenario)
    return scenario, ("done",)


async def _edit(scenario_factory, deps):
    scenario = await open_manager(scenario_factory, deps, "en-us", FORM, _seed)
    await scenario.click("section:notifications$0")
    await _submit_youtuber(scenario, "mrbeast")
    return scenario, ("done",)


CHANGES = {"setup": _set_up, "add": _add, "edit": _edit}
SAVED_BEFORE = {"setup": None, "add": ["pewdiepie"], "edit": ["pewdiepie"]}


@pytest.mark.parametrize("trouble", ["timeout", "data-api-error"])
@pytest.mark.parametrize("change", ["setup", "add", "edit"])
async def test_a_change_that_needs_the_channel_fails_as_a_timeout_does(
    scenario_factory, deps, change, trouble,
):
    deps.bot.config.is_dev = lambda: False
    scenario, commit = await CHANGES[change](scenario_factory, deps)
    youtube_answers(deps, trouble)

    for step in commit:
        if step == "confirm":
            await scenario.confirm()
        else:
            await scenario.click(step)

    assert told_it_could_not_be_processed(scenario)
    assert followed(deps) == SAVED_BEFORE[change], "nothing is saved"


@pytest.mark.parametrize("trouble", ["timeout", "data-api-error"])
async def test_a_failed_edit_leaves_the_youtuber_still_followed_with_its_renewal(
    scenario_factory, deps, trouble,
):
    """The engine unsubscribes the old youtuber before it subscribes the new one."""
    deps.bot.config.is_dev = lambda: False
    insert_renewal_reminder(7001, "pewdiepie")
    scenario, commit = await _edit(scenario_factory, deps)
    youtube_answers(deps, trouble)

    await scenario.click("done")

    assert followed(deps) == ["pewdiepie"]
    assert find_reminder_by_value("pewdiepie") is not None, (
        "a youtuber still followed without a renewal would go quiet within five days"
    )


async def test_an_edit_that_fails_after_the_old_name_was_unsubscribed_keeps_its_renewal(
    scenario_factory, deps,
):
    """The engine unsubscribes the old name before it subscribes the new one; here the hub
    took the first and the lookup of the second failed."""
    deps.bot.config.is_dev = lambda: False
    insert_renewal_reminder(7001, "pewdiepie")
    scenario, commit = await _edit(scenario_factory, deps)
    look_up = deps.youtube.get_channel_id_from_username

    def only_the_old_name(username):
        if username.lower().lstrip("@") == "mrbeast":
            raise youtube_integration.YoutubeAPIError("YouTube's channels did not answer: ReadTimeout")
        return look_up(username)

    deps.youtube.get_channel_id_from_username = only_the_old_name

    await scenario.click("done")

    assert followed(deps) == ["pewdiepie"], "nothing is saved"
    assert find_reminder_by_value("pewdiepie") is not None, (
        "a youtuber still followed keeps its renewal, which subscribes it again"
    )


async def test_removing_a_youtuber_a_paused_server_still_follows_keeps_the_hub_subscribed(
    scenario_factory, deps,
):
    scenario = await open_manager(scenario_factory, deps, "en-us", FORM, _seed_two_entries)
    deps.mongo_client.guild[FORM].insert_one({
        "guild_id": "999",
        "enabled": False,
        "notifications": {"values": [{"youtuber": {"value": "pewdiepie"}}]},
    })
    insert_renewal_reminder(7001, "pewdiepie")

    await scenario.click("remove")
    await scenario.select_option("notifications$0")

    assert followed(deps) == ["mrbeast"]
    assert deps.youtube.unsubscribe_calls == [], "the paused server unpauses still subscribed"
    assert find_reminder_by_value("pewdiepie") is not None


def renewal_created(deps):
    """When the renewal reminder of a new youtuber fires, read in reminders-api's zone."""
    sent = deps.bot.reminder.create_reminder.call_args.args[0]
    assert sent.get("time_tz"), "the hour is sent as well as the date"
    return datetime.combine(
        date.fromisoformat(str(sent["date_tz"])),
        time.fromisoformat(sent["time_tz"]),
        ZoneInfo("America/Sao_Paulo"),
    )


@pytest.mark.parametrize("status", [503, 429])
async def test_a_youtuber_added_while_the_hub_cannot_take_it_is_saved_and_tried_again_in_an_hour(
    scenario_factory, deps, caplog, status,
):
    deps.bot.config.is_dev = lambda: False
    deps.bot.reminder.create_reminder.return_value = {"id": 8001}
    scenario, commit = await _add(scenario_factory, deps)
    deps.youtube.subscribe_to_new_video_event = lambda channel_id: SimpleNamespace(status_code=status)

    with caplog.at_level("INFO"):
        await scenario.click("done")

    assert followed(deps) == ["pewdiepie", "mrbeast"], "the hub's answer does not undo the save"
    assert renewal_created(deps) - CLOCK == timedelta(hours=1), "Keiko's own retry covers it"
    assert find_reminder_by_value("mrbeast") is not None
    assert any(
        record.levelno == logging.WARNING and "**mrbeast**" in record.getMessage()
        for record in caplog.records
    )


async def test_a_youtuber_added_while_the_hub_refuses_it_is_saved_with_an_error(
    scenario_factory, deps, caplog,
):
    deps.bot.config.is_dev = lambda: False
    deps.bot.reminder.create_reminder.return_value = {"id": 8001}
    scenario, commit = await _add(scenario_factory, deps)
    deps.youtube.subscribe_to_new_video_event = lambda channel_id: SimpleNamespace(status_code=400)

    with caplog.at_level("INFO"):
        await scenario.click("done")

    assert followed(deps) == ["pewdiepie", "mrbeast"]
    refusals = [
        record for record in caplog.records
        if record.levelno >= logging.ERROR
        and getattr(getattr(record, "context", None), "flow", None) == "youtube_subscribe"
    ]
    assert [(record.context.extra["youtuber"], record.context.extra["status"]) for record in refusals] == [
        ("mrbeast", 400)
    ]
    assert renewal_created(deps) - CLOCK == timedelta(days=4), "the normal cadence"
