"""A server or member name with braces in it is printed as typed, not filled in.

Reported in the v1 review, after wave 1 moved the notices to `fill_placeholders`: the
birthday message and the welcome message still chained `.replace`, so a value filled in
first was read as a template by the next one. A member named "{server}" was celebrated
as the server's name, and a server named "{member_count} fans" welcomed people to "100
fans". The welcome looked for `{user}` in any capitals but filled only the lowercase one,
so "Hi {USER}!" mentioned nobody at all.

Shared behaviour: `fill_placeholders` (`app/services/utils.py`), now behind every
template an admin writes: the notices, the welcome and the birthday celebration.
Exposed by: the wave 1 review of the notices' placeholders.

Guaranteed: every placeholder is filled in one pass, a value is never read as a
template, and the welcome's mention is added on its own line whenever `{user}` is not
written in lowercase, like a notice's link.
"""
from unittest.mock import MagicMock

import pytest

from app.services.reminders_birthdays import build_celebration_embed
from app.services.utils import parse_welcome_messages
from app.services.welcome_messages import send_welcome_message
from tests.mocks.discord import create_guild, create_member

pytestmark = [pytest.mark.behavioral, pytest.mark.regression]


def newcomer(guild_name="Test Server"):
    guild = create_guild(name=guild_name)
    member = create_member(guild, id=999, name="Newcomer")
    member._user = MagicMock(id=999)
    return member


async def test_a_server_named_with_braces_is_welcomed_as_it_is_named(mock_cache, mock_banner):
    member = newcomer(guild_name="{member_count} fans")
    channel = member.guild.text_channels[0]
    mock_cache.return_value = {
        "welcome_messages_channel": {"values": str(channel.id)},
        "welcome_messages": {"values": "Welcome to {server}, {user}!"},
        "welcome_messages_title": "Welcome!",
    }

    await send_welcome_message(member)

    assert channel.get_last_embed().description == "Welcome to {member_count} fans, <@!999>!"


def test_a_user_placeholder_in_capitals_is_text_and_the_mention_is_still_added():
    welcome = parse_welcome_messages("Hi {USER}!", newcomer())

    assert welcome == "Hi {USER}!\n<@!999>"


def test_a_member_named_with_braces_is_celebrated_as_they_are_named():
    guild = create_guild(name="Test Server")
    member = create_member(guild, id=555, name="tester")
    member.display_name = "{server}"
    item = {"date": "05-12", "message": {"mode": "custom", "title": "🎂 {user}!", "content": "Party for {user} at {server}"}}

    embed = build_celebration_embed(item, member, guild, {}, "en-us")

    assert embed.title == "🎂 {server}!"
    assert embed.description == "Party for <@555> at Test Server"
