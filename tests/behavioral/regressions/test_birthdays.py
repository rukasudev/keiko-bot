"""Birthday reminders (`reminders_birthday`, its panel and the personal `/birthday`):
what the day's celebration posts and to whom, what the settings and member cards draw,
what a panel left open writes after another panel changed the feature, the repair of
the date schedules, and how `/birthday` says in its trace how it ended."""
import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import discord
import pytest

from app import logger as logger_module
from app.commands.birthdays import celebration as birthday_handler
from app.commands.birthdays import service as birthdays_service
from app.commands.birthdays.service import (
    birthday_config,
    find_birthday,
    find_birthdays_by_guild,
    remove_birthday,
    save_birthday_config,
)
from app.core import trace as trace_service
from app.core.servers import set_feature_enabled
from app.core.utils import ml
from app.data import data, wait
from app.forms.features import feature_for
from app.forms.features.feature import CommitContext, ListChanged
from tests.behavioral.flows.birthdays import (
    FORM,
    PERSONAL_GUILD_ID,
    PERSONAL_USER_ID,
    birthday_guild,
    personal_interaction,
    run_birthday_command,
    seed_config,
    type_day,
)
from tests.behavioral.flows.common import GUILD_ID, delete_feature, remove_item
from tests.generators.birthdays import last_celebrated_year, saved_birthday
from tests.generators.events import lifecycle
from tests.generators.moderations import recorded_server
from tests.mocks.discord import create_guild, create_member

pytestmark = [
    pytest.mark.behavioral,
    pytest.mark.regression,
    pytest.mark.shared_contract("birthday_reminders"),
]

FIRST_GUILD = 1001
SECOND_GUILD = 1002
TIMEZONE = "America/Sao_Paulo"
LOCALE = "pt-br"
MESSAGE = "Parabéns, {user}! Hoje, {date}, é o seu dia!"
REPAIR_GUILD = "1532291713580925048"
REPAIR_LIMIT = 3
EARLY = datetime(2000, 1, 1, tzinfo=timezone.utc)


@pytest.fixture
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
def guilds(deps, monkeypatch, clock):
    """Two servers the bot is in, each with its own birthday channel."""
    first = create_guild(id=FIRST_GUILD, name="First", channels=["birthdays"])
    second = create_guild(id=SECOND_GUILD, name="Second", channels=["parties"])
    by_id = {FIRST_GUILD: first, SECOND_GUILD: second}
    for guild_id in by_id:
        recorded_server(deps.mongo_client, guild_id, timezone=TIMEZONE)
    deps.bot.get_guild = lambda guild_id: by_id.get(int(guild_id))
    monkeypatch.setattr(birthday_handler, "bot", deps.bot)
    return SimpleNamespace(first=first, second=second)


@pytest.fixture
def closed_traces():
    """The traces that closed while the test ran, folded with their log lines."""
    trace_service.clear_sinks()
    captured = []
    trace_service.register_sink(captured.append)
    folding = logger_module.TraceFoldingHandler()
    logger_module.logger.addHandler(folding)
    yield captured
    logger_module.logger.removeHandler(folding)
    trace_service.clear_sinks()


@pytest.fixture
def repair_guild(deps):
    """A server with its birthday settings saved and no birthday yet."""
    recorded_server(deps.mongo_client, REPAIR_GUILD, timezone="UTC")
    wait(
        save_birthday_config(REPAIR_GUILD, channel_id="99", notification_time="14:00")
    )
    return REPAIR_GUILD


@pytest.fixture
def created_reminders(monkeypatch):
    """The dates reminders-api was asked to remind, in order."""
    created = []
    monkeypatch.setattr(
        birthdays_service.reminders_service,
        "create_reminder",
        lambda title, mm_dd, **kwargs: created.append(mm_dd) or f"r-{mm_dd}",
    )
    return created


async def register(guild, member_id, reminder_id, mm_dd="05-12", **fields):
    """A member of `guild` with a birthday on `mm_dd` whose date's reminder is
    `reminder_id`, the feature on and posting to the guild's first channel."""
    create_member(guild, id=member_id, name=f"Member {member_id}")
    await save_birthday_config(
        str(guild.id),
        str(guild.text_channels[0].id),
        notification_time="08:00",
    )
    saved_birthday(str(guild.id), str(member_id), mm_dd, reminder_id=reminder_id, **fields)
    await set_feature_enabled(str(guild.id), FORM, True)


async def fire(reminder_id, mm_dd="05-12"):
    """reminders-api delivers the reminder `reminder_id` of the date `mm_dd`."""
    await birthday_handler.process_birthday_webhook(reminder_id, mm_dd)


def celebrations(guild):
    return guild.text_channels[0].get_all_messages()


def stored_year(member_id):
    return last_celebrated_year(str(FIRST_GUILD), str(member_id))


def refuse_messages(guild):
    forbidden = discord.Forbidden(
        SimpleNamespace(status=403, reason="Forbidden"), "Missing Permissions"
    )
    guild.text_channels[0]._send.side_effect = forbidden


async def delete_channel(guild):
    await save_birthday_config(str(guild.id), "999")


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


def errors_in(caplog):
    """The errors the birthday job logged with its context, whatever else was logged."""
    return [
        record for record in caplog.records
        if record.levelno >= logging.ERROR
        and getattr(getattr(record, "context", None), "flow", None) == "birthday_webhook"
    ]


def quoted_lines(scenario):
    """The quoted lines of the card on screen: its message previews."""
    return [
        line for line in scenario.rendered_summary.splitlines() if line.startswith("> ")
    ]


def message_line(scenario):
    lines = [line for line in quoted_lines(scenario) if line.startswith("> Parabéns")]
    assert lines, "the card draws the custom message"
    return lines[-1]


async def panel_of(scenario_factory, guild, user=None):
    return await scenario_factory(locale=LOCALE, guild=guild, user=user).start_command(
        FORM
    )


async def second_admin_panel(scenario_factory, guild):
    """The panel of another admin of the same server, Helper (556)."""
    return await panel_of(
        scenario_factory, guild, create_member(guild, id=556, name="Helper")
    )


async def find_member(panel, name):
    """The member's birthday found with Search: its card, with Edit and Remove."""
    await panel.click("search")
    await panel.select_option(name)


async def add_a_birthday(panel):
    await panel.click("add")
    await panel.select_option("Amiga", target="user")
    await panel.select_option("02", target="month")
    await type_day(panel, "28", LOCALE)
    await panel.click("done")


async def edit_the_settings(panel):
    await panel.click("edit:group:settings")
    await panel.click("section:birthday_config")
    await panel.select_option("12:00", target="notification_time")
    await panel.click("done")


async def edit_a_member(panel):
    await find_member(panel, "Tester")
    await panel.click("edit:reminders_birthday$0")
    await panel.select_option("03", target="month")
    await panel.click("done")


async def remove_a_member(panel):
    await find_member(panel, "Tester")
    await remove_item(panel, "reminders_birthday$0")


def closed_in_place(scenario):
    drawn = [
        event
        for event in scenario.outputs
        if event.get("actor") == "bot"
        and (event.get("components_v2") or event.get("embed"))
    ]
    last = json.dumps(drawn[-1], ensure_ascii=False)
    return ml("texts.notices.closed", locale=LOCALE) in last and '"actionrow"' not in last


def said(scenario, key):
    copy = ml(key, locale=LOCALE)
    return any(copy in (event.get("content") or "") for event in scenario.outputs)


async def saved_members():
    return sorted(item["user_id"] for item in await find_birthdays_by_guild(GUILD_ID))


def unneeded_schedule(guild_id, mm_dd, index):
    wait(data().keiko_schedules.upsert_schedule(
        birthdays_service.birthday_schedule_id(guild_id, mm_dd),
        "birthday",
        None,
        due_at=EARLY + timedelta(days=index),
    ))


def reminder_of(guild_id, mm_dd):
    schedule = wait(data().keiko_schedules.find_schedule(
        birthdays_service.birthday_schedule_id(guild_id, mm_dd)
    ))
    return (schedule or {}).get("reminder_id")


def trace_lines(trace):
    return [line["message"] for line in trace.lines]


async def test_two_guilds_on_one_date_each_get_one_celebration_from_their_own_reminder(
    guilds,
):
    """A birthday reminder celebrates its own birthdays, once a year, guild by guild.

    Reported in the v1 review (3.4), from the production log of 10/05: the same reminder
    ran at 05:45 with one server and at 15:00 with two. Reminders are created per guild
    and date, each at its guild's timezone and hour, but the webhook celebrated the whole
    date in every guild. With K guilds on one date each member got K celebrations (and K
    @everyone), some at another guild's hour; a reminder delivered twice celebrated twice;
    and one guild's 403 stopped every guild after it, because the whole date ran inside
    one `try`.

    Shared behaviour: `process_birthday_webhook`, the job every birthday reminder runs,
    over `guild.members` and `keiko.schedules`, which the form, `/birthday` and the
    reconcile pass also write.
    Exposed by: two servers sharing a date in production.

    Guaranteed: a reminder celebrates only the items that carry its id; a member is
    celebrated at most once per guild per year, the year of the birthday being celebrated
    where the guild is, recorded on the member's own item once the message is drawn and
    just before it is posted (a new field: v0.9.0 neither reads nor erases it); a guild
    Discord refuses, or whose channel is gone, is counted and the next one still
    celebrates; a member whose message cannot be drawn or sent is counted and the next
    member still gets theirs; nothing is sent before the bot can see its guilds; an
    unexpected failure is logged as an error with its context, never as a missing server.

    Found during the PR review: a member whose message could not be drawn was still spent
    for the year and stopped the rest of the guild; any other Discord error on one member
    stopped the rest of that guild; a 12-31 reminder delivered after local midnight spent
    the next year; a reminder arriving while the bot was not ready yet ran against guilds
    it could not see. The final review found that a send Discord failed on was only a
    warning, and that a `KeyError` raised anywhere in a guild was reported as a missing
    server. The closing review found that a delivery arriving a little early (a stale time
    zone on the reminder) counted for the previous year, so the delivery on the day
    celebrated the member again.
    """
    await register(guilds.first, 555, "4001")
    await register(guilds.second, 777, "4002")

    await fire("4001")

    assert len(celebrations(guilds.first)) == 1
    assert celebrations(guilds.second) == [], (
        "the first guild's reminder fires at the first guild's hour"
    )

    await fire("4002")

    assert len(celebrations(guilds.first)) == 1
    assert len(celebrations(guilds.second)) == 1


async def test_the_same_reminder_twice_in_one_year_celebrates_once(guilds):
    await register(guilds.first, 555, "4001")

    await fire("4001")
    await fire("4001")

    assert len(celebrations(guilds.first)) == 1


async def test_an_edit_after_the_celebration_keeps_the_member_celebrated(guilds):
    """v0.9.0 and this version both rewrite the item with `$set`; neither may erase the key."""
    await register(guilds.first, 555, "4001")
    await fire("4001")

    saved_birthday(
        str(FIRST_GUILD),
        "555",
        "05-12",
        message={"mode": "custom", "title": "Hey", "content": "Happy day!"},
    )
    await fire("4001")

    assert len(celebrations(guilds.first)) == 1


async def test_a_new_year_celebrates_again(guilds, deps):
    await register(guilds.first, 555, "4001")
    deps.mongo_client.guild.members.update_one(
        {"_id": f"{FIRST_GUILD}:555"},
        {"$set": {"state.reminders_birthday.last_celebrated_year": 2025}},
    )

    await fire("4001")

    assert len(celebrations(guilds.first)) == 1
    assert stored_year(555) == 2026


async def test_a_new_years_eve_birthday_delivered_after_midnight_counts_for_its_own_year(
    guilds, clock,
):
    """The year is the birthday's, not the clock's: 00:30 on 01-01 still celebrates 12-31."""
    await register(guilds.first, 555, "4031", mm_dd="12-31")

    clock.moment = datetime(2027, 1, 1, 0, 30, tzinfo=ZoneInfo(TIMEZONE))
    await fire("4031", "12-31")
    clock.moment = datetime(2027, 12, 31, 8, 0, tzinfo=ZoneInfo(TIMEZONE))
    await fire("4031", "12-31")

    assert len(celebrations(guilds.first)) == 2, "the late delivery did not spend 2027"
    assert stored_year(555) == 2027


async def test_a_birthday_delivered_early_counts_for_its_own_year(guilds, clock):
    """A reminder carrying a stale time zone can arrive the evening before the day."""
    await register(guilds.first, 555, "4031", mm_dd="12-31")

    clock.moment = datetime(2026, 12, 30, 23, 30, tzinfo=ZoneInfo(TIMEZONE))
    await fire("4031", "12-31")
    clock.moment = datetime(2026, 12, 31, 8, 0, tzinfo=ZoneInfo(TIMEZONE))
    await fire("4031", "12-31")

    assert len(celebrations(guilds.first)) == 1, "the early delivery already celebrated 2026"
    assert stored_year(555) == 2026


async def test_a_member_who_moves_their_birthday_after_the_celebration_waits_for_next_year(
    guilds, clock,
):
    """Decided with Lucas: at most one celebration per member, guild and year."""
    await register(guilds.first, 555, "4001", mm_dd="05-12")
    await fire("4001", "05-12")

    saved_birthday(str(FIRST_GUILD), "555", "11-20", reminder_id="4009")
    clock.moment = datetime(2026, 11, 20, 8, 0, tzinfo=ZoneInfo(TIMEZONE))
    await fire("4009", "11-20")

    assert len(celebrations(guilds.first)) == 1


async def test_a_birthday_reminder_waits_until_the_bot_is_ready(guilds, deps):
    """A reminder that arrives while the bot boots must not miss its guilds."""
    await register(guilds.first, 555, "4001")
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
    await register(guilds.first, 555, "4000")
    await register(guilds.second, 777, "4000")
    broken = break_guild(guilds.first)
    if broken is not None:
        await broken

    with caplog.at_level("INFO"):
        await fire("4000")

    assert len(celebrations(guilds.second)) == 1, "the next guild still celebrates"
    assert "celebrated=1 guilds_failed=1 members_failed=0" in caplog.text
    if break_guild is refuse_messages:
        refused = [event for event in analytics_events
                   if event["type"] == "value.blocked_by_permission"]
        assert [event["guild_id"] for event in refused] == [str(FIRST_GUILD)]


async def test_a_message_discord_fails_to_send_does_not_stop_the_next_member_and_is_counted(
    guilds, caplog,
):
    """A 403 means the whole channel is closed to Keiko; any other error is about one message."""
    await register(guilds.first, 555, "4001")
    await register(guilds.first, 556, "4001")
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


@pytest.mark.parametrize("error", [
    discord.HTTPException(
        SimpleNamespace(status=500, reason="Internal Server Error"), "Discord is down"
    ),
    discord.Forbidden(SimpleNamespace(status=403, reason="Forbidden"), "Missing Permissions"),
], ids=["discord-fails", "discord-refuses"])
async def test_a_message_discord_did_not_post_leaves_the_year_to_the_next_delivery(
    guilds, error,
):
    """A failed post must not spend the member's year.

    Reported in the database plan (F9): the year was recorded just before the send and
    kept when Discord failed it, so the member got no message that year and every later
    delivery of the reminder skipped them as already celebrated.

    Shared behaviour: `celebrate_in_guild`, the claim every birthday post takes.
    Exposed by: reading the webhook for the move to `guild.members`.

    Guaranteed: the claim is still taken before the send, so two deliveries at once post
    once; a send Discord fails or refuses gives the year back, and the next delivery posts.
    """
    await register(guilds.first, 555, "4001")
    fail_the_first_message(guilds.first, error)

    await fire("4001")
    assert celebrations(guilds.first) == []

    await fire("4001")
    assert len(celebrations(guilds.first)) == 1, "the next delivery posts"


async def test_an_unexpected_error_inside_the_send_is_an_error_not_a_missing_server(
    guilds, caplog,
):
    """A real `KeyError` used to be caught as `LookupError` and read as a missing server."""
    await register(guilds.first, 555, "4001")
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
    await register(guilds.first, 555, "4001")
    await register(guilds.first, 556, "4001")
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


async def test_a_member_named_with_braces_is_celebrated_as_they_are_named(guilds):
    """A server or member name with braces in it is printed as typed, not filled in.

    Reported in the v1 review, after wave 1 moved the notices to `fill_placeholders`: the
    birthday message and the welcome message still chained `.replace`, so a value filled in
    first was read as a template by the next one. A member named "{server}" was celebrated
    as the server's name, and a server named "{member_count} fans" welcomed people to "100
    fans". The welcome looked for `{user}` in any capitals but filled only the lowercase one,
    so "Hi {USER}!" mentioned nobody at all.

    Shared behaviour: `fill_placeholders` (`app/core/utils.py`), now behind every
    template an admin writes: the notices, the welcome and the birthday celebration.
    Exposed by: the wave 1 review of the notices' placeholders.

    Guaranteed: every placeholder is filled in one pass, a value is never read as a
    template, and the welcome's mention is added on its own line whenever `{user}` is not
    written in lowercase, like a notice's link.
    """
    await register(
        guilds.first,
        555,
        "4001",
        message={
            "mode": "custom",
            "title": "🎂 {user}!",
            "content": "Party for {user} at {server}",
        },
    )
    guilds.first.get_member(555).display_name = "{server}"

    await fire("4001")

    embed = guilds.first.text_channels[0].get_last_embed()
    assert embed.title == "🎂 {server}!"
    assert embed.description == "Party for <@555> at First"


@pytest.mark.shared_contract("form_engine")
@pytest.mark.parametrize("locale", ["pt-br", "en-us"])
async def test_the_card_draws_and_starts_from_the_message_a_celebration_posts(
    scenario_factory, deps, guilds, locale
):
    """The default message a birthday card shows is the one the bot posts.

    Broke as: the message section of the birthday settings card drew its own default
    from `birthdays/form.yml` ("🎂 Happy Birthday {user}!", "May your day be full of joy
    and treats! 🎉✨"), and its modal started from it, while a celebration posted the
    copy's defaults ("🎂 It's {user}'s birthday!", "Happy birthday {user}! 🎉 Hope your
    day is full of joy and treats! ✨"): an admin who kept the default saw one text and
    the members got another.

    Shared behaviour: the default of a title-content section (`TitleContentSection`,
    drawn by `_title_content_preview` and opened by `_title_content_modal` in
    `app/forms/form/actions/configuration_card.py`) and the celebration's own message
    (`message_source` in `app/commands/birthdays/service.py`). Consumer that exposed it:
    reminders_birthday (the YAML audit of the form fields).

    Guaranteed: while nobody customized it, the settings card draws, and its modal starts
    from, the title and the message a celebration posts, in both languages.
    """
    scenario = await scenario_factory(locale=locale).start(FORM)
    await scenario.confirm()
    scenario.expect_step("birthday_config")
    lines = quoted_lines(scenario)
    title_line = next(line for line in lines if line.startswith("> **"))
    title = title_line[len("> **") : -len("**")]
    message = lines[lines.index(title_line) + 1][len("> ") :]
    await scenario.click("customize:default_message")
    started = [field.default for field in scenario.pending_modal.children]
    recorded_server(deps.mongo_client, FIRST_GUILD, language=locale)
    await register(guilds.first, 555, "4001")

    await fire("4001")

    posted = guilds.first.text_channels[0].get_last_embed()
    member = guilds.first.get_member(555)
    assert title.replace("{user}", member.display_name) == posted.title
    assert message.replace("{user}", member.mention) == posted.description
    assert started == [title, message]


@pytest.mark.shared_contract("form_engine")
async def test_a_new_members_card_reads_the_date_before_done(scenario_factory, deps):
    """A birthday card's message reads the date it will post, before Done.

    What broke: the member card writes a custom message such as "Parabéns, {user}!
    Hoje, {date}, é o seu dia!". The card draws that message with its variables
    filled, but `{date}` was read from the answer `date`, which only the card's
    transform writes, on Done: a new member's card read "Hoje, , é o seu dia!"
    while the month and the day were already picked, and an edit kept showing the
    old date after the admin picked another month.

    Shared behavior affected: `_template_vars` (app/forms/form/actions/
    configuration_card.py), which fills the `{name}` tokens of a card's preview
    lines from its draft, for every card whose `transform` derives a key from
    other keys of the draft.

    Exposed by: reminders_birthday, the member card's custom message (owner's
    round of 2026-11-17).

    Must remain guaranteed: while the card is open, the message preview shows the
    date the transform will save from the month and the day on the draft, and a
    new month or day changes it at once.
    """
    await seed_config(deps)
    scenario = await panel_of(scenario_factory, birthday_guild())
    await scenario.click("add")
    await scenario.select_option("Amiga", target="user")
    await scenario.select_option("02", target="month")
    await type_day(scenario, "28")
    await scenario.click("customize:message")
    await scenario.submit_modal({"Título": "🎂 Dia de festa", "Conteúdo": MESSAGE})

    assert message_line(scenario) == (
        "> Parabéns, <@777>! Hoje, 28 de fevereiro, é o seu dia!"
    )
    await scenario.finish()


@pytest.mark.shared_contract("form_engine")
async def test_an_edited_month_changes_the_date_on_the_card_at_once(
    scenario_factory, deps
):
    await seed_config(deps)
    saved_birthday(
        GUILD_ID, "777", "05-12",
        message={"mode": "custom", "title": "🎂 Dia de festa", "content": MESSAGE},
    )
    scenario = await panel_of(scenario_factory, birthday_guild())
    await find_member(scenario, "Amiga")
    await scenario.click("edit:reminders_birthday$1")
    assert message_line(scenario) == "> Parabéns, <@777>! Hoje, 12 de maio, é o seu dia!"

    await scenario.select_option("06", target="month")

    assert message_line(scenario) == (
        "> Parabéns, <@777>! Hoje, 12 de junho, é o seu dia!"
    )
    await scenario.finish()


@pytest.mark.shared_contract("manager_form")
@pytest.mark.parametrize(
    "change",
    [add_a_birthday, edit_the_settings, edit_a_member, remove_a_member],
    ids=["add", "edit_settings", "edit_member", "remove"],
)
async def test_a_birthday_change_after_the_feature_was_deleted_writes_nothing(
    scenario_factory, deps, change
):
    """A birthday panel left open after the feature was deleted elsewhere saves nothing.

    Broke as: the birthday commits (`BirthdayFeature` in app/commands/birthdays/service.py)
    wrote straight to `guild.members` and `guild.cogs`, never asking whether the feature
    was still saved, while every generic commit starts with `saved_document`. A panel
    opened before another panel deleted the birthdays added a member's birthday (and its
    reminder) to a server with no birthday feature, and an edit of the settings wrote the
    config document back, half a feature nobody turned on.

    Shared behaviour: the stale-commit rule of the manager (`saved_document`, the refusal
    it raises and the `Finalize("closed")` it ends on). Exposed by: the birthday panel,
    the one feature with commits of its own for every change.

    Guaranteed: after the feature is deleted, an Add, an edit of the settings, an edit of
    a member and a removal from a panel opened before write nothing, record nothing, and
    close that panel in place with the closed notice.
    """
    await seed_config(deps, members=("555",))
    guild = birthday_guild()
    panel = await panel_of(scenario_factory, guild)
    other = await second_admin_panel(scenario_factory, guild)

    await delete_feature(other)
    await change(panel)

    assert await birthday_config(GUILD_ID) is None, "the config came back"
    assert await find_birthday(GUILD_ID, "777") is None, "a birthday was added"
    assert await find_birthday(GUILD_ID, "555") is None, "a birthday was written back"
    assert lifecycle(deps.mongo_client, GUILD_ID, FORM) == ["disabled"]
    assert closed_in_place(panel)


@pytest.mark.shared_contract("manager_form")
async def test_a_removal_whose_member_went_meanwhile_removes_nobody(
    scenario_factory, deps
):
    """Removing birthdays removes every one chosen or none.

    What broke: the birthday feature's `commit_remove_item` deleted each chosen
    member as it went, skipping one that was already gone, so when another admin
    removed one of the chosen members after the panel was drawn, the others were
    deleted anyway and the panel never heard the list had changed. Every other
    list feature refuses the whole removal (`ListChanged`) in that case.

    Shared behavior affected: the all-or-nothing contract of a multi-item removal
    (`RemovalConfirmed` in app/forms/form/manager.py, the `ListChanged` refusal
    the adapter turns into the stale notice), here implemented by
    `BirthdayFeature.commit_remove_item` (app/commands/birthdays/service.py)
    on its own collection.

    Exposed by: the review of reminders_birthday (beta.2 corrections, K2): the
    removal the manager hands over names two members and one went meanwhile.

    Must remain guaranteed: when any chosen member is no longer saved, no birthday
    is deleted and the panel says the screen changed (the stale notice); when every
    chosen member is saved, all of them go.
    """
    await seed_config(deps, members=("555", "777"))
    guild = birthday_guild()
    first = await panel_of(scenario_factory, guild)
    second = await second_admin_panel(scenario_factory, guild)
    await find_member(first, "Tester")

    await remove_a_member(second)
    await remove_item(first, "reminders_birthday$0")

    assert await saved_members() == ["777"]
    assert said(first, "texts.notices.stale")


@pytest.mark.shared_contract("manager_form")
@pytest.mark.parametrize(
    "gone, kept",
    [("777", ["555"]), (None, [])],
    ids=["one-went-meanwhile", "all-saved"],
)
async def test_a_removal_of_several_members_goes_whole_or_not_at_all(deps, gone, kept):
    """The panel removes one member at a time; the commit the manager hands a removal
    of several to keeps the same rule: one chosen member gone refuses them all."""
    await save_birthday_config(GUILD_ID, "100")
    saved_birthday(GUILD_ID, "555", "05-12")
    saved_birthday(GUILD_ID, "777", "06-12")
    if gone is not None:
        await remove_birthday(GUILD_ID, gone)
    removal = {
        "indexes": [0, 1],
        "items": [{"user": {"value": "555"}}, {"user": {"value": "777"}}],
    }
    context = CommitContext(
        GUILD_ID, "555", LOCALE, {}, datetime(2026, 11, 6, tzinfo=timezone.utc),
        "slash", "s1",
    )

    if gone is None:
        await feature_for(FORM).commit("remove_item", removal, context)
    else:
        with pytest.raises(ListChanged):
            await feature_for(FORM).commit("remove_item", removal, context)

    assert await saved_members() == kept


def test_a_real_birthday_behind_a_pass_of_unneeded_schedules_gets_its_reminder(
    repair_guild, created_reminders
):
    """A birthday schedule nothing needs is released, so it never hides one that is needed.

    What broke: the reconciliation pass reads at most `limit` birthday schedules without
    a reminder and skipped, without releasing, the ones whose server had no birthday
    configuration or no member on that date. Those came back on every pass, so once a
    pass's worth of them piled up a real birthday behind them never got its reminder.
    A birthday whose save failed after its date was scheduled left one more of them.

    Shared behaviour: `reconcile_missing_reminders` and `upsert_birthday`
    (`app/commands/birthdays/service.py`) over `keiko.schedules`, run by the birthday
    cog's loop and by `make repair-birthdays`. Consumer that exposed it: the review of
    the new database.

    Guaranteed: a pass releases every schedule it reads that nothing needs, a real
    birthday behind more than one pass of them gets its reminder, and a save that fails
    leaves no schedule behind.
    """
    for index in range(REPAIR_LIMIT + 1):
        unneeded_schedule(repair_guild, f"01-{index + 1:02d}", index)
    unneeded_schedule("no-config", "02-02", 0)
    saved_birthday(repair_guild, "7", "12-25", reminder_id=None)
    wait(data().keiko_schedules.set_schedule_due(
        birthdays_service.birthday_schedule_id(repair_guild, "12-25"),
        datetime(2100, 1, 1, tzinfo=timezone.utc),
    ))

    for _ in range(3):
        wait(birthdays_service.reconcile_missing_reminders(limit=REPAIR_LIMIT))

    assert reminder_of(repair_guild, "12-25") == "r-12-25"
    assert created_reminders == ["12-25"]
    assert wait(birthdays_service.birthday_schedules_missing_reminder(limit=50)) == []


def test_a_birthday_whose_save_fails_leaves_no_schedule(
    repair_guild, created_reminders, monkeypatch
):
    async def refuse(*args, **kwargs):
        raise ConnectionError("mongo is down")

    monkeypatch.setattr(data().guild_members, "set_member_option", refuse)

    with pytest.raises(ConnectionError):
        wait(birthdays_service.upsert_birthday(repair_guild, "7", "12-25"))

    assert wait(birthdays_service.find_birthday(repair_guild, "7")) is None
    assert wait(data().keiko_schedules.find_schedule(
        birthdays_service.birthday_schedule_id(repair_guild, "12-25")
    )) is None


async def test_a_registered_birthday_says_so_in_its_trace(
    birthday_command_on, closed_traces
):
    """The personal /birthday says in the log how it ended.

    Reported from the log channel: the command posted a "Command Run" whose only
    line was that it started. Nothing said whether the birthday was registered,
    replaced or refused, and the replacement ran after the command's trace had
    closed, so it left no trace at all.

    Guaranteed: every outcome writes one line naming it, never the date, and sets
    the Result; a refusal names its reason; replacing a saved birthday is a traced
    unit of work of its own; and the trace says whether the person is an admin.
    """
    await run_birthday_command(personal_interaction(), 3, 14)

    trace = closed_traces[-1]
    assert trace.result == "registered"
    assert any("birthday registered" in line for line in trace_lines(trace))
    assert not any("03-14" in line for line in trace_lines(trace)), "a date is personal"


async def test_a_refused_birthday_names_the_reason_not_the_date(deps, closed_traces):
    await run_birthday_command(personal_interaction(), 2, 31)

    trace = closed_traces[-1]
    assert trace.result == "refused"
    assert any("refused: invalid-date" in line for line in trace_lines(trace))


async def test_a_server_without_birthdays_refuses_with_its_reason(deps, closed_traces):
    await run_birthday_command(personal_interaction(), 3, 14)

    trace = closed_traces[-1]
    assert trace.result == "refused"
    assert any(
        "refused: reminders-birthdays-disabled" in line for line in trace_lines(trace)
    )


async def test_replacing_a_birthday_is_its_own_traced_unit_of_work(
    birthday_command_on, closed_traces
):
    await run_birthday_command(personal_interaction(), 3, 14)
    ask = personal_interaction()
    await run_birthday_command(ask, 4, 20)

    asked = closed_traces[-1]
    assert asked.result == "asked"

    view = ask.followup.send.await_args.kwargs["view"]
    await view.confirm.callback(personal_interaction())

    replaced = closed_traces[-1]
    assert replaced is not asked and replaced.result == "replaced"
    assert any("birthday replaced" in line for line in trace_lines(replaced))
    saved = await find_birthday(PERSONAL_GUILD_ID, str(PERSONAL_USER_ID))
    assert saved["date"] == "04-20"


async def test_the_trace_says_whether_the_person_is_an_admin(deps, closed_traces):
    await run_birthday_command(personal_interaction(admin=True), 2, 31)

    assert closed_traces[-1].is_admin is True
