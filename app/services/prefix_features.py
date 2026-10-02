"""The features that answer the configured prefix in chat, and the hint when none does."""

import asyncio
from typing import Any, Dict, List, Optional

import discord

from app.bot import DiscordBot
from app.constants import Commands as commands_constants
from app.services import cache
from app.services.utils import fill, get_command_display_name, ml


def prefix_features() -> List[Dict[str, Any]]:
    """The `/setup` features marked as answering the configured prefix."""
    return [
        spec for spec in commands_constants.SETUP_FEATURES if spec.get("answers_prefix")
    ]


async def any_prefix_feature_on(guild_id: str) -> bool:
    """Whether a feature that answers the prefix is on in the guild, read off the loop."""
    for spec in prefix_features():
        if await asyncio.to_thread(
            cache.get_cog_data_or_populate, guild_id, spec["command_key"]
        ):
            return True
    return False


async def prefix_hint(bot: DiscordBot, guild: Optional[discord.Guild]) -> Optional[str]:
    """What Keiko answers a prefixed message no text command matched, if anything."""
    if guild is None:
        return "\n\n".join(
            ml("messages.prefix-hint.direct", locale)
            for locale in (discord.Locale.brazil_portuguese, discord.Locale.american_english)
        )
    if await any_prefix_feature_on(str(guild.id)):
        return None

    locale = guild.preferred_locale
    activations = "\n".join(
        fill(
            ml("messages.prefix-hint.feature", locale),
            command=get_command_display_name(bot, spec["command_key"], locale),
        )
        for spec in prefix_features()
    )
    return fill(
        ml("messages.prefix-hint.guild", locale),
        prefix=bot.config.PREFIX,
        commands=activations,
    )
