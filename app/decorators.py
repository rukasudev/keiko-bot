"""The decorators every slash command of Keiko is built with."""

import functools
from collections.abc import Callable, Coroutine
from typing import Any, Concatenate, ParamSpec, TypeVar

import discord
from discord import app_commands
from discord.app_commands import Command
from discord.ext import commands

from app.core import analytics
from app.core.trace import trace_scope
from app.core.utils import is_guild_admin
from app.ui.embed import response_embed

P = ParamSpec("P")
BindingT = TypeVar("BindingT", bound=app_commands.Group | commands.Cog)
Callback = Callable[
    Concatenate[BindingT, discord.Interaction[Any], P], Coroutine[Any, Any, None]
]


def keiko_command(
    *,
    name: str | app_commands.locale_str = "",
    description: str | app_commands.locale_str = "...",
    nsfw: bool = False,
    auto_locale_strings: bool = True,
    extras: dict[Any, Any] | None = None,
) -> Callable[[Callback[BindingT, P]], Command[BindingT, P, None]]:
    """A slash command traced and counted as one attempt, named `name`."""

    def decorator(
        func: Callback[BindingT, P],
    ) -> Command[BindingT, P, None]:
        async def wrapper(
            self: BindingT,
            interaction: discord.Interaction[Any],
            /,
            *args: P.args,
            **kwargs: P.kwargs,
        ) -> None:
            command_name = (
                interaction.command.qualified_name
                if interaction.command
                else (str(name) or func.__name__)
            )
            async with trace_scope(
                command_name,
                opening=f"`/{command_name}` started",
                guild_id=interaction.guild_id,
                user_id=interaction.user.id,
                source="slash",
                is_admin=is_guild_admin(interaction.user),
                command=command_name,
            ) as trace:
                feature = getattr(interaction.command, "_attr", None)
                if feature:
                    trace.feature = feature

                async with analytics.counting_attempt(
                    trace, interaction.guild_id, feature
                ):
                    await func(self, interaction, *args, **kwargs)

        functools.update_wrapper(wrapper, func)
        return Command(
            name=name if name != "" else func.__name__,
            description=description,
            callback=wrapper,
            parent=None,
            nsfw=nsfw,
            auto_locale_strings=auto_locale_strings,
            extras=extras or {},
        )

    return decorator


def keiko_admin_only(
    func: Callback[BindingT, P],
) -> Callback[BindingT, P]:
    """The command, answered only for a server administrator."""

    async def wrapper(
        self: BindingT,
        interaction: discord.Interaction[Any],
        /,
        *args: P.args,
        **kwargs: P.kwargs,
    ) -> None:
        if not is_guild_admin(interaction.user):
            embed = response_embed("commands.responses.admin_only", interaction.locale)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        await func(self, interaction, *args, **kwargs)

    functools.update_wrapper(wrapper, func)
    return wrapper
