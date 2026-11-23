"""Every command, one folder each, in the order Keiko offers and loads them."""

from app.commands import (
    admin,
    birthdays,
    block_links,
    config,
    help,
    kick,
    reaction_roles,
    report,
    roles,
    setup,
    stream_elements,
    support,
    translate,
    twitch,
    welcome,
    youtube,
    youtube_live,
)
from app.core.commands import Command

COMMANDS: tuple[Command, ...] = (
    help.COMMAND,
    welcome.COMMAND,
    roles.COMMAND,
    reaction_roles.COMMAND,
    block_links.COMMAND,
    twitch.COMMAND,
    kick.COMMAND,
    youtube.COMMAND,
    birthdays.COMMAND,
    youtube_live.COMMAND,
    stream_elements.COMMAND,
    setup.COMMAND,
    report.COMMAND,
    support.COMMAND,
    translate.COMMAND,
    config.COMMAND,
    admin.COMMAND,
)
