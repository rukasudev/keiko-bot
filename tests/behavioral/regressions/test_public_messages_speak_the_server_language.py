"""What the whole server sees is written in the language its settings saved.

Broke as: before PR15 every public message guessed the server's language from
Discord's community locale on every send (`guild_locale`, `preferred_locale`), the
birthday from the language its setup happened to run in, and the StreamElements
command list's page footer was always English. A server whose admin saved another
language kept reading the guess.

Shared behaviour: `server_language` (`app/core/servers.py`), read off the loop
by every public site: the Twitch notice and its status, the YouTube notice, the
birthday celebration, the StreamElements reply and command list (with the page footer
of `PaginationWithoutInteractionView`), the prefix hint and the greeting. Consumer
that exposed it: the server settings of PR15.

Guaranteed: each public site speaks the saved language when the server's Discord
locale says the other one; the page footer follows the locale its view was built
with; an answer only the clicker sees speaks the saved language too, once the seam
every interaction passes (`interaction_language`) set it, since 2026-11-08 (it kept the
clicker's own language before); nothing in
`app/` guesses a language from `preferred_locale` outside the join's guess.
"""
import re
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import discord
import pytest

from app.constants import Commands
from app.integrations.stream_elements import StreamElementsClient
from app.core.utils import ml
from tests.generators.moderations import recorded_server
from tests.mocks.discord import MockInteraction, create_guild, create_member, create_message

pytestmark = [
    pytest.mark.behavioral,
    pytest.mark.regression,
    pytest.mark.shared_contract("server_settings"),
]

APP = Path(__file__).resolve().parents[3] / "app"
OTHER = {"pt-br": "en-us", "en-us": "pt-br"}
DISCORD_LOCALE = {
    "pt-br": discord.Locale.brazil_portuguese,
    "en-us": discord.Locale.american_english,
}
STREAM_ELEMENTS = "commands.commands.commons.stream-elements-manager.commands"


def opening(key, language):
    """The words a copy starts with, before its first placeholder."""
    return re.split(r"[$\{]", ml(key, language))[0]


@pytest.fixture
def server(deps, mock_cache, monkeypatch):
    """A server whose admin set up StreamElements for `gaules`, Keiko seeing only it."""
    guild = create_guild(id=5001, name="Server", channels=["general"])
    deps.bot.get_guild = lambda guild_id: guild if int(guild_id) == guild.id else None
    deps.twitch.add_user("gaules", user_id="123")

    def configured(guild_id, key, *args, **kwargs):
        if key == Commands.INTEGRATIONS_STREAM_ELEMENTS_COMMANDS_KEY:
            return {"streamer": "gaules", "channel_id": "se-gaules", "enabled": True}
        return {}

    mock_cache.side_effect = configured
    monkeypatch.setattr(
        StreamElementsClient,
        "get_chat_commands",
        staticmethod(lambda channel_id: [{"command": "mouse", "reply": "G Pro", "enabled": True}]),
    )
    channel = guild.text_channels[0]
    channel._fetch_message.side_effect = lambda message_id: next(
        (sent for sent in channel._sent_messages if str(sent.id) == str(message_id)), None
    )
    guild.cache = mock_cache
    return guild


def twitch_notice(guild):
    channel = guild.text_channels[0]
    guild_data = {"guild_id": str(guild.id)}
    notification = {
        "channel": {"value": str(channel.id)},
        "streamer": {"value": "gaules"},
        "notification_messages": {"value": "{streamer} is live!"},
    }
    stream_info = {"title": "LOUD vs FURIA", "game_name": "CS2"}
    user_info = {"login": "gaules", "description": "Gaules"}
    return channel, guild_data, notification, stream_info, user_info


async def twitch_live(deps, guild):
    from app.services.notifications_twitch import announce_live

    channel, *notice = twitch_notice(guild)
    await announce_live(*notice)
    return channel.get_last_embed().fields[0].name, (
        "commands.commands.commons.notifications-fields.twitch.game"
    )


async def twitch_status(deps, guild):
    from app.services.notifications_twitch import announce_live, show_status

    channel, guild_data, notification, stream_info, user_info = twitch_notice(guild)
    await announce_live(guild_data, notification, stream_info, user_info)
    await show_status(guild_data, notification, "gaules", "offline", "2h")
    return channel.get_last_embed().footer.text, (
        "commands.commands.commons.notifications-status.twitch.offline"
    )


async def youtube_video(deps, guild):
    from app.services.notifications_youtube_video import VideoAnnouncement, deliver_video

    channel = guild.text_channels[0]
    announcement = VideoAnnouncement(
        video={"id": "video-1", "snippet": {"title": "A video", "description": "About it"}},
        channel={"title": "PewDiePie"},
        youtuber="pewdiepie",
        followers=[],
    )
    notification = {
        "youtuber": {"value": "pewdiepie"},
        "channel": {"value": str(channel.id)},
        "notification_messages": {"value": "{youtuber} posted {video_link}"},
    }
    await deliver_video(announcement, {"guild_id": str(guild.id)}, notification)
    return channel.get_last_embed().fields[0].name, (
        "commands.commands.commons.notifications-fields.youtube.description"
    )


async def birthday(deps, guild):
    from app.services.reminders_birthdays import save_birthday_config
    from app.core.servers import set_feature_enabled
    from tests.generators.birthdays import saved_birthday
    from app.webhooks.birthday_handler import celebrate_in_guild

    channel = guild.text_channels[0]
    create_member(guild, id=555, name="Tester")
    await save_birthday_config(str(guild.id), str(channel.id), False)
    saved_birthday(str(guild.id), "555", "05-12", reminder_id="4001")
    await set_feature_enabled(str(guild.id), Commands.REMINDERS_BIRTHDAY_KEY, True)

    await celebrate_in_guild(
        str(guild.id), [{"user_id": "555", "date": "05-12"}], "05-12", Counter()
    )
    return channel.get_last_embed().title, "messages.birthday-defaults.title"


async def stream_elements_reply(deps, guild):
    from app.services.stream_elements import check_message

    member = create_member(guild, id=111, name="Asker")
    message = create_message(content="ks!mouse", author=member, channel=guild.text_channels[0])
    await check_message(str(guild.id), message, "ks!")
    embed = message._reply.await_args.kwargs["embed"]
    return embed.footer.text, f"{STREAM_ELEMENTS}.reply-footer"


async def stream_elements_list(deps, guild):
    from app.services.stream_elements import check_message

    member = create_member(guild, id=111, name="Asker")
    message = create_message(
        content="ks!commands", author=member, channel=guild.text_channels[0]
    )
    await check_message(str(guild.id), message, "ks!")
    embed = message._reply.await_args.kwargs["embed"]
    return embed.title, f"{STREAM_ELEMENTS}.embed.title"


async def stream_elements_list_page(deps, guild):
    from app.services.stream_elements import check_message

    member = create_member(guild, id=111, name="Asker")
    message = create_message(
        content="ks!commands", author=member, channel=guild.text_channels[0]
    )
    await check_message(str(guild.id), message, "ks!")
    footer = message._reply.await_args.kwargs["embed"].footer.text
    return footer, "commands.pagination-view.footer"


async def prefix_hint(deps, guild):
    from app.services.help import prefix_hint as hint

    guild.cache.side_effect = lambda *args, **kwargs: {}
    return await hint(deps.bot, guild), "messages.prefix-hint.guild"


async def greeting(deps, guild):
    from app.views.greetings import GreetingsView

    guild.me = SimpleNamespace()
    await GreetingsView().send(guild)
    return guild.text_channels[0].get_last_embed().title, "messages.greetings-message.title"


SITES = [
    twitch_live,
    twitch_status,
    youtube_video,
    birthday,
    stream_elements_reply,
    stream_elements_list,
    stream_elements_list_page,
    prefix_hint,
    greeting,
]


@pytest.mark.parametrize("language", ["pt-br", "en-us"])
@pytest.mark.parametrize("site", SITES, ids=[site.__name__ for site in SITES])
async def test_every_public_site_speaks_the_saved_language_not_the_servers_locale(
    deps, server, site, language
):
    recorded_server(deps.mongo_client, server.id, language=language)
    server.preferred_locale = DISCORD_LOCALE[OTHER[language]]

    text, key = await site(deps, server)

    assert opening(key, language) in text
    assert opening(key, OTHER[language]) not in text


@pytest.mark.parametrize("language", ["pt-br", "en-us"])
def test_the_page_footer_follows_the_locale_its_view_was_built_with(language):
    from app.views.pagination_without_interaction import PaginationWithoutInteractionView

    view = PaginationWithoutInteractionView(
        "Title", "Description", {"!mouse": "G Pro"}, locale=language
    )

    footer = view.create_embed(view.get_current_page_data()).footer.text

    assert footer == f"• {ml('commands.pagination-view.footer', language)} 1 / 1"


async def test_an_answer_only_the_clicker_sees_speaks_the_saved_language(
    scenario_factory, deps, monkeypatch
):
    monkeypatch.setattr(
        StreamElementsClient, "get_chat_commands", staticmethod(lambda channel_id: [])
    )
    scenario = scenario_factory(locale="pt-br")
    recorded_server(deps.mongo_client, scenario.guild.id, language="en-us")
    await scenario.start_manager(
        Commands.INTEGRATIONS_STREAM_ELEMENTS_COMMANDS_KEY,
        {"streamer": "gaules", "channel_id": "se-gaules", "enabled": True},
    )

    await scenario.click("Commands")

    answer = scenario.expect_message(ephemeral=True)
    assert answer["embed"]["title"] == "🎮 StreamElements Commands"


def test_no_module_guesses_a_servers_language_from_its_discord_locale():
    import app.core.utils as utils

    guessing = sorted(
        str(path.relative_to(APP))
        for path in APP.rglob("*.py")
        if "preferred_locale" in path.read_text(encoding="utf-8")
    )

    assert not hasattr(utils, "guild_locale")
    assert [
        str(path.relative_to(APP))
        for path in APP.rglob("*.py")
        if "guild_locale" in path.read_text(encoding="utf-8")
    ] == []
    assert guessing == ["core/servers.py"], "only the join's guess reads it"
