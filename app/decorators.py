import functools
from typing import Any, Callable, Dict

import discord
from discord.app_commands import Command

from app import logger
from app.components.embed import response_embed
from app.constants import LogTypes as logconstants
from app.exceptions import ErrorContext
from app.services.utils import parse_locale, parse_valid_locale


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
            interaction.locale = parse_valid_locale(interaction.locale)
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
            embed = response_embed("buttons.setup.admin-only", parse_locale(interaction.locale))
            return await interaction.response.send_message(embed=embed, ephemeral=True)
        return await func(self, interaction, *args, **kwargs)
    return wrapper


def with_error_context(flow: str) -> Callable:
    """
    Decorator que captura erros com contexto enriquecido.

    Extrai automaticamente guild_id, user_id e channel_id dos argumentos
    quando disponíveis (Member, Message, Interaction).
    """

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            context = ErrorContext(flow=flow)

            for arg in args:
                if hasattr(arg, "guild") and arg.guild:
                    context.guild_id = str(arg.guild.id)

                if hasattr(arg, "id"):
                    if hasattr(arg, "display_avatar"):
                        context.user_id = str(arg.id)
                        context.extra["user_name"] = getattr(arg, "name", None)
                    elif hasattr(arg, "author"):
                        context.user_id = str(arg.author.id)
                        context.extra["user_name"] = arg.author.name
                        if hasattr(arg, "content") and arg.content:
                            context.extra["message_preview"] = arg.content[:200]

                if hasattr(arg, "channel") and arg.channel:
                    context.channel_id = str(arg.channel.id)

            try:
                return await func(*args, **kwargs)
            except Exception as e:
                logger.error(
                    f"Error in {flow}: {type(e).__name__}: {e}",
                    log_type=logconstants.COMMAND_ERROR_TYPE,
                    context=context,
                    exc_info=True,
                )
                raise

        return wrapper

    return decorator
