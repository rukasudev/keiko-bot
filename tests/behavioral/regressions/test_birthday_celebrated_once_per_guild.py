"""A birthday reminder celebrates its own birthdays, once a year, guild by guild.

Reported in the v1 review (3.4), from the production log of 10/05: the same reminder ran
at 05:45 with one server and at 15:00 with two. Reminders are created per guild and date,
each at its guild's timezone and hour, but the webhook celebrated the whole date in every
guild. With K guilds on one date each member got K celebrations (and K @everyone), some at
another guild's hour; a reminder delivered twice celebrated twice; and one guild's 403
stopped every guild after it, because the whole date ran inside one `try`.

Shared behaviour: `process_birthday_webhook`, the job every birthday reminder runs, over
`reminders.birthdays`, which the form, `/birthday` and the reconcile pass also write.
Exposed by: two servers sharing a date in production.

Guaranteed: a reminder celebrates only the items that carry its id; a member is celebrated
at most once per guild per year, the year of the birthday being celebrated where the guild
is, recorded on the member's own item once the message is drawn and just before it is
posted (a new field: v0.9.0 neither reads nor erases it); a guild Discord refuses, or whose
channel is gone, is counted and the next one still celebrates; a member whose message
cannot be drawn or sent is counted and the next member still gets theirs; nothing is sent
before the bot can see its guilds; an unexpected failure is logged as an error with its
context, never as a missing server.

Found during the PR review: a member whose message could not be drawn was still spent for
the year and stopped the rest of the guild; any other Discord error on one member stopped
the rest of that guild; a 12-31 reminder delivered after local midnight spent the next
year; a reminder arriving while the bot was not ready yet ran against guilds it could not
see. The final review found that a send Discord failed on was only a warning, and that a
`KeyError` raised anywhere in a guild was reported as a missing server. The closing review
found that a delivery arriving a little early (a stale time zone on the reminder) counted
for the previous year, so the delivery on the day celebrated the member again.
"""
import asyncio
import logging
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import discord
import pytest

from app.constants import Commands
from app.data.birthdays import (
    find_birthday_item,
    upsert_birthday_config,
    upsert_birthday_item,
)
from app.services.moderations import update_moderations_by_guild
from app.webhooks import birthday_handler
from tests.mocks.discord import create_guild, create_member

pytestmark = [
    pytest.mark.behavioral,
    pytest.mark.regression,
    pytest.mark.shared_contract("birthday_reminders"),
]

FIRST_GUILD = 1001
SECOND_GUILD = 1002
TIMEZONE = "America/Sao_Paulo"


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    """The moment the birthday job reads: 08:00 on 2026-05-12 where the guilds are."""
    class Clock:
        moment = datetime(2026, 5, 12, 8, 0, tzinfo=ZoneInfo(TIMEZONE))

        @classmethod
        def now(cls, tz=None):
            return cls.moment.astimezone(tz) if tz is not None else cls.moment

    monkeypatch.setattr(birthday_handler, "datetime", Clock)
    return Clock


@pytest.fixture
def guilds(deps, monkeypatch):
    """Two servers the bot is in, each with its own birthday channel."""
    first = create_guild(id=FIRST_GUILD, name="First", channels=["birthdays"])
    second = create_guild(id=SECOND_GUILD, name="Second", channels=["parties"])
    by_id = {FIRST_GUILD: first, SECOND_GUILD: second}
    deps.bot.get_guild = lambda guild_id: by_id.get(int(guild_id))
    monkeypatch.setattr(birthday_handler, "bot", deps.bot)
    return SimpleNamespace(first=first, second=second)


def register(guild, member_id, reminder_id, mm_dd="05-12"):
    create_member(guild, id=member_id, name=f"Member {member_id}")
    upsert_birthday_config(
        str(guild.id),
        str(guild.text_channels[0].id),
        False,
        timezone=TIMEZONE,
        notification_time="08:00",
    )
    upsert_birthday_item(str(guild.id), str(member_id), mm_dd, reminder_id=reminder_id)
    update_moderations_by_guild(str(guild.id), Commands.REMINDERS_BIRTHDAY_KEY, True)


def celebrations(guild):
    return guild.text_channels[0]._sent_messages


def stored_year(member_id):
    return find_birthday_item(str(FIRST_GUILD), str(member_id))["celebrated_year"]


def refuse_messages(guild):
    forbidden = discord.Forbidden(
        SimpleNamespace(status=403, reason="Forbidden"), "Missing Permissions"
    )
    guild.text_channels[0]._send.side_effect = forbidden


def delete_channel(guild):
    upsert_birthday_config(str(guild.id), "999", False, timezone=TIMEZONE)


def fail_the_first_message(guild, error):
    """Discord fails the first message sent to the guild's channel and takes the next ones."""
    channel = guild.text_channels[0]
    deliver = channel._handle_send
    attempts = []

    async def send(*args, **kwargs):
        attempts.append(kwargs)
        if len(attempts) == 1:
            raise error
        return await deliver(*args, **kwargs)

    channel._send.side_effect = send


async def fire(reminder_id, mm_dd="05-12"):
    await birthday_handler.process_birthday_webhook(reminder_id, mm_dd)


def errors_in(caplog):
    """The errors the birthday job logged with its context, whatever else was logged."""
    return [
        record for record in caplog.records
        if record.levelno >= logging.ERROR
        and getattr(getattr(record, "context", None), "flow", None) == "birthday_webhook"
    ]


async def test_two_guilds_on_one_date_each_get_one_celebration_from_their_own_reminder(
    guilds,
):
    register(guilds.first, 555, "4001")
    register(guilds.second, 777, "4002")

    await fire("4001")

    assert len(celebrations(guilds.first)) == 1
    assert celebrations(guilds.second) == [], (
        "the first guild's reminder fires at the first guild's hour"
    )

    await fire("4002")

    assert len(celebrations(guilds.first)) == 1
    assert len(celebrations(guilds.second)) == 1


async def test_the_same_reminder_twice_in_one_year_celebrates_once(guilds):
    register(guilds.first, 555, "4001")

    await fire("4001")
    await fire("4001")

    assert len(celebrations(guilds.first)) == 1


async def test_an_edit_after_the_celebration_keeps_the_member_celebrated(guilds):
    """v0.9.0 and this version both rewrite the item with `$set`; neither may erase the key."""
    register(guilds.first, 555, "4001")
    await fire("4001")

    upsert_birthday_item(
        str(FIRST_GUILD),
        "555",
        "05-12",
        message={"mode": "custom", "title": "Hey", "content": "Happy day!"},
    )
    await fire("4001")

    assert len(celebrations(guilds.first)) == 1


async def test_a_new_year_celebrates_again(guilds, deps):
    register(guilds.first, 555, "4001")
    deps.mongo_client.reminders.birthdays.update_one(
        {"guild_id": str(FIRST_GUILD), "user_id": "555"},
        {"$set": {"celebrated_year": 2025}},
    )

    await fire("4001")

    assert len(celebrations(guilds.first)) == 1
    assert stored_year(555) == 2026


async def test_a_new_years_eve_birthday_delivered_after_midnight_counts_for_its_own_year(
    guilds, clock,
):
    """The year is the birthday's, not the clock's: 00:30 on 01-01 still celebrates 12-31."""
    register(guilds.first, 555, "4031", mm_dd="12-31")

    clock.moment = datetime(2027, 1, 1, 0, 30, tzinfo=ZoneInfo(TIMEZONE))
    await fire("4031", "12-31")
    clock.moment = datetime(2027, 12, 31, 8, 0, tzinfo=ZoneInfo(TIMEZONE))
    await fire("4031", "12-31")

    assert len(celebrations(guilds.first)) == 2, "the late delivery did not spend 2027"
    assert stored_year(555) == 2027


async def test_a_birthday_delivered_early_counts_for_its_own_year(guilds, clock):
    """A reminder carrying a stale time zone can arrive the evening before the day."""
    register(guilds.first, 555, "4031", mm_dd="12-31")

    clock.moment = datetime(2026, 12, 30, 23, 30, tzinfo=ZoneInfo(TIMEZONE))
    await fire("4031", "12-31")
    clock.moment = datetime(2026, 12, 31, 8, 0, tzinfo=ZoneInfo(TIMEZONE))
    await fire("4031", "12-31")

    assert len(celebrations(guilds.first)) == 1, "the early delivery already celebrated 2026"
    assert stored_year(555) == 2026


async def test_a_birthday_reminder_waits_until_the_bot_is_ready(guilds, deps):
    """A reminder that arrives while the bot boots must not miss its guilds."""
    register(guilds.first, 555, "4001")
    ready = asyncio.Event()
    deps.bot.wait_until_ready = ready.wait

    job = asyncio.create_task(fire("4001"))
    await asyncio.sleep(0.05)
    assert celebrations(guilds.first) == [], "nothing is sent before the bot sees its guilds"

    ready.set()
    await asyncio.wait_for(job, timeout=1)

    assert len(celebrations(guilds.first)) == 1


@pytest.mark.parametrize("break_guild", [refuse_messages, delete_channel],
                         ids=["discord-refuses", "channel-deleted"])
async def test_a_guild_that_fails_does_not_stop_the_next_and_is_counted(
    guilds, analytics_events, caplog, break_guild,
):
    """Both items carry one reminder: before 2026-05-10 there was one per date for every guild."""
    register(guilds.first, 555, "4000")
    register(guilds.second, 777, "4000")
    break_guild(guilds.first)

    with caplog.at_level("INFO"):
        await fire("4000")

    assert len(celebrations(guilds.second)) == 1, "the next guild still celebrates"
    assert "celebrated=1 guilds_failed=1 members_failed=0" in caplog.text
    if break_guild is refuse_messages:
        refused = [event for event in analytics_events
                   if event["event"] == "value.blocked_by_permission"]
        assert [event["guild_id"] for event in refused] == [str(FIRST_GUILD)]


async def test_a_message_discord_fails_to_send_does_not_stop_the_next_member_and_is_counted(
    guilds, caplog,
):
    """A 403 means the whole channel is closed to Keiko; any other error is about one message."""
    register(guilds.first, 555, "4001")
    register(guilds.first, 556, "4001")
    fail_the_first_message(guilds.first, discord.HTTPException(
        SimpleNamespace(status=500, reason="Internal Server Error"), "Discord is down"
    ))

    with caplog.at_level("INFO"):
        await fire("4001")

    assert len(celebrations(guilds.first)) == 1, "the next member still gets their message"
    assert "celebrated=1 guilds_failed=0 members_failed=1" in caplog.text
    errors = errors_in(caplog)
    assert [record.context.user_id for record in errors] == ["555"], (
        "a message Discord failed on is an error with its member, like a drawing error"
    )
    assert errors[0].exc_info is not None


async def test_an_unexpected_error_inside_the_send_is_an_error_not_a_missing_server(
    guilds, caplog,
):
    """A real `KeyError` used to be caught as `LookupError` and read as a missing server."""
    register(guilds.first, 555, "4001")
    guilds.first.text_channels[0]._send.side_effect = KeyError("embed")

    with caplog.at_level("INFO"):
        await fire("4001")

    errors = errors_in(caplog)
    assert len(errors) == 1
    assert errors[0].exc_info is not None
    assert "not found" not in caplog.text


async def test_a_celebration_that_cannot_be_drawn_does_not_spend_the_year(
    guilds, monkeypatch, caplog,
):
    """The member is claimed once the message exists, so a drawing error leaves them for a
    retry, and the next member is still celebrated."""
    register(guilds.first, 555, "4001")
    register(guilds.first, 556, "4001")
    draw = birthday_handler.build_celebration_embed

    def broken_for_one_member(item, *args, **kwargs):
        if item["user_id"] == "555":
            raise ValueError("the custom image is not a link")
        return draw(item, *args, **kwargs)

    monkeypatch.setattr(birthday_handler, "build_celebration_embed", broken_for_one_member)
    with caplog.at_level("INFO"):
        await fire("4001")

    assert len(celebrations(guilds.first)) == 1, "the next member is still celebrated"
    assert "celebrated=1 guilds_failed=0 members_failed=1" in caplog.text

    monkeypatch.setattr(birthday_handler, "build_celebration_embed", draw)
    await fire("4001")

    assert len(celebrations(guilds.first)) == 2, "the member whose drawing failed was not spent"


async def test_a_member_who_moves_their_birthday_after_the_celebration_waits_for_next_year(
    guilds, clock,
):
    """Decided with Lucas: at most one celebration per member, guild and year."""
    register(guilds.first, 555, "4001", mm_dd="05-12")
    await fire("4001", "05-12")

    upsert_birthday_item(str(FIRST_GUILD), "555", "11-20", reminder_id="4009")
    clock.moment = datetime(2026, 11, 20, 8, 0, tzinfo=ZoneInfo(TIMEZONE))
    await fire("4009", "11-20")

    assert len(celebrations(guilds.first)) == 1
