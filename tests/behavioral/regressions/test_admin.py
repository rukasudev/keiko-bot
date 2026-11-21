"""The operator's `/admin` command: `/admin configs` (Keiko's own operator ids and
presence), `/admin subscriptions` (the reminder and Twitch sync buttons) and the
inspection of a log message (its pages, its commands status and its History)."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest
from discord.app_commands import Choice

import app as app_package
from app.commands.admin import subscriptions
from app.commands.admin.groups.configs import Configs
from app.commands.admin.log_inspection import LogInspectionView
from app.commands.birthdays.service import birthday_schedule_id
from app.commands.config.service import update_status
from app.commands.youtube.service import youtube_renewal_id
from app.config import AppConfig
from app.core import analytics
from app.core.commands import command_for
from app.core.trace import trace_scope
from app.core.utils import format_relative_time, ml
from app.data import data, wait
from app.forms.features.subscriptions import subscription_feature
from app.ui.buttons import HistoryButton
from tests.behavioral.flows.common import seed_history
from tests.generators.events import event
from tests.generators.moderations import recorded_server, seed_cog
from tests.mocks.discord import MockInteraction, create_guild, create_member

pytestmark = [pytest.mark.behavioral, pytest.mark.regression]

OPERATOR = {
    "admin_guild_id": 10,
    "admin_reports_channel_id": 11,
    "admin_logs_channel_id": 12,
    "admin_logs_command_call_id": 13,
    "admin_logs_error_channel_id": 14,
    "admin_logs_files_channel_id": 15,
    "admin_dump_channel_id": 16,
}
BIRTHDAY = "birthday_reminder"
RENEWAL = "youtube_notification"
INSPECTED_GUILD = "777"
LOCALE = "pt-br"


class ReminderList:
    """reminders-api with a fixed list of reminders, recording each delete."""

    def __init__(self, reminders):
        self.reminders = reminders
        self.deleted = []

    def get_reminders(self):
        return {"data": self.reminders}

    def delete_reminder(self, reminder_id):
        self.deleted.append(str(reminder_id))
        return {}


async def booted_config():
    """Keiko's config as a boot reads it from keiko.settings, without the environment."""
    config = AppConfig.__new__(AppConfig)
    await config.load_db_configs(data())
    return config


@pytest.fixture
def keiko(deps):
    """Keiko's own settings saved with its operator ids, and the config it runs on."""
    deps.mongo_client.keiko.settings.insert_one({
        "_id": "keiko",
        "options": {
            "presence": {"status": "online", "activity": 0, "description": "/help"},
            "prefix": "ke!",
            "owner_id": None,
            "operator": dict(OPERATOR),
        },
    })
    deps.mongo_client.keiko.integrations.insert_one(
        {"_id": "notion", "options": {"enabled": False}}
    )
    return SimpleNamespace(config=wait(booted_config()))


@pytest.fixture
def configs(keiko):
    """The `/admin configs` cog on the running Keiko."""
    cog = Configs.__new__(Configs)
    cog.bot = keiko
    return cog


def operator_asking():
    guild = create_guild(id=4242, name="Operator")
    interaction = MockInteraction(user=create_member(guild), guild=guild)
    interaction.command = SimpleNamespace(qualified_name="admin configs")
    return interaction


def keiko_changes(deps):
    return list(deps.mongo_client.events.changes.find({"type": "keiko.settings_changed"}))


async def presence_changed(**kwargs):
    return None


@pytest.fixture
def api(deps, monkeypatch):
    """reminders-api with two birthday reminders of one date in two servers and three
    YouTube renewals, two of them for the same youtuber; one server follows mrbeast."""
    reminders = ReminderList([
        {"id": 1, "title": BIRTHDAY, "notes": "05-12"},
        {"id": 2, "title": BIRTHDAY, "notes": "05-12"},
        {"id": 3, "title": RENEWAL, "notes": "pewdiepie"},
        {"id": 4, "title": RENEWAL, "notes": "mrbeast"},
        {"id": 5, "title": RENEWAL, "notes": "mrbeast"},
    ])
    deps.bot.reminder = reminders
    monkeypatch.setattr(app_package, "bot", deps.bot)
    for guild_id, reminder_id in (("1001", "1"), ("1002", "2")):
        wait(data().keiko_schedules.upsert_schedule(
            birthday_schedule_id(guild_id, "05-12"), "birthday", reminder_id
        ))
    for youtuber, reminder_id in (("pewdiepie", "3"), ("mrbeast", "4")):
        wait(data().keiko_schedules.upsert_schedule(
            youtube_renewal_id(youtuber), "youtube_renewal", reminder_id
        ))
    seed_cog(deps.mongo_client, "notifications_youtube_video", {
        "guild_id": "1001",
        "enabled": True,
        "notifications": {"values": [{"youtuber": {"value": "mrbeast"}}]},
    })
    return reminders


def button_interaction():
    return SimpleNamespace(
        locale="en-US",
        guild_id=None,
        response=SimpleNamespace(defer=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()),
    )


async def press(view, label):
    [button] = [item for item in view.children if item.label == label]
    await button.callback(button_interaction())


def history_asking(guild):
    interaction = MockInteraction(user=create_member(guild), guild=guild, locale="pt-BR")
    interaction.client = SimpleNamespace(user=SimpleNamespace(id=999))
    interaction.edit_original_response = AsyncMock()
    return interaction


async def inspected_history(guild):
    """What the operator reads on the History of the inspection of `guild`."""
    interaction = history_asking(guild)
    await LogInspectionView(None, guild, str(guild.id)).history_callback(interaction)
    view = interaction.get_last_response()["view"]
    return "\n".join(f"{field.name}\n{field.value}" for field in view.embed.fields)


async def test_an_updated_channel_is_the_one_keiko_uses_now_and_after_a_restart(
    keiko, configs
):
    """`/admin configs` changes the channel Keiko uses now and after a restart, and
    shows it.

    Reported in the database PR 1 inventory: `/admin configs update` saved the id as
    text (`"ID" in config` never matched the lower-case keys) and set a lower-case
    attribute nobody reads, so the running Keiko kept logging to the old channel until a
    restart; `/admin configs show` called `bot.config.get_db_configs()`, which does not
    exist, so the operator got no answer.

    Shared behaviour: `update_admin_configs` and `get_admin_configs`
    (`app/commands/admin/service.py`) over keiko.settings (`app/data/keiko/settings.py`),
    and `AppConfig.load_db_configs`.
    Exposed by: the inventory of the writers of `configs.admin`.

    Guaranteed: an updated operator id is a number in the running config at once and in
    the saved settings a restart reads; `show` answers with what is saved and what runs.
    """
    await Configs.update_config.callback(
        configs, operator_asking(), config="admin_logs_channel_id", value="999"
    )

    assert keiko.config.ADMIN_LOGS_CHANNEL_ID == 999
    restarted = await booted_config()
    assert restarted.ADMIN_LOGS_CHANNEL_ID == 999
    saved = await data().keiko_settings.find_keiko_settings()
    assert saved["operator"]["admin_logs_channel_id"] == 999


async def test_an_id_that_is_not_a_number_is_refused_and_nothing_changes(keiko, configs):
    interaction = operator_asking()

    await Configs.update_config.callback(
        configs, interaction, config="admin_logs_channel_id", value="the logs"
    )

    assert keiko.config.ADMIN_LOGS_CHANNEL_ID == 12
    assert "not" in interaction.get_last_response()["content"]


async def test_show_answers_with_the_saved_and_the_running_configs(configs):
    interaction = operator_asking()

    await Configs.show_config.callback(configs, interaction)

    content = interaction.get_last_response()["content"]
    saved, running = content.split("Configs in Keiko")
    assert "admin_logs_channel_id: 12" in saved
    assert "ADMIN_LOGS_CHANNEL_ID: 12" in running


async def test_an_operator_change_is_recorded_as_a_change_of_keiko_naming_no_server(
    configs, deps
):
    """Database PR 2: a change to Keiko's own settings (its presence through /config,
    its operator ids through /admin configs) wrote nothing anyone could read back.

    Guaranteed: each is one `keiko.settings_changed` change, naming no server, with the
    operator who made it as the actor and the keys it changed.
    """
    interaction = operator_asking()

    await Configs.update_config.callback(
        configs, interaction, config="admin_logs_channel_id", value="999"
    )
    await analytics.written()

    [change] = keiko_changes(deps)
    assert change["guild_id"] is None
    assert change["actor"] == {"kind": "user", "id": str(interaction.user.id)}
    assert change["props"] == {
        "group": "operator", "changed_keys": ["admin_logs_channel_id"]
    }


async def test_a_presence_change_is_recorded_as_a_change_of_keiko(keiko, deps):
    bot = SimpleNamespace(activity=None, status=None, change_presence=presence_changed)
    async with trace_scope("config status", guild_id=4242, user_id=77):
        await update_status(bot, Choice(name="idle", value="idle"))
    await analytics.written()

    [change] = keiko_changes(deps)
    assert change["guild_id"] is None
    assert change["actor"] == {"kind": "user", "id": "77"}
    assert change["props"] == {"group": "presence", "changed_keys": ["status"]}


async def test_the_sync_deletes_only_a_renewal_no_server_follows(api):
    """The admin's reminder buttons act only on YouTube renewals.

    Reported in the database plan (F6): `/admin subscriptions reminders` read every
    reminder on the reminders API as a YouTube renewal, its notes as a youtuber. A
    birthday reminder's notes are its date, which no server follows as a youtuber, so
    "Syncronize Reminders" deleted it; and two servers celebrating on one date carry the
    same notes, so "Remove Duplicated Reminders" deleted one of them as a duplicate.
    Either way a server's birthdays on that date stopped being celebrated, with nothing
    in the log.

    Shared behaviour: `get_reminder_subscriptions` (`app/commands/admin/subscriptions.py`)
    and the two buttons it builds there. Exposed by: the inventory of the reminder rows
    for the move to `keiko.schedules`.

    Guaranteed: a reminder is a YouTube renewal only when `keiko.schedules` says so (kind
    `youtube_renewal`), or when it carries the renewal title and no schedule; a birthday
    reminder is never listed, synchronized away or taken for a duplicate; the sync
    deletes a renewal no server follows, with its schedule; the duplicates removed are
    the renewals Keiko does not track, never the one its schedule points at; the Twitch
    sync drops only the streamers no server follows; the three buttons keep their labels.
    """
    await press(await subscriptions.get_reminder_subscriptions(), "Syncronize Reminders")

    assert api.deleted == ["3"], "the birthdays of 05-12 stay scheduled"
    assert await data().keiko_schedules.find_schedule("youtube_renewal:pewdiepie") is None
    birthday = await data().keiko_schedules.find_schedule("birthday:1001:05-12")
    assert birthday["reminder_id"] == "1"


async def test_two_servers_on_one_date_are_not_duplicates(api):
    await press(
        await subscriptions.get_reminder_subscriptions(), "Remove Duplicated Reminders"
    )

    assert api.deleted == ["5"], "only the renewal Keiko does not track goes"


async def test_the_list_names_only_youtube_renewals(api):
    view = await subscriptions.get_reminder_subscriptions()

    names = [field.name for field in view.custom_embed.fields]
    assert names == ["pewdiepie (1)", "mrbeast (2)"]


async def test_the_list_counts_only_the_servers_announcing_while_on(api, deps):
    seed_cog(deps.mongo_client, "notifications_youtube_video", {
        "guild_id": "1002",
        "enabled": False,
        "notifications": {"values": [{"youtuber": {"value": "mrbeast"}}]},
    })

    view = await subscriptions.get_reminder_subscriptions()

    counts = {field.name: field.value for field in view.custom_embed.fields}
    assert counts == {"pewdiepie (1)": "0", "mrbeast (2)": "1"}, "a paused server waits"


async def test_the_twitch_sync_unsubscribes_only_streamers_no_server_follows(monkeypatch):
    unsubscribed = []

    async def unsubscribe(_interaction, notification):
        unsubscribed.append(notification["streamer"]["value"])

    twitch = command_for("notifications_twitch").feature
    monkeypatch.setattr(twitch, "unsubscribe", unsubscribe)
    view = subscriptions.generate_guilds_by_twitch_subscription_view(
        {"gaules": 2, "alanzoka": 0}
    )
    await press(view, "Syncronize Subscriptions")

    assert unsubscribed == ["alanzoka"]


async def test_the_twitch_list_counts_the_servers_announcing_each_streamer(deps):
    seed_cog(deps.mongo_client, "notifications_twitch", {
        "guild_id": "1001",
        "enabled": True,
        "notifications": {"values": [{"streamer": {"value": "gaules"}}]},
    })
    seed_cog(deps.mongo_client, "notifications_twitch", {
        "guild_id": "1002",
        "enabled": False,
        "notifications": {"values": [{"streamer": {"value": "gaules"}}]},
    })
    deps.bot.twitch = SimpleNamespace(
        get_subscriptions=lambda: {"data": [
            {"condition": {"broadcaster_user_id": "1"}},
            {"condition": {"broadcaster_user_id": "2"}},
        ]},
        get_user_info_by_id=lambda user_id: {"login": {"1": "gaules", "2": "alanzoka"}[user_id]},
    )

    view = await subscriptions.get_twitch_subscriptions()

    counts = {field.name: field.value for field in view.custom_embed.fields}
    assert counts == {"gaules": "1", "alanzoka": "0"}


async def test_the_admin_buttons_keep_their_labels():
    reminders = subscriptions.generate_reminders_by_streamer_view(
        subscriptions.ReminderOverview({}, {}, {}, {}, [])
    )
    twitch = subscriptions.generate_guilds_by_twitch_subscription_view({})

    assert [
        (button.label, str(button.emoji), button.style)
        for button in [*reminders.children, *twitch.children]
    ] == [
        ("Syncronize Reminders", "🔄", discord.ButtonStyle.grey),
        ("Remove Duplicated Reminders", "🗑️", discord.ButtonStyle.red),
        ("Syncronize Subscriptions", "🔄", discord.ButtonStyle.grey),
    ]


def test_only_a_command_that_keeps_subscriptions_has_a_subscription_feature():
    """Found in the design review: admin kept copies of YouTube's rules (its storage
    path, its renewal title and schedule kind, its count) to get around the ban on
    importing another command, and reached Twitch's feature by key with an
    `isinstance`. Admin now reads both through their `SubscriptionFeature`, and both
    lists still count only the servers that announce while on, a paused server left
    out."""
    for key in ("notifications_twitch", "notifications_youtube_video"):
        assert subscription_feature(key) is command_for(key).feature

    with pytest.raises(KeyError):
        subscription_feature("welcome_messages")


async def test_paging_back_and_forth_keeps_one_history_button(deps, guild):
    """The log inspection offers one History button, however often the operator pages.

    Broke as: every turn to the guild page added another History button, so paging back
    and forth stacked them under the inspection, and the 25th turn would have overflowed
    the view.

    Shared behaviour: `LogInspectionView.callback` (`app/commands/admin/log_inspection.py`),
    the Next button of the inspect action on every log message.
    Exposed by: typing the log inspection service under mypy strict.

    Guaranteed: the view holds the Next button and, from the guild page on, one History
    button.
    """
    view = LogInspectionView(None, None, INSPECTED_GUILD)

    for _turn in range(4):
        await view.callback(MockInteraction(user=create_member(guild), guild=guild))

    history = [item for item in view.children if isinstance(item, HistoryButton)]
    assert len(history) == 1, f"{len(history)} History buttons after two round trips"


async def test_a_feature_that_is_on_is_dated_from_its_latest_enable(deps, guild):
    """The log inspection dates a feature that is on from the last time it came on.

    Broke as: `_find_last_enabled_event` walked the events newest first, the order
    `find_cog_events_by_guild` reads them in, and kept the last match, so a feature
    paused and unpaused yesterday read as on "since" its first setup months ago.

    Shared behaviour: the commands status of `LogInspectionView`
    (app/commands/admin/log_inspection.py), read by the /admin inspect button of every
    log message, over the per-feature event history every lifecycle commit writes.

    Guaranteed: the status line of a feature that is on names its newest enable or
    unpause, whatever older events the history holds.
    """
    now = datetime.now(timezone.utc)
    unpaused = now - timedelta(days=1)
    deps.mongo_client.events.changes.insert_many([
        event(event_type, guild_id=INSPECTED_GUILD, cog="block_links", ts=moment)
        for event_type, moment in (
            ("feature.enabled", now - timedelta(days=90)),
            ("feature.paused", now - timedelta(days=2)),
            ("feature.unpaused", unpaused),
            ("config.changed", now - timedelta(hours=1)),
        )
    ])
    recorded_server(deps.mongo_client, INSPECTED_GUILD)
    seed_cog(
        deps.mongo_client, "block_links", {"guild_id": INSPECTED_GUILD, "enabled": True}
    )
    interaction = MockInteraction(user=create_member(guild), guild=guild)

    await LogInspectionView(None, None, INSPECTED_GUILD).callback(interaction)

    [status] = [
        field for field in interaction.get_last_embed().fields
        if field.name.startswith("Bot Online")
    ]
    [line] = [line for line in status.value.split("\n") if "block_links" in line]
    assert line == f"✅ block_links ({format_relative_time(unpaused)})", (
        "the inspection dated block links from its first setup, not its latest unpause"
    )


async def test_the_admin_inspection_names_a_change_by_its_feature(deps, guild):
    """Every list of a server's changes names a change by its feature.

    What broke: the History of the admin log inspection, which lists every change of
    a server, named each one by its feature's storage key ("➕ Item adicionado em
    **block_links** por <@555>"), while /setup's History named the same change by the
    feature's name ("🚫 Bloquear Links"). An admin and an operator read the same
    record two ways, and the key is no name anyone sees in Discord.

    Shared behavior affected: `parse_history_data` and the label it gives a change
    (app/core/manager.py), read by the admin inspection
    (app/commands/admin/log_inspection.py) and /setup's History
    (app/commands/setup/service.py), whose half is pinned in
    tests/behavioral/contracts/test_setup_dashboard.py.

    Exposed by: block_links, an item added from its panel.

    Must remain guaranteed: both lists name a change by the name /setup gives its
    feature, one emoji per line (the change's own), the server settings by the title
    of /setup's settings card, and neither shows the key.
    """
    seed_history(deps, "block_links", "added")

    shown = await inspected_history(guild)

    feature = ml("block_links.command.setup.label", locale=LOCALE)
    added = ml("commands.records.history.lines.added", locale=LOCALE)
    assert f"{feature} · {added}" in shown
    assert "block_links" not in shown
    assert "🚫" not in shown, "the feature's emoji would be a second one on the line"


async def test_the_admin_inspection_names_the_server_settings_as_setup_does(
    deps, guild
):
    seed_history(deps, "settings", "edited")

    shown = await inspected_history(guild)

    settings = ml("texts.server_settings", locale=LOCALE)
    edited = ml("commands.records.history.lines.edited", locale=LOCALE)
    assert f"1. {settings} · {edited}" in shown
    assert "settings" not in shown
