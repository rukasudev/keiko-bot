"""The parent slash groups, each holding the subgroups the commands declare under it."""

from typing import Any

from discord import app_commands
from discord.app_commands import locale_str

from app.bot import DiscordBot
from app.core.commands import PARENT_GROUPS, ParentGroup, commands
from app.types.cogs import GroupCog


@app_commands.guild_only()
class Moderations(GroupCog, name=locale_str("moderations", type="groups")):
    """/moderations."""

    group_key: ParentGroup = "moderations"

    def __init__(self, bot: DiscordBot) -> None:
        self.bot = bot
        super().__init__()


@app_commands.guild_only()
class Notifications(GroupCog, name=locale_str("notifications", type="groups")):
    """/notifications."""

    group_key: ParentGroup = "notifications"

    def __init__(self, bot: DiscordBot) -> None:
        self.bot = bot
        super().__init__()


@app_commands.guild_only()
class Integrations(GroupCog, name=locale_str("integrations", type="groups")):
    """/integrations."""

    group_key: ParentGroup = "integrations"

    def __init__(self, bot: DiscordBot) -> None:
        self.bot = bot
        super().__init__()


def _is_subgroup(cog: type[Any]) -> bool:
    return issubclass(cog, app_commands.Group)


def _attach(group: app_commands.Group, subgroup: app_commands.Group) -> None:
    shared = group.get_command(subgroup.name)
    if not isinstance(shared, app_commands.Group):
        group.add_command(subgroup)
        return
    for leaf in subgroup.commands:
        shared.add_command(leaf)


async def setup(bot: DiscordBot) -> None:
    """Add each parent group with the subgroups the commands declare under it, one per
    name, each command adding its leaves to the subgroup of its name, and refuse a
    subgroup whose command names no parent group."""
    parents = {
        parent.group_key: parent
        for parent in (Moderations(bot), Notifications(bot), Integrations(bot))
    }
    for command in commands():
        for cog in command.cogs:
            if not _is_subgroup(cog):
                continue
            parent = parents.get(command.group) if command.group else None
            group = parent.app_command if parent is not None else None
            if group is None:
                raise ValueError(
                    f"{command.key!r} declares the subgroup {cog.__name__} under no "
                    f"parent group: give it a `group` of {list(PARENT_GROUPS)}"
                )
            _attach(group, cog(bot))
    for parent in parents.values():
        await bot.add_cog(parent)
