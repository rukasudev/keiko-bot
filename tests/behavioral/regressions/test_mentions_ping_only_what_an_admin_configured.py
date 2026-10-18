"""A message of Keiko's pings only what an admin configured it to ping.

Reported in the v1 review: `DiscordBot` passed no `allowed_mentions`, so Discord parsed
every @everyone, @here and role mention in anything Keiko sent, whoever had typed the
text: a reply built from someone else's words could ping a whole server.

Shared behaviour: the bot's default, which every send inherits, and the sends whose
mentions an admin configured. Exposed by: the review of what can ping.

Guaranteed: by default Keiko pings no @everyone, @here or role (a user mention and the
author of a message it replies to still do); a Twitch or YouTube notice still pings
whatever its template, written by an admin, names; the birthday's @everyone pings
exactly when the server asked for it; the welcome can name its new member and nobody
else. Each check reads what Discord would parse: the bot's default merged with what the
send passed, as discord.py sends it.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock

import discord
import pytest

from app.bot import DiscordBot
from tests.mocks.discord import create_guild, create_member

pytestmark = [pytest.mark.behavioral, pytest.mark.regression]

EVERYTHING = {"parse": ["users", "roles", "everyone"]}


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


def last_send(channel):
    return channel._send.await_args.kwargs


def test_by_default_keiko_pings_no_everyone_here_or_role(default):
    allowed = parsed(default, {})

    assert "everyone" not in allowed["parse"], "@everyone and @here"
    assert "roles" not in allowed["parse"]
    assert "users" in allowed["parse"], "a member Keiko names still hears it"
    assert allowed.get("replied_user", True) is True, "a reply still pings its author"


async def test_a_twitch_notice_still_pings_what_its_template_names(deps, default):
    from app.services.notifications_twitch import handle_send_streamer_notification

    guild = create_guild(id=5001, channels=["lives"])
    deps.bot.get_guild = lambda guild_id: guild if int(guild_id) == guild.id else None
    deps.twitch.add_user("gaules", user_id="123")
    deps.twitch.set_stream_online("gaules")
    deps.mongo_client.guild.notifications_twitch.insert_one({
        "guild_id": str(guild.id),
        "enabled": True,
        "notifications": {"values": [{
            "streamer": {"value": "gaules"},
            "channel": {"value": str(guild.text_channels[0].id)},
            "notification_messages": {"value": "@everyone <@&777> {streamer} is live!"},
        }]},
    })

    await handle_send_streamer_notification("gaules")

    allowed = parsed(default, last_send(guild.text_channels[0]))
    assert {"everyone", "roles", "users"} <= set(allowed["parse"])


async def test_a_youtube_notice_still_pings_what_its_template_names(deps, default):
    from app.services.notifications_youtube_video import VideoAnnouncement, deliver_video

    guild = create_guild(id=5001, channels=["videos"])
    deps.bot.get_guild = lambda guild_id: guild if int(guild_id) == guild.id else None
    announcement = VideoAnnouncement(
        video={"id": "video-1", "snippet": {"title": "A new video"}},
        channel={"title": "PewDiePie"},
        youtuber="pewdiepie",
        followers=[],
    )
    notification = {
        "youtuber": {"value": "pewdiepie"},
        "channel": {"value": str(guild.text_channels[0].id)},
        "notification_messages": {"value": "@everyone {youtuber} posted {video_link}"},
    }

    await deliver_video(announcement, {"guild_id": str(guild.id)}, notification)

    allowed = parsed(default, last_send(guild.text_channels[0]))
    assert {"everyone", "roles", "users"} <= set(allowed["parse"])


@pytest.mark.parametrize("mention_everyone", [True, False], ids=["asked", "not-asked"])
async def test_the_birthday_pings_everyone_exactly_when_the_server_asked(
    deps, default, monkeypatch, mention_everyone
):
    from app.constants import Commands
    from app.data.birthdays import upsert_birthday_config, upsert_birthday_item
    from app.services.moderations import update_moderations_by_guild
    from app.webhooks import birthday_handler

    guild = create_guild(id=5001, channels=["birthdays"])
    create_member(guild, id=555, name="Tester")
    deps.bot.get_guild = lambda guild_id: guild if int(guild_id) == guild.id else None
    monkeypatch.setattr(birthday_handler, "bot", deps.bot)
    upsert_birthday_config(str(guild.id), str(guild.text_channels[0].id), mention_everyone)
    upsert_birthday_item(str(guild.id), "555", "05-12", reminder_id="4001")
    update_moderations_by_guild(str(guild.id), Commands.REMINDERS_BIRTHDAY_KEY, True)

    await birthday_handler.process_birthday_webhook("4001", "05-12")

    allowed = parsed(default, last_send(guild.text_channels[0]))
    assert ("everyone" in allowed["parse"]) is mention_everyone
    assert "roles" not in allowed["parse"]


async def test_the_welcome_can_name_its_new_member_and_nobody_else(
    deps, default, mock_cache, mock_banner
):
    from app.services.welcome_messages import send_welcome_message

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
