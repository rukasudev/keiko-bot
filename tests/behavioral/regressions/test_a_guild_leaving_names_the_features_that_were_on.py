"""The "Left Guild" message names the features that were on, by the record the bot runs on.

Reported in the v1 review: `on_guild_remove` listed "Active commands" from the old
moderations flags, which wave 1 stopped trusting (`is_feature_on` is the one source of
"on"). A feature saved without the flag (StreamElements) was missing from the line, and a
feature disabled while its flag stayed on was listed. The line is what whoever is on call
reads to know what a server lost.

Shared behaviour: `Events.on_guild_remove`, the listener every server removal runs.
Exposed by: the wave 1 review of every reader of the old flags.

Guaranteed: the line lists exactly the features `is_feature_on` calls on before Keiko
pauses them, and says "none" when nothing was.
"""
import pytest

from app.constants import Commands
from app.constants import LogTypes as logconstants
from tests.behavioral.regressions.test_guild_lifecycle_logging import (  # noqa: F401
    embeds,
    events,
    guild_double,
    log_channel,
    rendered,
)

pytestmark = [pytest.mark.behavioral, pytest.mark.regression]

GUILD_ID = "4242"


def left_line(log_channel):
    left = [
        embed for embed in embeds(log_channel)
        if embed.title == logconstants.EVENT_LEFT_GUILD_TITLE
    ]
    assert len(left) == 1
    return next(
        line for line in rendered(left[0]).splitlines() if "Active commands" in line
    )


async def test_the_left_guild_line_names_what_was_on_by_its_saved_settings(
    deps, events, log_channel
):
    deps.mongo_client.guild.moderations.insert_one({
        "guild_id": GUILD_ID,
        "is_bot_online": True,
        Commands.BLOCK_LINKS_KEY: True,
        Commands.WELCOME_MESSAGES_KEY: False,
    })
    deps.mongo_client.guild[Commands.BLOCK_LINKS_KEY].insert_one(
        {"guild_id": GUILD_ID, "enabled": False}
    )
    deps.mongo_client.guild[Commands.WELCOME_MESSAGES_KEY].insert_one(
        {"guild_id": GUILD_ID, "enabled": True}
    )
    deps.mongo_client.guild[Commands.INTEGRATIONS_STREAM_ELEMENTS_COMMANDS_KEY].insert_one(
        {"guild_id": GUILD_ID, "enabled": True}
    )

    await events.on_guild_remove(guild_double(guild_id=int(GUILD_ID)))

    line = left_line(log_channel)
    assert Commands.WELCOME_MESSAGES_KEY in line, "on without the old flag"
    assert Commands.INTEGRATIONS_STREAM_ELEMENTS_COMMANDS_KEY in line
    assert Commands.BLOCK_LINKS_KEY not in line, "disabled while its old flag stayed on"


async def test_a_guild_with_nothing_on_says_none(deps, events, log_channel):
    deps.mongo_client.guild.moderations.insert_one(
        {"guild_id": GUILD_ID, "is_bot_online": True, Commands.BLOCK_LINKS_KEY: True}
    )

    await events.on_guild_remove(guild_double(guild_id=int(GUILD_ID)))

    assert "Active commands: none" in left_line(log_channel)
