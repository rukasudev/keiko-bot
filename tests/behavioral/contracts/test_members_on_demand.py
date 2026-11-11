"""A member is found on demand, never from a member list held in bulk.

discord.py no longer downloads any server's members at start and keeps only the
members who join while Keiko runs, so a member is found the way
`app.services.members` finds one: discord.py's cache, then the members events
carried in the last `Commands.MEMBERS_RECENT_SECONDS` (at most
`Commands.MEMBERS_RECENT_SIZE`), then Discord's API. Every consumer that read
the full member cache runs here against a server whose gateway cache is empty.

Guaranteed: each consumer finds its member through the recent members or the
API, the API is not asked for a member the recent ones hold, a member who left
reads as None, the recent members stay within their size and their time, and
the default-roles sync reads the server page by page without caching anyone.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from app.constants import Commands
from app.services import members
from tests.mocks.discord import MockRole, create_guild, create_member, create_message

pytestmark = [pytest.mark.behavioral, pytest.mark.shared_contract("members")]


def uncached(guild, member_id=777, name="Amiga", **kwargs):
    """A member Discord has and discord.py never cached."""
    return create_member(guild, id=member_id, name=name, cached=False, **kwargs)


async def test_a_member_nobody_cached_is_asked_once_then_kept():
    guild = create_guild()
    member = uncached(guild)

    assert members.known(guild, member.id) is None
    assert await members.find(guild, member.id) is member
    assert await members.find(guild, str(member.id)) is member

    assert guild.fetched == [member.id], "the second find read the recent members"
    assert guild.members == [], "the API answer never enters discord.py's cache"


async def test_a_member_in_discord_pys_cache_costs_no_request():
    guild = create_guild()
    member = create_member(guild, id=12, name="Bea")

    assert await members.find(guild, 12) is member
    assert guild.fetched == []


async def test_a_member_who_left_reads_as_none():
    guild = create_guild()

    assert await members.find(guild, 404) is None
    assert await members.find(guild, "not an id") is None
    assert guild.fetched == [404]


def test_the_recent_members_keep_the_newest_within_their_size(monkeypatch):
    monkeypatch.setattr(Commands, "MEMBERS_RECENT_SIZE", 2)
    guild = create_guild()
    first, second, third = (uncached(guild, 700 + index) for index in range(3))
    for member in (first, second, third):
        members.remember(member)

    assert members.held() == 2
    assert members.known(guild, first.id) is None
    assert members.known(guild, third.id) is third


async def test_a_recent_member_is_asked_again_once_its_time_is_over(monkeypatch):
    monkeypatch.setattr(Commands, "MEMBERS_RECENT_SECONDS", 0)
    guild = create_guild()
    member = uncached(guild)
    members.remember(member)

    assert members.known(guild, member.id) is None
    assert await members.find(guild, member.id) is member
    assert guild.fetched == [member.id]


async def test_the_events_keep_their_member_at_hand(deps):
    """A message author, a joiner and whoever interacts are what later screens ask for."""
    from app.cogs.events import Events

    guild = create_guild()
    author, joiner, clicker = (uncached(guild, 800 + index) for index in range(3))
    joiner._user = SimpleNamespace(id=joiner.id)
    events = Events(deps.bot)

    await events.on_message(create_message(guild.text_channels[0], author, "oi"))
    await events.on_member_join(joiner)
    await events.on_interaction(
        SimpleNamespace(type=discord.InteractionType.component, user=clicker)
    )

    for member in (author, joiner, clicker):
        assert await members.find(guild, member.id) is member
    assert guild.fetched == []


def test_the_history_names_its_actors_without_a_lookup():
    """The audit history is drawn synchronously: the mention of an id is the same
    `<@id>` a member's mention is, so no member is looked up at all."""
    from app.services.manager import parse_history_data
    from datetime import datetime

    guild = create_guild()
    interaction = SimpleNamespace(
        locale="pt-br", guild=guild, client=SimpleNamespace(user=SimpleNamespace(id=1))
    )
    record = {
        "datetime": datetime(2026, 1, 1, 9, 30),
        "user_id": "777",
        "event": "edited",
        "cog_key": "welcome_messages",
    }

    lines = parse_history_data([record], interaction)

    assert "<@777>" in next(iter(lines.values()))
    assert guild.fetched == []


async def test_the_birthday_preview_draws_a_member_nobody_cached(deps):
    from app.services.reminders_birthdays import send_birthday_preview

    guild = create_guild()
    friend = uncached(guild)
    interaction = MagicMock()
    interaction.guild = guild
    interaction.user = create_member(guild, id=555, name="Tester")
    interaction.locale = "pt-br"
    interaction.followup.send = AsyncMock()

    await send_birthday_preview(interaction, [{"key": "user", "value": "777"}])
    await send_birthday_preview(interaction, [{"key": "user", "value": "777"}])

    embed = interaction.followup.send.await_args.kwargs["embed"]
    assert embed.thumbnail.url == friend.display_avatar.url
    assert guild.fetched == [777], "the second preview read the recent members"


async def test_the_welcome_preview_finds_the_member_behind_a_plain_user(deps):
    from app.services.welcome_messages import send_welcome_message_preview

    guild = create_guild()
    member = uncached(guild, 555, "Tester")
    member._user = SimpleNamespace(id=555)
    interaction = MagicMock()
    interaction.user = SimpleNamespace(id=555)
    interaction.guild = guild
    interaction.locale = "pt-br"
    interaction.followup.send = AsyncMock()

    await send_welcome_message_preview(
        interaction,
        [
            {"key": "welcome_messages_title", "value": "Chegou gente nova!"},
            {"key": "welcome_messages", "value": "Oi {user}!"},
            {"key": "welcome_design", "value": "custom_only"},
            {
                "key": "welcome_custom_image",
                "value": "https://cdn.discordapp.com/attachments/1/2/banner.png",
            },
        ],
    )

    embed = interaction.followup.send.await_args.kwargs["embed"]
    assert "<@!555>" in embed.description
    assert guild.fetched == [555]


async def test_a_picked_member_has_a_picture_without_a_request():
    """The member select hands its members; the card and the found screen draw
    their pictures from them synchronously."""
    from app.settings.discord.callbacks import MemberPictures
    from app.settings.discord.views import UserPicker
    from app.settings.form.components import Picker

    guild = create_guild()
    friend = uncached(guild)
    reported = []

    async def report(_interaction, custom_id, chosen):
        reported.append(chosen)

    picker = UserPicker(
        SimpleNamespace(handle=report),
        "s:1:user",
        Picker("user", "user", "Escolha", ()),
    )
    pictures = MemberPictures(guild)
    assert pictures.get("777") is None, "nobody picked the member yet"

    picker._values = [friend]
    await picker.callback(MagicMock())

    assert reported == [["777"]]
    assert pictures["777"] == friend.display_avatar.url
    assert "777" in list(pictures) and len(pictures) == 1
    assert guild.fetched == []


async def test_a_birthday_of_a_member_nobody_cached_is_celebrated(deps, monkeypatch):
    from app.data.birthdays import upsert_birthday_config, upsert_birthday_item
    from app.services.moderations import update_moderations_by_guild
    from app.webhooks import birthday_handler
    from tests.generators.moderations import recorded_server

    guild = create_guild(id=1001, channels=["birthdays"])
    friend = uncached(guild)
    recorded_server(deps.mongo_client, guild.id)
    deps.bot.get_guild = lambda guild_id: guild if int(guild_id) == guild.id else None
    monkeypatch.setattr(birthday_handler, "bot", deps.bot)
    upsert_birthday_config(str(guild.id), str(guild.text_channels[0].id), False)
    for member_id in ("777", "404"):
        upsert_birthday_item(str(guild.id), member_id, "05-12", reminder_id="4001")
    update_moderations_by_guild(str(guild.id), Commands.REMINDERS_BIRTHDAY_KEY, True)

    await birthday_handler.process_birthday_webhook("4001", "05-12")

    [celebration] = guild.text_channels[0]._sent_messages
    assert celebration.embeds[0].thumbnail.url == friend.display_avatar.url
    assert sorted(guild.fetched) == [404, 777], "one request per uncached birthday"


def _roles_server(**counted):
    """A server whose gateway cache is empty: a member missing the role, one who
    has it, and a bot with the role Discord gave it."""
    guild = create_guild(roles=["Admin", "Moderator", "Member"])
    guild.roles.append(MockRole(id=299, name="Robo", managed=True, bot_id=13))
    uncached(guild, 11, "Ana")
    uncached(guild, 12, "Bea", roles=["Member"])
    uncached(guild, 13, "Robo", bot=True)
    guild.me = create_member(guild, id=1, name="Keiko", roles=["Admin"], bot=True)
    return guild


async def test_the_default_roles_counts_read_the_server_page_by_page(monkeypatch):
    from app.services import default_roles

    monkeypatch.setattr(
        default_roles, "get_available_roles_by_guild", lambda guild: {"Member": "202"}
    )
    guild = _roles_server()
    document = {"default_roles": {"values": ["202"]}}

    assert await default_roles.server_totals(guild) == (2, 2)
    assert await default_roles.count_roles_receivers(document, guild) == (1, 0)
    assert guild.pages_fetched == 2 and [m.id for m in guild.members] == [1]


async def test_past_the_counted_bound_no_count_is_guessed(monkeypatch):
    """A server too big to read for a question is never counted nor estimated:
    a kind that gets a role reads as more than the bound, and no member is asked."""
    from app.services import default_roles

    monkeypatch.setattr(Commands, "DEFAULT_ROLES_COUNTED_MEMBERS_MAX", 1)
    monkeypatch.setattr(
        default_roles, "get_available_roles_by_guild", lambda guild: {"Member": "202"}
    )
    guild = _roles_server()
    document = {"default_roles": {"values": ["202"]}}

    assert await default_roles.server_totals(guild) == (None, None)
    assert await default_roles.count_roles_receivers(document, guild) == (None, 0)
    assert guild.pages_fetched == 0


@pytest.mark.parametrize("locale,label", [("pt-br", "5 mil+"), ("en-us", "5,000+")])
async def test_past_the_counted_bound_the_screens_say_more_than_the_bound(
    monkeypatch, locale, label
):
    """The setup step and the Sync question show the bound as a floor, in the
    admin's language, built from `DEFAULT_ROLES_COUNTED_MEMBERS_MAX`."""
    from app.services import default_roles as service
    from app.settings.features import default_roles

    monkeypatch.setattr(
        service, "get_available_roles_by_guild", lambda guild: {"Member": "202"}
    )
    guild = _roles_server()
    for index in range(Commands.DEFAULT_ROLES_COUNTED_MEMBERS_MAX):
        guild.server_members[10_000 + index] = guild.server_members[11]
    document = {"default_roles": {"values": ["202"]}}

    asked = await default_roles._roles_of(document, locale, guild)
    totals = await default_roles._server_totals(guild, locale)

    assert (asked["members_count"], asked["bots_count"]) == (label, "0")
    assert totals == {"members_count": label, "bots_count": label}
    assert guild.pages_fetched == 0


@pytest.mark.parametrize(
    "bound,locale,label",
    [(10_000, "pt-br", "10 mil+"), (10_000, "en-us", "10,000+"), (5000, "en-us", "5,000+")],
)
def test_the_floor_label_follows_its_number(bound, locale, label):
    from app.services.utils import at_least

    assert at_least(bound, locale) == label


async def test_the_sync_gives_the_roles_page_by_page_and_caches_nobody(
    deps, mock_cache, monkeypatch
):
    from app.services import default_roles

    guild = _roles_server()
    member_role = next(role for role in guild.roles if role.name == "Member")
    monkeypatch.setattr(
        default_roles,
        "get_available_roles_by_guild",
        lambda guild: {"Member": str(member_role.id)},
    )
    mock_cache.return_value = {"default_roles": {"values": [str(member_role.id)]}}
    message = MagicMock(edit=AsyncMock())
    interaction = MagicMock(guild=guild, locale="pt-br")
    interaction.followup.send = AsyncMock(return_value=message)

    await default_roles.set_on_default_roles_sync(interaction)

    ana = guild.server_members[11]
    assert member_role in ana._add_roles.await_args.args
    assert guild.pages_fetched == 1
    assert [m.id for m in guild.members] == [1], "the sync filled no cache"
    assert "Membros: **1**" in message.edit.await_args.kwargs["embed"].description


async def test_a_joiner_gets_the_roles_without_any_request(deps, mock_cache, monkeypatch):
    from app.services import default_roles

    guild = _roles_server()
    member_role = next(role for role in guild.roles if role.name == "Member")
    monkeypatch.setattr(
        default_roles,
        "get_available_roles_by_guild",
        lambda guild: {"Member": str(member_role.id)},
    )
    mock_cache.return_value = {"default_roles": {"values": [str(member_role.id)]}}
    joiner = uncached(guild, 21, "Nova")

    await default_roles.set_on_member_join(joiner)

    assert member_role in joiner._add_roles.await_args.args
    assert guild.pages_fetched == 0 and guild.fetched == []


async def test_a_logged_user_discord_py_never_cached_is_asked_to_the_api(monkeypatch):
    from app.services import log_inspection

    user = SimpleNamespace(id=777, name="Amiga")
    bot = SimpleNamespace(get_user=lambda _id: None, fetch_user=AsyncMock(return_value=user))
    monkeypatch.setattr(log_inspection, "bot", bot)

    assert await log_inspection.get_user(777) is user

    bot.fetch_user.side_effect = discord.NotFound(
        SimpleNamespace(status=404, reason="Not Found"), "Unknown User"
    )
    assert await log_inspection.get_user(404) is None


async def test_an_edit_by_an_exempt_member_nobody_cached_stays(deps, mock_cache):
    """A message fetched from the API names its author as a user, without roles;
    the edit check reads the member behind it, so an exempt role still exempts."""
    from app.services.block_links import check_edited_message

    guild = create_guild(roles=["Admin", "Moderator", "Member"])
    channel = guild.text_channels[0]
    moderator = uncached(guild, 31, "Mod", roles=["Moderator"])
    message = create_message(channel, moderator, "olha https://spam-site.com")
    message.author = SimpleNamespace(id=31, name="Mod", bot=False, mention="<@31>")
    channel.register_message(message)
    mock_cache.return_value = {
        "allowed_roles": {"values": [str(moderator.roles[0].id)]},
        "allowed_chats": {"values": []},
        "allowed_links": {"values": []},
        "answer": "Nada de links por aqui!",
    }
    payload = SimpleNamespace(
        guild_id=guild.id,
        channel_id=channel.id,
        message_id=message.id,
        data={"content": message.content},
    )

    await check_edited_message(SimpleNamespace(get_channel=lambda _id: channel), payload)

    message.assert_not_deleted()
    assert guild.fetched == [31]
