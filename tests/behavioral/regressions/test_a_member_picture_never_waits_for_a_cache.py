"""A member's picture is drawn whether or not any cache still holds the member.

Broke as: once no server's members were downloaded at start, the pictures of
the birthday cards were read from discord.py's cache and the members seen in
the last minutes only. An admin who searched a birthday, went for a coffee and
then pressed Edit got the member card without the member's picture, and the
search screen lost it on the way back. Shared behaviour: `MemberPictures` and
the context the adapter builds for every screen (`Runtime._context`).
Consumer that exposed it: reminders_birthday, the only form with a pictured
member select today. Guaranteed: every member a session's screens picture
(the card's member, the item a search found, a member just chosen) is found
before the screen is drawn, from the API when no cache has it.
"""

import discord
import pytest

from app.constants import Commands
from app.services import members
from tests.behavioral.golden.paths import reminders_birthday as birthdays
from tests.behavioral.harness import locators
from tests.mocks.discord import create_guild, create_member

pytestmark = [pytest.mark.behavioral, pytest.mark.regression]


def pictures(scenario):
    """Every picture the screen in front of the admin draws."""
    found = []
    for item in locators.walk_items(scenario.current_message.view):
        if isinstance(item, discord.ui.MediaGallery):
            found += [str(entry.media.url) for entry in item.items]
        elif isinstance(item, discord.ui.Thumbnail):
            found.append(str(item.media.url))
    return found


async def test_a_birthday_searched_and_edited_shows_the_members_picture(
    scenario_factory, deps, monkeypatch
):
    """The gateway cache is empty and the recent members keep nobody: each
    screen finds the member it pictures before it is drawn."""
    monkeypatch.setattr(Commands, "MEMBERS_RECENT_SECONDS", 0)
    birthdays._seed_config(deps, members=("777",))
    guild = create_guild()
    friend = create_member(guild, id=777, name="Amiga", cached=False)
    scenario = await scenario_factory(locale="pt-br", guild=guild).start_command(
        birthdays.FORM
    )

    await scenario.click("search")
    await scenario.select_option(friend)
    assert friend.display_avatar.url in pictures(scenario), "the item the search found"

    await scenario.click("edit:reminders_birthday$0")
    assert friend.display_avatar.url in pictures(scenario), "the card of the edit"
    await scenario.finish()
