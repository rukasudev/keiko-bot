"""A message of Keiko's pings only what an admin configured it to ping.

Reported in the v1 review: `DiscordBot` passed no `allowed_mentions`, so Discord parsed
every @everyone, @here and role mention in anything Keiko sent, whoever had typed the
text: a reply built from someone else's words could ping a whole server. Then the
Twitch and YouTube notices sent with `AllowedMentions.all()`, so an @everyone or a role
typed anywhere in a message pinged, and a notice had no way to ping a role on purpose
(#51). Then, in the review of PR8, a notice set to @here still pinged the whole server
when its message held an @everyone: Discord's `everyone` flag allows both words, and
the admin's text went out as typed.

Shared behaviour: the bot's default, which every send inherits, and the mention a
notice's admin chose (`app/core/mentions.py`), the one place that says what a notice
writes before its text and what Discord may ping in it. Exposed by: the review of what
can ping, and the notices' Mention field (PR8).

Guaranteed: by default Keiko pings no @everyone, @here or role (a user mention and the
author of a message it replies to still do); a Twitch notice, a YouTube notice and a
birthday ping exactly the mention their admin chose (nobody, @everyone, @here or the
roles picked), written in front of the notice; an @everyone, an @here, a role or a
member typed in a notice's message pings nobody, whatever the notice mentions: the
@everyone and @here read as plain text (they share Discord's one `everyone` flag, so
only the chosen one is written live) and the role or member stays drawn as a mention
the allowed mentions leave out; the birthday's own text
sits in its embed, so its content holds the chosen mention alone; the welcome can name
its new member and nobody else.
Each check reads what Discord would parse: the bot's default merged with what the send
passed, as discord.py sends it.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock

import discord
import pytest

from app.bot import DiscordBot
from tests.generators.moderations import recorded_server, seed_cog
from tests.mocks.discord import create_guild, create_member

pytestmark = [pytest.mark.behavioral, pytest.mark.regression]

EVERYTHING = {"parse": ["users", "roles", "everyone"]}
ROLE_ID = 777
TYPED_ROLE = 123456789012345678
TYPED_MEMBER = 234567890123456789
TYPED = f"@everyone @here <@&{TYPED_ROLE}> <@{TYPED_MEMBER}> <@!{TYPED_MEMBER}>"
BROADCASTS = {"none": None, "everyone": "everyone", "here": "here", "roles": None}
AS_SENT = (
    f"@\u200beveryone @\u200bhere <@&{TYPED_ROLE}> <@{TYPED_MEMBER}> <@!{TYPED_MEMBER}>"
)
MENTIONS = [
    pytest.param("none", None, "", {"parse": []}, id="nobody"),
    pytest.param("everyone", None, "@everyone ", {"parse": ["everyone"]}, id="everyone"),
    pytest.param("here", None, "@here ", {"parse": ["everyone"]}, id="here"),
    pytest.param(
        "roles", str(ROLE_ID), f"<@&{ROLE_ID}> ", {"parse": [], "roles": [ROLE_ID]},
        id="a-role",
    ),
]


def bot_config():
    return SimpleNamespace(
        PREFIX="ke!",
        APPLICATION_ID=1,
        OWNER_ID=2,
        STATUS=discord.Status.online,
        ACTIVITY=discord.ActivityType.playing,
        DESCRIPTION="Keiko",
        WEBHOOK_URL="https://keiko.test/v1/webhooks",
        NOTION_TOKEN="token",
        NOTION_DATABASE_ID="database",
        NOTION_ENABLED=False,
        YOUTUBE_API_KEY="key",
        REMINDER_APPLICATION_ID="1",
        REMINDER_API_KEY="api-key",
    )


@pytest.fixture(scope="module")
def default():
    return DiscordBot(bot_config()).allowed_mentions


def parsed(default, sent):
    """What Discord receives as `allowed_mentions` for one send, as discord.py merges it."""
    explicit = sent.get("allowed_mentions")
    chosen = default.merge(explicit) if default and explicit else (explicit or default)
    return chosen.to_dict() if chosen else EVERYTHING


def broadcast(default, sent):
    """The broadcast Discord sends for one message: @everyone, else @here, when the
    word is written live and the send allows the `everyone` flag."""
    if "everyone" not in parsed(default, sent)["parse"]:
        return None
    content = sent.get("content") or ""
    return next((word for word in ("everyone", "here") if f"@{word}" in content), None)


def pings_nobody_typed(default, sent):
    """A role or member typed in the text stays drawn and Discord may ping neither."""
    allowed = parsed(default, sent)
    return (
        "users" not in allowed["parse"]
        and TYPED_MEMBER not in allowed.get("users", [])
        and "roles" not in allowed["parse"]
        and TYPED_ROLE not in allowed.get("roles", [])
    )


def last_send(channel):
    return channel._send.await_args.kwargs


def mention_entries(mention, roles):
    entries = {"mention": {"value": mention, "_raw_value": mention}}
    if roles:
        entries["mention_roles"] = {"value": roles, "style": "role"}
    return entries


def test_by_default_keiko_pings_no_everyone_here_or_role(default):
    allowed = parsed(default, {})

    assert "everyone" not in allowed["parse"], "@everyone and @here"
    assert "roles" not in allowed["parse"]
    assert "users" in allowed["parse"], "a member Keiko names still hears it"
    assert allowed.get("replied_user", True) is True, "a reply still pings its author"


async def _twitch_live(deps, message, **mention):
    from app.commands.twitch.service import handle_send_streamer_notification

    guild = create_guild(id=5001, channels=["lives"])
    deps.bot.get_guild = lambda guild_id: guild if int(guild_id) == guild.id else None
    recorded_server(deps.mongo_client, guild.id)
    deps.twitch.add_user("gaules", user_id="123")
    deps.twitch.set_stream_online("gaules")
    seed_cog(deps.mongo_client, "notifications_twitch", {
        "guild_id": str(guild.id),
        "enabled": True,
        "notifications": {"values": [{
            "streamer": {"value": "gaules"},
            "channel": {"value": str(guild.text_channels[0].id)},
            "notification_messages": {"value": message},
            **mention,
        }]},
    })

    await handle_send_streamer_notification("gaules")
    return last_send(guild.text_channels[0])


@pytest.mark.parametrize("mention, roles, prefix, expected", MENTIONS)
async def test_a_twitch_notice_pings_exactly_the_mention_its_admin_chose(
    deps, default, mention, roles, prefix, expected
):
    sent = await _twitch_live(
        deps, "{streamer} is live! {stream_link}", **mention_entries(mention, roles)
    )

    assert parsed(default, sent) == expected
    assert sent["content"] == f"{prefix}gaules is live! https://www.twitch.tv/gaules"


@pytest.mark.parametrize("mention, roles, prefix, expected", MENTIONS)
async def test_a_mention_typed_in_a_twitch_message_pings_nobody_and_reads_as_text(
    deps, default, mention, roles, prefix, expected
):
    sent = await _twitch_live(
        deps,
        f"{TYPED} {{streamer}} is live! {{stream_link}}",
        **mention_entries(mention, roles),
    )

    assert parsed(default, sent) == expected
    assert broadcast(default, sent) == BROADCASTS[mention]
    assert pings_nobody_typed(default, sent)
    assert sent["content"] == (
        f"{prefix}{AS_SENT} gaules is live! https://www.twitch.tv/gaules"
    )


async def test_a_twitch_notice_saved_without_a_mention_pings_nobody(deps, default):
    sent = await _twitch_live(deps, "@everyone {streamer} is live!")

    assert parsed(default, sent) == {"parse": []}


async def _youtube_video(deps, message, **mention):
    from app.commands.youtube.service import VideoAnnouncement, deliver_video

    guild = create_guild(id=5001, channels=["videos"])
    deps.bot.get_guild = lambda guild_id: guild if int(guild_id) == guild.id else None
    recorded_server(deps.mongo_client, guild.id)
    announcement = VideoAnnouncement(
        video={"id": "video-1", "snippet": {"title": "A new video"}},
        channel={"title": "PewDiePie"},
        youtuber="pewdiepie",
        followers=[],
    )
    notification = {
        "youtuber": {"value": "pewdiepie"},
        "channel": {"value": str(guild.text_channels[0].id)},
        "notification_messages": {"value": message},
        **mention,
    }

    await deliver_video(announcement, {"guild_id": str(guild.id)}, notification)
    return last_send(guild.text_channels[0])


@pytest.mark.parametrize("mention, roles, prefix, expected", MENTIONS)
async def test_a_youtube_notice_pings_exactly_the_mention_its_admin_chose(
    deps, default, mention, roles, prefix, expected
):
    sent = await _youtube_video(
        deps, "{youtuber} posted {video_link}", **mention_entries(mention, roles)
    )

    assert parsed(default, sent) == expected
    assert sent["content"] == (
        f"{prefix}pewdiepie posted https://www.youtube.com/watch?v=video-1"
    )


@pytest.mark.parametrize("mention, roles, prefix, expected", MENTIONS)
async def test_a_mention_typed_in_a_youtube_message_pings_nobody_and_reads_as_text(
    deps, default, mention, roles, prefix, expected
):
    sent = await _youtube_video(
        deps,
        f"{TYPED} {{youtuber}} posted {{video_link}}",
        **mention_entries(mention, roles),
    )

    assert parsed(default, sent) == expected
    assert broadcast(default, sent) == BROADCASTS[mention]
    assert pings_nobody_typed(default, sent)
    assert sent["content"] == (
        f"{prefix}{AS_SENT} pewdiepie posted https://www.youtube.com/watch?v=video-1"
    )


@pytest.mark.parametrize("mention, roles, prefix, expected", MENTIONS)
async def test_the_birthday_pings_exactly_the_mention_the_server_chose(
    deps, default, monkeypatch, mention, roles, prefix, expected
):
    from app.commands.birthdays.service import save_birthday_config
    from app.core.servers import set_feature_enabled
    from tests.generators.birthdays import saved_birthday
    from app.commands.birthdays import celebration as birthday_handler

    guild = create_guild(id=5001, channels=["birthdays"])
    create_member(guild, id=555, name="Tester")
    deps.bot.get_guild = lambda guild_id: guild if int(guild_id) == guild.id else None
    monkeypatch.setattr(birthday_handler, "bot", deps.bot)
    recorded_server(deps.mongo_client, guild.id)
    await save_birthday_config(
        str(guild.id),
        str(guild.text_channels[0].id),
        mention,
        default_message={
            "mode": "custom",
            "title": f"{TYPED} 🎂",
            "content": f"{TYPED} Parabéns, {{user}}!",
        },
        mention_roles=[roles] if roles else (),
    )
    saved_birthday(str(guild.id), "555", "05-12", reminder_id="4001")
    await set_feature_enabled(str(guild.id), "reminders_birthday", True)

    await birthday_handler.process_birthday_webhook("4001", "05-12")

    sent = last_send(guild.text_channels[0])
    assert parsed(default, sent) == expected
    assert sent["content"] == (prefix.strip() or None), (
        "the mentions typed in the birthday's text stay in its embed"
    )


async def test_the_welcome_can_name_its_new_member_and_nobody_else(
    deps, default, mock_cache, mock_banner
):
    from app.commands.welcome.service import send_welcome_message

    guild = create_guild(id=5001, channels=["welcome"])
    member = create_member(guild, id=999, name="Newcomer")
    member._user = MagicMock(id=999)
    mock_cache.return_value = {
        "welcome_messages_channel": {"values": str(guild.text_channels[0].id)},
        "welcome_messages": {"values": "@everyone say hi to {user}!"},
        "welcome_messages_title": "Welcome!",
    }

    await send_welcome_message(member)

    allowed = parsed(default, last_send(guild.text_channels[0]))
    assert allowed["parse"] == [] and allowed.get("users") == [member.id]
