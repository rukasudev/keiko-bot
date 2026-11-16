"""Every command, one folder each, in the order Keiko offers and loads them."""

from app.commands import (
    admin,
    birthdays,
    block_links,
    config,
    help,
    report,
    roles,
    setup,
    stream_elements,
    support,
    translate,
    twitch,
    welcome,
    youtube,
)
from app.core.commands import Command

COMMANDS: tuple[Command, ...] = (
    help.COMMAND,
    welcome.COMMAND,
    roles.COMMAND,
    block_links.COMMAND,
    twitch.COMMAND,
    youtube.COMMAND,
    birthdays.COMMAND,
    stream_elements.COMMAND,
    setup.COMMAND,
    report.COMMAND,
    support.COMMAND,
    translate.COMMAND,
    config.COMMAND,
    admin.COMMAND,
)
