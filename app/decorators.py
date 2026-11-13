import functools
from typing import Any, Callable, Dict

import discord
from discord.app_commands import Command

from app.components.embed import response_embed
from app.core import analytics
from app.core.trace import trace_scope
from app.core.utils import is_guild_admin


def keiko_command(
    *,
    name: str = "",
    description: str = "...",
    nsfw: bool = False,
    auto_locale_strings: bool = True,
    extras: Dict[Any, Any] = None,
):
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(self, interaction: discord.Interaction, *args, **kwargs):
            command_name = (
                interaction.command.qualified_name if interaction.command
                else (name or func.__name__)
            )
            async with trace_scope(
                command_name,
                opening=f"`/{command_name}` started",
                guild_id=interaction.guild_id,
                user_id=interaction.user.id,
                source="slash",
                is_admin=is_guild_admin(interaction.user),
            ) as trace:
                feature = getattr(interaction.command, "_attr", None)
                if feature:
                    trace.feature = feature

                async with analytics.counting_attempt(trace, interaction.guild_id, feature):
                    await func(self, interaction, *args, **kwargs)

        return Command(
            name=name if name != "" else func.__name__,
            description=description,
            callback=wrapper,
            parent=None,
            nsfw=nsfw,
            auto_locale_strings=auto_locale_strings,
            extras=extras,
        )

    return decorator


def keiko_admin_only(func: Callable) -> Callable:
    @functools.wraps(func)
    async def wrapper(self, interaction, *args, **kwargs):
        if not interaction.user.guild_permissions.administrator:
            embed = response_embed("buttons.setup.admin-only", interaction.locale)
            return await interaction.response.send_message(embed=embed, ephemeral=True)
        return await func(self, interaction, *args, **kwargs)
    return wrapper
