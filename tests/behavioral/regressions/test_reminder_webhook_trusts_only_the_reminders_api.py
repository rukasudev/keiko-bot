"""/reminder obeys reminders-api alone, and only for reminders Keiko created.

Reported in the v1 review (3.1): `POST /v1/webhooks/reminder` never read the HTTP Basic
Auth that `ReminderWebhook` registers on every reminder (user `keiko`, the
`REMINDER_AUTH_PASSWORD`, sent since the integration's first commit). A few anonymous
bytes made Keiko celebrate every birthday of a date, with @everyone where a server asked
for it, as often as the sender liked. A `youtube_notification` with any id went straight
to the authenticated PUT on reminders-api, so a stranger could reschedule Keiko's own
reminders, birthdays included.

Shared behaviour: the one route that both birthday reminders and YouTube renewals reach.
Exposed by: anyone who reads the public repository.

Guaranteed: without the credentials a request is a 401 that does nothing; with them, an id
Keiko did not create does nothing at all (no job, no subscription, no PUT); a renewal acts
on the youtuber Keiko stored, never on what the request says; unsubscribing a youtuber
forgets its renewal, so that id stops being Keiko's; every reminder of a callback runs even
when one fails, and a callback with a failure answers 500; if reminders-api sends such a
callback again (assumed, not verified), every reminder kind can run twice (a birthday
celebrates each member once a year, a renewal subscribes and reschedules again). Keiko owns
the renewal's retry: one that YouTube or the hub cannot take now (a timeout, a Data API
error, a hub 5xx or 429) is moved an hour ahead and does not fail the callback; one whose
channel is gone, or that the hub refuses for good (4xx), is an error and moves four days
ahead; only reminders-api refusing the move fails the callback. Every move is computed in
reminders-api's zone and sends the hour as well as the date. A renewal the hub takes stamps
the youtuber's record, and while it keeps failing the error channel hears once, half a day
before the lease it renewed last ends. A renewal whose youtuber no server follows any more,
paused or not, drops itself instead of subscribing again, whether its id was stored as a
number or as text; it is the one place a renewal is deleted.

Found during the PR review: the unsubscribe deleted the renewal record by `id`, a field the
record does not have, so every renewal Keiko ever created stayed known forever. The final
review found that one reminder failing (a renewal timing out) stopped every reminder after it
in the same callback, a guild's birthday included; answering 200 after such a failure then
meant reminders-api never sent the renewal again, and its channel went silent when the lease
ended. The closing review found that a channel lookup answering nothing, or the hub
answering anything, ended the renewal chain without a retry or a trace in the error channel.
The review of the callback token found that the renewal then relied on reminders-api sending
a callback that answered 500 again, which nobody has verified: without that resend, one hub
5xx ended the youtuber's renewal chain for good, where the base kept it going. Making the
unsubscribe best-effort then needed a renewal that ends itself: an unsubscribe the hub could
not take keeps the renewal, so a failed edit leaves its youtuber renewed. The check of that
pass found that hourly retries could outlast the hub's lease with nothing in the error
channel, that a renewal the hub took was moved with the container's clock and a date only,
and that an edit unsubscribing the old name before failing on the new one still deleted the
renewal of a youtuber still followed.
These tests run on the production log bus (the trace folding handler installed), and pick
the errors they check by their context, so a line another layer adds cannot break them.
"""
import asyncio
import base64
import logging
import subprocess
import sys
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
import requests

from app import logger as logger_module
from app.api import create_api
from app.constants import Commands
from app.data.birthdays import upsert_birthday_config, upsert_birthday_item
from app.data.reminder import insert_reminder
from app.integrations import reminder_webhook
from app.integrations.reminder_webhook import (
    REMINDER_AUTH_USER,
    REMINDER_TIMEZONE,
    ReminderAPIError,
    ReminderWebhook,
)
from app.services import trace as trace_service
from app.services.moderations import update_moderations_by_guild
from app.webhooks import birthday_handler
from tests.mocks.discord import create_member

pytestmark = [
    pytest.mark.behavioral,
    pytest.mark.regression,
    pytest.mark.shared_contract("webhooks"),
]

REPO_ROOT = Path(__file__).resolve().parents[3]
URL = "/v1/webhooks/reminder"
PASSWORD = "reminders-api-password"
BIRTHDAY_REMINDER = 4242
RENEWAL_REMINDER = 7001
MEMBER_ID = 555
CLOCK_START = datetime(2026, 10, 1, 15, 0, tzinfo=ZoneInfo(REMINDER_TIMEZONE))


class RecordingReminders(ReminderWebhook):
    """The real credential check, with every call to reminders-api recorded instead."""

    def __init__(self):
        super().__init__(SimpleNamespace(config=SimpleNamespace(
            WEBHOOK_URL="https://keiko.test/v1/webhooks",
            REMINDER_APPLICATION_ID="1",
            REMINDER_API_KEY="api-key",
            REMINDER_AUTH_PASSWORD=PASSWORD,
        )))
        self.updated = []
        self.moves = []
        self.deleted = []
        self.refusals = 0

    def update_reminder(self, reminder_id, date_tz, rrule=None, timezone=None, time_tz=None):
        if self.refusals:
            self.refusals -= 1
            raise ReminderAPIError(503, "reminders-api is down")
        self.updated.append(reminder_id)
        self.moves.append((str(date_tz), time_tz))
        return {"id": reminder_id}

    def delete_reminder(self, reminder_id):
        self.deleted.append(reminder_id)
        return {}


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    """The moment every renewal is moved from: 15:00 on 2026-10-01 where reminders-api reads it."""
    class Clock:
        moment = CLOCK_START

        @classmethod
        def now(cls, tz=None):
            return cls.moment.astimezone(tz) if tz is not None else cls.moment

    monkeypatch.setattr(reminder_webhook, "datetime", Clock, raising=False)
    return Clock


@pytest.fixture
def traces():
    """Every trace that closed, with `logger.*` folded into it as in production."""
    captured = []
    trace_service.register_sink(captured.append)
    folding = logger_module.TraceFoldingHandler()
    logger_module.logger.addHandler(folding)
    yield captured
    logger_module.logger.removeHandler(folding)


@pytest.fixture
async def keiko(deps, monkeypatch, traces):
    """The real API app and the real scheduler, with Discord and reminders-api faked."""
    deps.bot.loop = asyncio.get_running_loop()
    deps.bot.reminder = RecordingReminders()
    monkeypatch.setattr(birthday_handler, "bot", deps.bot)

    guild_id = str(deps.guild.id)
    channel = deps.guild.text_channels[0]
    create_member(deps.guild, id=MEMBER_ID, name="Tester")
    upsert_birthday_config(guild_id, str(channel.id), True, timezone="America/Sao_Paulo")
    upsert_birthday_item(guild_id, str(MEMBER_ID), "05-12", reminder_id=str(BIRTHDAY_REMINDER))
    update_moderations_by_guild(guild_id, Commands.REMINDERS_BIRTHDAY_KEY, True)
    follow_youtuber(deps, guild_id, "pewdiepie")

    return SimpleNamespace(
        client=create_api("dev").test_client(),
        channel=channel,
        reminders=deps.bot.reminder,
        youtube=deps.youtube,
        traces=traces,
    )


def follow_youtuber(deps, guild_id, youtuber, enabled=True):
    deps.mongo_client.guild.notifications_youtube_video.insert_one({
        "guild_id": guild_id,
        "enabled": enabled,
        "notifications": {"values": [{"youtuber": {"value": youtuber}}]},
    })


def basic(user, password):
    token = base64.b64encode(f"{user}:{password}".encode("utf-8")).decode("ascii")
    return {"Authorization": f"Basic {token}"}


def birthday(reminder_id=BIRTHDAY_REMINDER, notes="05-12"):
    return {"id": reminder_id, "title": Commands.REMINDER_API_TITLE_BIRTHDAY, "notes": notes}


def renewal(reminder_id=RENEWAL_REMINDER, notes="pewdiepie"):
    return {"id": reminder_id, "title": "youtube_notification", "notes": notes}


def notify(keiko, *reminders, headers=None):
    return keiko.client.post(
        URL,
        json={"reminders_notified": list(reminders)},
        headers=basic(REMINDER_AUTH_USER, PASSWORD) if headers is None else headers,
    )


async def settle():
    """Let every job the request handed to the loop run to its end."""
    for _ in range(100):
        await asyncio.sleep(0.01)
        if not [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]:
            return


async def deliver(keiko, *reminders, attempts=3):
    """Send a callback again after a 5xx, a few times at most: what reminders-api would do
    if it resends a failed callback, which nobody has verified."""
    responses = []
    while len(responses) < attempts:
        responses.append(notify(keiko, *reminders))
        await settle()
        if responses[-1].status_code < 500:
            break
    return responses


async def test_a_reminder_without_the_password_is_refused_and_celebrates_nothing(keiko):
    response = notify(keiko, birthday(), headers={})
    await settle()

    assert response.status_code == 401
    assert keiko.channel._sent_messages == [], "an anonymous POST must not reach a server"


@pytest.mark.parametrize("user, password", [
    (REMINDER_AUTH_USER, "guessed"),
    ("someone", PASSWORD),
    (REMINDER_AUTH_USER, ""),
    (REMINDER_AUTH_USER, "sénha"),
], ids=["wrong-password", "wrong-user", "empty-password", "non-ascii-password"])
async def test_a_reminder_with_wrong_credentials_is_refused_and_celebrates_nothing(
    keiko, user, password,
):
    response = notify(keiko, birthday(), headers=basic(user, password))
    await settle()

    assert response.status_code == 401
    assert keiko.channel._sent_messages == []


async def test_a_birthday_reminder_keiko_did_not_create_does_nothing(keiko):
    response = notify(keiko, birthday(reminder_id=9999))
    await settle()

    assert response.status_code == 200
    assert keiko.channel._sent_messages == [], (
        "a reminder id no birthday carries must not celebrate the date it names"
    )


async def test_a_youtube_reminder_keiko_did_not_create_never_reaches_the_reminders_api(keiko):
    insert_reminder(RENEWAL_REMINDER, "youtube_notification", "pewdiepie")
    keiko.youtube.add_channel("UC-pewdiepie", "PewDiePie", custom_url="@pewdiepie")

    response = notify(keiko, renewal(reminder_id=BIRTHDAY_REMINDER))

    assert response.status_code == 200
    assert keiko.reminders.updated == [], (
        "a birthday reminder's id sent as a renewal would reschedule that birthday"
    )
    assert keiko.youtube.subscribe_calls == []


def errors_of(caplog, flow):
    """The error records a flow logged with its context, whatever else was logged."""
    return [
        record for record in caplog.records
        if record.levelno >= logging.ERROR
        and getattr(getattr(record, "context", None), "flow", None) == flow
    ]


def moved_by(move, clock):
    """How far ahead of the clock a move sent the reminder, read in reminders-api's zone."""
    date_tz, time_tz = move
    assert time_tz, "every move sends the hour as well as the date"
    moment = datetime.combine(
        date.fromisoformat(date_tz), time.fromisoformat(time_tz), ZoneInfo(REMINDER_TIMEZONE)
    )
    return moment - clock.moment


def hub_answers(keiko, monkeypatch, status):
    """The hub answering every subscribe with `status`, which the test may change."""
    keiko.youtube.add_channel("UC-pewdiepie", "PewDiePie", custom_url="@pewdiepie")
    monkeypatch.setattr(
        keiko.youtube, "subscribe_to_new_video_event",
        lambda channel_id: SimpleNamespace(status_code=status["code"]),
    )


def lapse_errors(caplog):
    return [
        record for record in errors_of(caplog, "youtube_renewal")
        if "lease_lapses_at" in record.context.extra
    ]


def posted_errors(traces):
    """The error lines of every trace that reaches the log channel."""
    return [
        line["message"]
        for trace in traces if trace.is_noteworthy
        for line in trace.lines if line["levelno"] >= logging.ERROR
    ]


async def test_a_renewal_whose_channel_is_gone_is_an_error_and_still_moves_ahead(
    keiko, caplog, clock,
):
    """A one-shot renewal that is not moved ahead never fires again: the chain would end."""
    insert_reminder(RENEWAL_REMINDER, "youtube_notification", "pewdiepie")

    with caplog.at_level("INFO"):
        response = notify(keiko, renewal())

    assert response.status_code == 200, "sending it again cannot find the channel either"
    assert keiko.reminders.updated == [RENEWAL_REMINDER]
    assert moved_by(keiko.reminders.moves[-1], clock) == timedelta(days=4)
    renewal_errors = errors_of(caplog, "youtube_renewal")
    assert [record.context.extra["youtuber"] for record in renewal_errors] == ["pewdiepie"]


@pytest.mark.parametrize("trouble", ["timeout", "hub-503", "hub-429"])
async def test_a_renewal_youtube_or_the_hub_cannot_take_now_is_tried_again_in_an_hour(
    keiko, monkeypatch, caplog, clock, trouble,
):
    """Keiko owns the retry: whether reminders-api resends a callback that answered 500 is
    not known, and without it one hub 5xx ended the renewal chain for good."""
    insert_reminder(RENEWAL_REMINDER, "youtube_notification", "pewdiepie")
    keiko.youtube.add_channel("UC-pewdiepie", "PewDiePie", custom_url="@pewdiepie")
    if trouble == "timeout":
        def times_out(username):
            raise requests.Timeout("YouTube did not answer in 10 s")

        monkeypatch.setattr(keiko.youtube, "get_channel_id_from_username", times_out)
    else:
        status = int(trouble.split("-")[1])
        monkeypatch.setattr(
            keiko.youtube, "subscribe_to_new_video_event",
            lambda channel_id: SimpleNamespace(status_code=status),
        )

    with caplog.at_level("INFO"):
        response = notify(keiko, renewal())

    assert response.status_code == 200, "the callback did its part, and Keiko tries again"
    assert moved_by(keiko.reminders.moves[-1], clock) == timedelta(hours=1)
    assert errors_of(caplog, "reminder_webhook") == []
    warnings = [
        record.getMessage() for record in caplog.records
        if record.levelno == logging.WARNING and "**pewdiepie**" in record.getMessage()
    ]
    assert len(warnings) == 1, "the retry is a warning that names the youtuber"


async def test_a_hub_that_refuses_the_renewal_is_an_error_and_does_not_loop(
    keiko, monkeypatch, caplog, clock,
):
    insert_reminder(RENEWAL_REMINDER, "youtube_notification", "pewdiepie")
    keiko.youtube.add_channel("UC-pewdiepie", "PewDiePie", custom_url="@pewdiepie")
    monkeypatch.setattr(
        keiko.youtube, "subscribe_to_new_video_event",
        lambda channel_id: SimpleNamespace(status_code=400),
    )

    with caplog.at_level("INFO"):
        response = notify(keiko, renewal())

    assert response.status_code == 200, "a refusal for good would only come back refused"
    assert keiko.reminders.updated == [RENEWAL_REMINDER]
    assert moved_by(keiko.reminders.moves[-1], clock) == timedelta(days=4)
    assert [record.context.extra["status"] for record in errors_of(caplog, "youtube_renewal")] == [
        400
    ]


async def test_a_youtube_renewal_renews_the_youtuber_keiko_stored(keiko):
    insert_reminder(RENEWAL_REMINDER, "youtube_notification", "pewdiepie")
    keiko.youtube.add_channel("UC-pewdiepie", "PewDiePie", custom_url="@pewdiepie")
    keiko.youtube.add_channel("UC-stranger", "Stranger", custom_url="@stranger")

    response = notify(keiko, renewal(notes="stranger"))

    assert response.status_code == 200
    assert keiko.youtube.subscribe_calls == ["UC-pewdiepie"]
    assert keiko.reminders.updated == [RENEWAL_REMINDER]


async def test_a_renewal_that_times_out_does_not_stop_the_birthday_after_it(
    keiko, monkeypatch, clock,
):
    insert_reminder(RENEWAL_REMINDER, "youtube_notification", "pewdiepie")

    def youtube_times_out(username):
        raise requests.Timeout("YouTube did not answer in 10 s")

    monkeypatch.setattr(keiko.youtube, "get_channel_id_from_username", youtube_times_out)

    response = notify(keiko, renewal(), birthday())
    await settle()

    assert response.status_code == 200
    assert len(keiko.channel._sent_messages) == 1, "the birthday after the renewal still runs"
    assert moved_by(keiko.reminders.moves[-1], clock) == timedelta(hours=1)


async def test_a_renewal_reminders_api_refuses_to_move_fails_the_callback(keiko, caplog):
    """The one renewal failure Keiko cannot retry by itself: the reminder did not move."""
    insert_reminder(RENEWAL_REMINDER, "youtube_notification", "pewdiepie")
    keiko.youtube.add_channel("UC-pewdiepie", "PewDiePie", custom_url="@pewdiepie")
    keiko.reminders.refusals = 1

    with caplog.at_level("INFO"):
        response = notify(keiko, renewal(), birthday())
        await settle()

    assert response.status_code >= 500
    assert len(keiko.channel._sent_messages) == 1, "the birthday after the renewal still runs"
    assert [record.context.extra for record in errors_of(caplog, "reminder_webhook")] == [
        {"reminder_id": RENEWAL_REMINDER, "title": "youtube_notification"}
    ]
    assert any(
        f"reminder {RENEWAL_REMINDER} (youtube_notification) failed" in line
        for line in posted_errors(keiko.traces)
    ), "the failure reaches the log channel"


async def test_a_callback_sent_again_celebrates_no_one_twice_and_renews_again(keiko):
    """If reminders-api sends a callback that answered 500 again (assumed, not verified),
    every reminder in it runs again safely."""
    insert_reminder(RENEWAL_REMINDER, "youtube_notification", "pewdiepie")
    keiko.youtube.add_channel("UC-pewdiepie", "PewDiePie", custom_url="@pewdiepie")
    keiko.reminders.refusals = 1

    responses = await deliver(keiko, renewal(), birthday())

    assert [response.status_code >= 500 for response in responses] == [True, False]
    assert keiko.youtube.subscribe_calls == ["UC-pewdiepie", "UC-pewdiepie"], (
        "the hub keeps one subscription per callback and topic and renews its lease"
    )
    assert keiko.reminders.updated == [RENEWAL_REMINDER]
    assert len(keiko.channel._sent_messages) == 1, "nobody is celebrated twice"


@pytest.mark.parametrize("stored_id", [RENEWAL_REMINDER, str(RENEWAL_REMINDER)],
                         ids=["stored-as-a-number", "stored-as-text"])
async def test_a_renewal_for_a_youtuber_no_server_follows_drops_itself(keiko, deps, stored_id):
    """The one place a renewal is deleted: the unsubscribe leaves it, and it ends here."""
    from app.data.reminder import find_reminder_by_id

    deps.mongo_client.guild.notifications_youtube_video.delete_many({})
    insert_reminder(stored_id, "youtube_notification", "pewdiepie")
    keiko.youtube.add_channel("UC-pewdiepie", "PewDiePie", custom_url="@pewdiepie")

    response = notify(keiko, renewal())

    assert response.status_code == 200
    assert keiko.reminders.deleted == [stored_id]
    assert find_reminder_by_id(RENEWAL_REMINDER) is None, "that id stops being Keiko's"
    assert keiko.youtube.subscribe_calls == [] and keiko.reminders.updated == []


async def test_a_paused_server_keeps_its_youtuber_renewed(keiko, deps, clock):
    deps.mongo_client.guild.notifications_youtube_video.delete_many({})
    follow_youtuber(deps, str(deps.guild.id), "pewdiepie", enabled=False)
    insert_reminder(RENEWAL_REMINDER, "youtube_notification", "pewdiepie")
    keiko.youtube.add_channel("UC-pewdiepie", "PewDiePie", custom_url="@pewdiepie")

    response = notify(keiko, renewal())

    assert response.status_code == 200
    assert keiko.youtube.subscribe_calls == ["UC-pewdiepie"], "unpausing finds it still subscribed"
    assert moved_by(keiko.reminders.moves[-1], clock) == timedelta(days=4)


async def test_a_renewal_the_hub_takes_stamps_the_youtubers_record(keiko, monkeypatch, clock):
    from app.data.reminder import find_reminder_by_id

    insert_reminder(RENEWAL_REMINDER, "youtube_notification", "pewdiepie")
    hub_answers(keiko, monkeypatch, {"code": 202})

    notify(keiko, renewal())

    stamped = find_reminder_by_id(RENEWAL_REMINDER)["hub_confirmed_at"]
    assert stamped.astimezone(timezone.utc) == CLOCK_START.astimezone(timezone.utc)


async def test_renewals_that_keep_failing_tell_the_error_channel_once_before_the_lease_ends(
    keiko, monkeypatch, caplog, clock,
):
    """Hourly retries are warnings; a hub that never comes back would lapse every channel."""
    from app.data.reminder import stamp_hub_confirmation

    insert_reminder(RENEWAL_REMINDER, "youtube_notification", "pewdiepie")
    stamp_hub_confirmation("pewdiepie", CLOCK_START.astimezone(timezone.utc))
    hub_answers(keiko, monkeypatch, {"code": 503})
    reported = []

    with caplog.at_level("INFO"):
        for hours in (107, 108, 109, 110):
            clock.moment = CLOCK_START + timedelta(hours=hours)
            notify(keiko, renewal())
            reported.append(len(lapse_errors(caplog)))

    assert reported == [0, 1, 1, 1], "warnings until half a day before, then one error, once"
    [error] = lapse_errors(caplog)
    assert error.context.extra["lease_lapses_at"] == "2026-10-06T18:00:00+00:00"
    assert error.context.extra["youtuber"] == "pewdiepie"


async def test_the_hub_taking_a_renewal_again_clears_the_lapse_report(
    keiko, monkeypatch, caplog, clock,
):
    from app.data.reminder import stamp_hub_confirmation

    insert_reminder(RENEWAL_REMINDER, "youtube_notification", "pewdiepie")
    stamp_hub_confirmation("pewdiepie", CLOCK_START.astimezone(timezone.utc))
    status = {"code": 503}
    hub_answers(keiko, monkeypatch, status)
    reported = []

    with caplog.at_level("INFO"):
        for hours, code in ((108, 503), (109, 202), (109 + 107, 503), (109 + 108, 503)):
            clock.moment = CLOCK_START + timedelta(hours=hours)
            status["code"] = code
            notify(keiko, renewal())
            reported.append(len(lapse_errors(caplog)))

    assert reported == [1, 1, 1, 2], "the next lapse is reported again"


async def test_a_renewal_written_before_the_stamp_counts_from_its_creation(
    keiko, deps, monkeypatch, caplog, clock,
):
    deps.mongo_client.audit.reminders.insert_one({
        "reminder_id": RENEWAL_REMINDER,
        "title": "youtube_notification",
        "value": "pewdiepie",
        "created_at": CLOCK_START.astimezone(timezone.utc),
    })
    hub_answers(keiko, monkeypatch, {"code": 503})
    reported = []

    with caplog.at_level("INFO"):
        for hours in (107, 108):
            clock.moment = CLOCK_START + timedelta(hours=hours)
            notify(keiko, renewal())
            reported.append(len(lapse_errors(caplog)))

    assert reported == [0, 1]


async def test_a_birthday_reminder_with_the_password_celebrates_once(keiko):
    response = notify(keiko, birthday())
    await settle()

    assert response.status_code == 200
    assert len(keiko.channel._sent_messages) == 1
    assert keiko.channel._sent_messages[0].content == "@everyone"


def test_the_webhooks_import_before_the_databases_connect():
    """`__main__` imports every route before `create_app` connects Mongo and Redis.

    A route module that imports `app.data` or `app.services.cache` at the top reads
    `app.mongo_client` before it exists, and the bot does not start.
    """
    result = subprocess.run(
        [sys.executable, "-c", "import app.webhooks"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert result.returncode == 0, result.stderr[-2000:]
